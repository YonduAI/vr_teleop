#!/usr/bin/env python3
"""
WebRTC H264 Receiver (no browser) using GStreamer webrtcbin + your WebSocket signaling.

- Connects to your signaling server, auths, joins room
- Creates an OFFER (recvonly H264)
- When RTP arrives, depayloads H264 and counts Access Units (frame-ish) to estimate FPS

Run:
  python3 webrtc_receiver_h264.py --ws ws://13.56.253.215:3000 --token supersecret123 --room testroom

If you want TURN:
  python3 webrtc_receiver_h264.py ... --turn turn://user:pass@host:3478?transport=udp
"""

import argparse
import asyncio
import json
import logging
import signal
import threading
import time
from typing import Any, Dict, List, Optional

import gi
import websockets

gi.require_version("Gst", "1.0")
gi.require_version("GstSdp", "1.0")
gi.require_version("GstWebRTC", "1.0")
from gi.repository import GLib, Gst, GstSdp, GstWebRTC  # type: ignore

log = logging.getLogger("webrtc-rx")


# ---------------------- Signaling ----------------------


class SignalingClient:
    def __init__(self, ws_url: str, token: str, room_id: str, on_ready, on_message):
        self.ws_url = ws_url
        self.token = token
        self.room_id = room_id
        self.on_ready = on_ready  # thread-safe callable()
        self.on_message = on_message  # thread-safe callable(dict)

        self._thread: Optional[threading.Thread] = None
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._outq: Optional[asyncio.Queue] = None
        self._stop_evt: Optional[asyncio.Event] = None

    def start(self):
        if self._thread:
            return
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def stop(self):
        if self._loop and self._stop_evt:
            self._loop.call_soon_threadsafe(self._stop_evt.set)

    def send(self, msg: Dict[str, Any]):
        if not self._loop or not self._outq:
            return
        self._loop.call_soon_threadsafe(self._outq.put_nowait, msg)

    def _run(self):
        self._loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self._loop)
        self._outq = asyncio.Queue()
        self._stop_evt = asyncio.Event()
        try:
            self._loop.run_until_complete(self._main())
        except Exception:
            log.exception("Signaling thread crashed")
        finally:
            try:
                self._loop.stop()
                self._loop.close()
            except Exception:
                pass

    async def _main(self):
        assert self._outq is not None
        assert self._stop_evt is not None

        async with websockets.connect(
            self.ws_url, ping_interval=20, ping_timeout=20
        ) as ws:
            log.info("WebSocket connected: %s", self.ws_url)

            # auth
            await ws.send(
                json.dumps({"type": "auth", "payload": {"token": self.token}})
            )
            while True:
                raw = await ws.recv()
                msg = json.loads(raw)
                if msg.get("type") == "auth-ok":
                    log.info("Auth OK")
                    break

            # join
            await ws.send(
                json.dumps({"type": "join", "roomId": self.room_id, "payload": {}})
            )
            log.info("Joined room: %s", self.room_id)

            # notify app
            try:
                self.on_ready()
            except Exception:
                log.exception("on_ready failed")

            sender = asyncio.create_task(self._sender(ws))
            receiver = asyncio.create_task(self._receiver(ws))
            stopper = asyncio.create_task(self._stop_evt.wait())

            done, pending = await asyncio.wait(
                [sender, receiver, stopper], return_when=asyncio.FIRST_COMPLETED
            )
            for t in pending:
                t.cancel()

    async def _sender(self, ws):
        assert self._outq is not None
        while True:
            msg = await self._outq.get()
            await ws.send(json.dumps(msg))

    async def _receiver(self, ws):
        async for raw in ws:
            try:
                msg = json.loads(raw)
            except Exception:
                log.warning("Bad JSON: %r", raw)
                continue
            try:
                self.on_message(msg)
            except Exception:
                log.exception("on_message failed")


# ---------------------- WebRTC Receiver ----------------------


class WebRTCReceiver:
    def __init__(self, args: argparse.Namespace):
        self.args = args
        self.mainloop = GLib.MainLoop()

        self.pipeline: Optional[Gst.Pipeline] = None
        self.webrtc: Optional[Gst.Element] = None

        self._remote_desc_set = False
        self._pending_remote_ice: List[Dict[str, Any]] = []

        # FPS measurement
        self._au_count = 0
        self._last_t = time.time()

        self.signaling = SignalingClient(
            ws_url=args.ws,
            token=args.token,
            room_id=args.room,
            on_ready=self._on_signaling_ready_threadsafe,
            on_message=self._on_signaling_message_threadsafe,
        )

    def _on_signaling_ready_threadsafe(self):
        GLib.idle_add(self.start_pipeline)

    def _on_signaling_message_threadsafe(self, msg: Dict[str, Any]):
        GLib.idle_add(self._handle_signaling_message, msg)

    def _make(self, factory: str, name: Optional[str] = None) -> Gst.Element:
        e = Gst.ElementFactory.make(factory, name)
        if not e:
            raise RuntimeError(f"Failed to create element: {factory}")
        return e

    def build_pipeline(self):
        pipe = Gst.Pipeline.new("webrtc-rx-pipe")
        webrtc = self._make("webrtcbin", "webrtc")

        webrtc.set_property("bundle-policy", "max-bundle")
        if self.args.stun:
            webrtc.set_property("stun-server", self.args.stun)
        if self.args.turn:
            # Prefer add-turn-server API when available
            try:
                webrtc.emit("add-turn-server", self.args.turn)
            except Exception:
                # Fallback: some builds have turn-server property
                if webrtc.find_property("turn-server") is not None:
                    webrtc.set_property("turn-server", self.args.turn)

        # IMPORTANT: add a RECVONLY transceiver + caps before creating offer
        # Choose a dynamic payload type we control since we're the offerer.
        rx_caps = Gst.Caps.from_string(
            "application/x-rtp,media=video,encoding-name=H264,clock-rate=90000,"
            "packetization-mode=(string)1,payload=96"
        )
        webrtc.emit(
            "add-transceiver", GstWebRTC.WebRTCRTPTransceiverDirection.RECVONLY, rx_caps
        )

        pipe.add(webrtc)

        # callbacks
        webrtc.connect("on-negotiation-needed", self._on_negotiation_needed)
        webrtc.connect("on-ice-candidate", self._on_ice_candidate)
        webrtc.connect("pad-added", self._on_incoming_pad)
        webrtc.connect("notify::connection-state", self._on_conn_state)
        webrtc.connect("notify::ice-connection-state", self._on_ice_state)
        webrtc.connect("notify::ice-gathering-state", self._on_ice_gathering)

        bus = pipe.get_bus()
        bus.add_signal_watch()
        bus.connect("message", self._on_bus_message)

        self.pipeline = pipe
        self.webrtc = webrtc

    def start_pipeline(self):
        if self.pipeline is not None:
            return False

        self.build_pipeline()
        assert self.pipeline is not None
        assert self.webrtc is not None

        log.info("Pipeline built; setting PLAYING")
        self.pipeline.set_state(Gst.State.PLAYING)

        # print fps every second
        GLib.timeout_add_seconds(1, self._print_fps)

        return False

    def _print_fps(self):
        now = time.time()
        dt = now - self._last_t
        if dt <= 0:
            return True
        fps = self._au_count / dt
        log.info("Incoming H264 access units: %.1f fps", fps)
        self._au_count = 0
        self._last_t = now
        return True

    def stop(self):
        try:
            self.signaling.stop()
        except Exception:
            pass
        if self.pipeline:
            self.pipeline.set_state(Gst.State.NULL)
            self.pipeline = None
            self.webrtc = None
        try:
            self.mainloop.quit()
        except Exception:
            pass

    # --------- webrtcbin handlers ---------

    def _on_negotiation_needed(self, element):
        log.info("Negotiation-needed: creating OFFER (recvonly H264)")
        promise = Gst.Promise.new_with_change_func(
            self._on_offer_created, element, None
        )
        element.emit("create-offer", None, promise)

    def _on_offer_created(self, promise: Gst.Promise, element, _user_data):
        reply = promise.get_reply()
        offer = reply.get_value("offer")
        element.emit("set-local-description", offer, None)

        sdp_text = offer.sdp.as_text()
        self.signaling.send(
            {
                "type": "offer",
                "roomId": self.args.room,
                "payload": {"sdp": sdp_text, "sdpType": "offer"},
            }
        )
        log.info("Sent OFFER (%d bytes)", len(sdp_text))

    def _on_ice_candidate(self, element, mlineindex: int, candidate: str):
        self.signaling.send(
            {
                "type": "ice-candidate",
                "roomId": self.args.room,
                "payload": {"candidate": candidate, "sdpMLineIndex": int(mlineindex)},
            }
        )

    def _on_incoming_pad(self, element, pad: Gst.Pad):
        caps = pad.get_current_caps() or pad.query_caps(None)
        s = caps.to_string() if caps else ""
        if "application/x-rtp" not in s or "encoding-name=(string)H264" not in s:
            log.info("Incoming pad (ignored): %s", s)
            return

        log.info("Incoming H264 RTP pad: %s", s)

        q = self._make("queue", None)
        depay = self._make("rtph264depay", None)
        parse = self._make("h264parse", None)

        # Counter tap (this is what your prints use)
        counter = self._make("identity", None)
        counter.set_property("signal-handoffs", True)
        counter.connect("handoff", self._on_h264_handoff)

        dec = self._make("avdec_h264", None)
        conv = self._make("videoconvert", None)

        sink = self._make("fpsdisplaysink", None)
        sink.set_property("sync", False)
        sink.set_property("text-overlay", True)

        assert self.pipeline is not None
        for e in [q, depay, parse, counter, dec, conv, sink]:
            self.pipeline.add(e)
            e.sync_state_with_parent()

        if not (
            q.link(depay)
            and depay.link(parse)
            and parse.link(counter)
            and counter.link(dec)
            and dec.link(conv)
            and conv.link(sink)
        ):
            log.error("Failed to link display chain")
            return

        sinkpad = q.get_static_pad("sink")
        if pad.link(sinkpad) != Gst.PadLinkReturn.OK:
            log.error("Failed to link webrtc src pad -> display chain")

    def _on_h264_handoff(self, identity, buffer):
        self._au_count += 1

    def _on_conn_state(self, *args):
        if self.webrtc:
            log.info(
                "PeerConnection state: %s",
                self.webrtc.get_property("connection-state").value_nick,
            )

    def _on_ice_state(self, *args):
        if self.webrtc:
            log.info(
                "ICE state: %s",
                self.webrtc.get_property("ice-connection-state").value_nick,
            )

    def _on_ice_gathering(self, *args):
        if self.webrtc:
            log.info(
                "ICE gathering: %s",
                self.webrtc.get_property("ice-gathering-state").value_nick,
            )

    # --------- signaling handling (GLib thread) ---------

    def _handle_signaling_message(self, msg: Dict[str, Any]):
        mtype = msg.get("type")
        payload = msg.get("payload") or {}

        if not self.webrtc:
            return False

        if mtype == "answer":
            sdp = payload.get("sdp")
            if sdp:
                self._set_remote_sdp(str(sdp), "answer")
            return False

        if mtype == "ice-candidate":
            cand = payload.get("candidate")
            idx = payload.get("sdpMLineIndex")
            if cand is not None and idx is not None:
                self._add_remote_ice(int(idx), str(cand))
            return False

        # ignore offers (this receiver is offerer)
        return False

    def _set_remote_sdp(self, sdp_text: str, sdp_type: str):
        assert self.webrtc is not None

        res, sdpmsg = GstSdp.sdp_message_new()
        if res != GstSdp.SDPResult.OK:
            raise RuntimeError("Failed to create SDPMessage")

        parse_res = GstSdp.sdp_message_parse_buffer(sdp_text.encode("utf-8"), sdpmsg)
        if parse_res != GstSdp.SDPResult.OK:
            raise RuntimeError(f"Failed to parse SDP: {parse_res}")

        desc = GstWebRTC.WebRTCSessionDescription.new(
            GstWebRTC.WebRTCSDPType.ANSWER, sdpmsg
        )
        self.webrtc.emit("set-remote-description", desc, None)
        self._remote_desc_set = True
        log.info("Set remote ANSWER")

        for item in self._pending_remote_ice:
            self.webrtc.emit("add-ice-candidate", item["mline"], item["cand"])
        self._pending_remote_ice.clear()

    def _add_remote_ice(self, mline: int, cand: str):
        assert self.webrtc is not None
        if not self._remote_desc_set:
            self._pending_remote_ice.append({"mline": mline, "cand": cand})
            return
        self.webrtc.emit("add-ice-candidate", mline, cand)

    # --------- bus ---------

    def _on_bus_message(self, bus: Gst.Bus, msg: Gst.Message):
        t = msg.type
        if t == Gst.MessageType.ERROR:
            err, dbg = msg.parse_error()
            log.error("GStreamer ERROR: %s (%s)", err, dbg)
            self.stop()
        elif t == Gst.MessageType.EOS:
            log.warning("GStreamer EOS")
            self.stop()
        return True

    def run(self):
        self.signaling.start()

        def _sig(*_):
            log.info("Stopping...")
            self.stop()

        signal.signal(signal.SIGINT, _sig)
        signal.signal(signal.SIGTERM, _sig)

        try:
            self.mainloop.run()
        finally:
            self.stop()


def parse_args():
    p = argparse.ArgumentParser()
    p.add_argument("--ws", required=True)
    p.add_argument("--token", required=True)
    p.add_argument("--room", required=True)
    p.add_argument("--stun", default="stun://stun.l.google.com:19302")
    p.add_argument(
        "--turn", default="", help="turn(s)://user:pass@host:port?transport=udp"
    )
    p.add_argument("--verbose", action="store_true")
    return p.parse_args()


def main():
    args = parse_args()
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    Gst.init(None)
    WebRTCReceiver(args).run()


if __name__ == "__main__":
    main()
