#!/usr/bin/env python3

"""
Jetson AGX Orin -> WebRTC (GStreamer webrtcbin) video sender with WebSocket signaling.

Works with your signaling server message schema:
  { "type": "...", "roomId": "...", "payload": {...} }

Message payload formats used:
  offer/answer:
    payload: { "sdp": "<string>", "sdpType": "offer"|"answer" }
  ice-candidate:
    payload: { "candidate": "<string>", "sdpMLineIndex": <int> }

Install (Jetson):
  sudo apt-get update
  sudo apt-get install -y \
    python3-gi gir1.2-gst-plugins-bad-1.0 \
    gstreamer1.0-tools gstreamer1.0-plugins-base gstreamer1.0-plugins-good \
    gstreamer1.0-plugins-bad gstreamer1.0-plugins-ugly gstreamer1.0-libav

  python3 -m pip install --upgrade websockets

Run:
  python3 orin_webrtc_streamer.py \
    --ws wss://YOUR_AWS_HOST \
    --token YOUR_SHARED_SECRET \
    --room YOUR_ROOM_ID

By default this app is "answerer" (browser sends offer; Orin sends answer).
If you want Orin to create the offer, add: --offerer
"""

import argparse
import asyncio
import json
import logging
import signal
import threading
from typing import Any, Dict, Optional, List

import websockets

import gi
gi.require_version("Gst", "1.0")
gi.require_version("GstSdp", "1.0")
gi.require_version("GstWebRTC", "1.0")
from gi.repository import Gst, GstSdp, GstWebRTC, GLib  # type: ignore


log = logging.getLogger("orin-webrtc")


class SignalingClient:
    def __init__(
        self,
        ws_url: str,
        token: str,
        room_id: str,
        on_message,  # callable(dict) -> None (thread-safe)
        on_ready,    # callable() -> None (thread-safe)
    ):
        self.ws_url = ws_url
        self.token = token
        self.room_id = room_id
        self.on_message = on_message
        self.on_ready = on_ready

        self._thread: Optional[threading.Thread] = None
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._outq: Optional[asyncio.Queue] = None
        self._stop_evt: Optional[asyncio.Event] = None
        self._ws = None

    def start(self):
        if self._thread is not None:
            return
        self._thread = threading.Thread(target=self._run_thread, daemon=True)
        self._thread.start()

    def stop(self):
        if self._loop and self._stop_evt:
            self._loop.call_soon_threadsafe(self._stop_evt.set)

    def send(self, msg: Dict[str, Any]):
        if not self._loop or not self._outq:
            return
        # Thread-safe enqueue
        self._loop.call_soon_threadsafe(self._outq.put_nowait, msg)

    def _run_thread(self):
        self._loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self._loop)
        self._outq = asyncio.Queue()
        self._stop_evt = asyncio.Event()
        try:
            self._loop.run_until_complete(self._main())
        except Exception:
            log.exception("Signaling loop crashed")
        finally:
            try:
                self._loop.stop()
                self._loop.close()
            except Exception:
                pass

    async def _main(self):
        assert self._outq is not None
        assert self._stop_evt is not None

        async with websockets.connect(self.ws_url, ping_interval=20, ping_timeout=20) as ws:
            self._ws = ws
            log.info("WebSocket connected: %s", self.ws_url)

            # 1) auth
            await ws.send(json.dumps({
                "type": "auth",
                "payload": {"token": self.token},
            }))

            # wait auth-ok
            while True:
                raw = await ws.recv()
                data = json.loads(raw)
                if data.get("type") == "auth-ok":
                    log.info("Auth OK")
                    break

            # 2) join
            await ws.send(json.dumps({
                "type": "join",
                "roomId": self.room_id,
                "payload": {},
            }))
            log.info("Joined room: %s", self.room_id)

            # notify app it's safe to start pipeline
            try:
                self.on_ready()
            except Exception:
                log.exception("on_ready failed")

            sender_task = asyncio.create_task(self._sender_loop(ws))
            recv_task = asyncio.create_task(self._recv_loop(ws))
            stop_task = asyncio.create_task(self._stop_evt.wait())

            done, pending = await asyncio.wait(
                [sender_task, recv_task, stop_task],
                return_when=asyncio.FIRST_COMPLETED
            )

            for t in pending:
                t.cancel()

    async def _sender_loop(self, ws):
        assert self._outq is not None
        while True:
            msg = await self._outq.get()
            await ws.send(json.dumps(msg))

    async def _recv_loop(self, ws):
        async for raw in ws:
            try:
                data = json.loads(raw)
            except Exception:
                log.warning("Bad JSON from server: %r", raw)
                continue
            try:
                self.on_message(data)
            except Exception:
                log.exception("on_message failed")


class OrinWebRTCStreamer:
    def __init__(self, args: argparse.Namespace):
        self.args = args

        self.pipeline: Optional[Gst.Element] = None
        self.webrtc: Optional[Gst.Element] = None
        self.mainloop = GLib.MainLoop()

        self._remote_desc_set = False
        self._pending_remote_ice: List[Dict[str, Any]] = []

        self.signaling = SignalingClient(
            ws_url=args.ws,
            token=args.token,
            room_id=args.room,
            on_message=self._on_signaling_message_threadsafe,
            on_ready=self._on_signaling_ready_threadsafe,
        )

    # ---------- thread-safe entry points from signaling thread ----------

    def _on_signaling_ready_threadsafe(self):
        GLib.idle_add(self.start_pipeline)

    def _on_signaling_message_threadsafe(self, msg: Dict[str, Any]):
        GLib.idle_add(self._handle_signaling_message, msg)

    # ---------- GStreamer / WebRTC ----------

    def build_pipeline(self) -> str:
        # Your tested capture + NV encode settings, adapted to WebRTC.
        # H.264 baseline profile=0 for compatibility. (NVIDIA docs list profile=0 as baseline) 
        # insert-sps-pps to repeat SPS/PPS at IDR.
        turn = ""
        if self.args.turn:
            # format expected by webrtcbin: turn://user:pass@host:port?transport=udp  (or turns://...)
            turn = f" turn-server={self.args.turn}"

        pipeline = f"""
            v4l2src device={self.args.device} io-mode=2 !
              video/x-raw,format=YUY2,width={self.args.width},height={self.args.height},framerate={self.args.fps}/1 !
              queue leaky=downstream max-size-buffers=1 !
              nvvidconv !
              video/x-raw(memory:NVMM),format=NV12 !
              queue leaky=downstream max-size-buffers=1 !
              nvv4l2h264enc bitrate={self.args.bitrate} iframeinterval={self.args.iframeinterval} insert-sps-pps=true profile=0 !
              video/x-h264,stream-format=byte-stream,alignment=au !
              h264parse !
              rtph264pay pt=96 config-interval={self.args.config_interval} aggregate-mode=zero-latency !
              application/x-rtp,media=video,encoding-name=H264,payload=96,clock-rate=90000,packetization-mode=1 !
              webrtcbin name=webrtc bundle-policy=max-bundle latency={self.args.webrtc_latency} stun-server={self.args.stun}{turn}
        """

        return " ".join(pipeline.split())

    def start_pipeline(self):
        if self.pipeline is not None:
            return False  # stop idle callback

        pipe_str = self.build_pipeline()
        log.info("GStreamer pipeline: %s", pipe_str)

        self.pipeline = Gst.parse_launch(pipe_str)
        self.webrtc = self.pipeline.get_by_name("webrtc")
        if not self.webrtc:
            raise RuntimeError("Failed to get webrtcbin element")

        # Force sendonly on any created transceiver
        self.webrtc.connect("on-new-transceiver", self._on_new_transceiver)

        # If we are offerer, this is how we start negotiation
        self.webrtc.connect("on-negotiation-needed", self._on_negotiation_needed)

        # Trickle ICE out
        self.webrtc.connect("on-ice-candidate", self._on_ice_candidate)

        # Debug state changes
        self.webrtc.connect("notify::connection-state", self._on_conn_state)
        self.webrtc.connect("notify::ice-connection-state", self._on_ice_state)
        self.webrtc.connect("notify::ice-gathering-state", self._on_ice_gathering)

        bus = self.pipeline.get_bus()
        bus.add_signal_watch()
        bus.connect("message", self._on_bus_message)

        self.pipeline.set_state(Gst.State.PLAYING)
        log.info("Pipeline set to PLAYING")

        return False  # stop idle callback

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

    # ---------- webrtcbin signal handlers ----------

    def _on_new_transceiver(self, element, transceiver):
        try:
            # Ensure we only SEND video (answer should become sendonly if browser offers recvonly)
            transceiver.props.direction = GstWebRTC.WebRTCRTPTransceiverDirection.SENDONLY
            log.info("Transceiver set to SENDONLY")
        except Exception:
            log.exception("Failed to set transceiver direction")

    def _on_negotiation_needed(self, element):
        if not self.args.offerer:
            log.info("Negotiation-needed (ignored; running as answerer)")
            return

        log.info("Negotiation-needed: creating OFFER")
        promise = Gst.Promise.new_with_change_func(self._on_offer_created, element, None)
        element.emit("create-offer", None, promise)

    def _on_offer_created(self, promise: Gst.Promise, element, _user_data):
        reply = promise.get_reply()
        offer = reply.get_value("offer")
        element.emit("set-local-description", offer, None)

        sdp_text = offer.sdp.as_text()
        self.signaling.send({
            "type": "offer",
            "roomId": self.args.room,
            "payload": {"sdp": sdp_text, "sdpType": "offer"},
        })
        log.info("Sent OFFER (%d bytes)", len(sdp_text))

    def _on_answer_created(self, promise: Gst.Promise, element, _user_data):
        reply = promise.get_reply()
        answer = reply.get_value("answer")
        element.emit("set-local-description", answer, None)

        sdp_text = answer.sdp.as_text()
        self.signaling.send({
            "type": "answer",
            "roomId": self.args.room,
            "payload": {"sdp": sdp_text, "sdpType": "answer"},
        })
        log.info("Sent ANSWER (%d bytes)", len(sdp_text))

    def _on_ice_candidate(self, element, mlineindex: int, candidate: str):
        self.signaling.send({
            "type": "ice-candidate",
            "roomId": self.args.room,
            "payload": {"candidate": candidate, "sdpMLineIndex": int(mlineindex)},
        })

    def _on_conn_state(self, *args):
        if self.webrtc:
            log.info("PeerConnection state: %s", self.webrtc.get_property("connection-state").value_nick)

    def _on_ice_state(self, *args):
        if self.webrtc:
            log.info("ICE state: %s", self.webrtc.get_property("ice-connection-state").value_nick)

    def _on_ice_gathering(self, *args):
        if self.webrtc:
            log.info("ICE gathering: %s", self.webrtc.get_property("ice-gathering-state").value_nick)

    # ---------- signaling message handling (runs in GLib thread) ----------

    def _handle_signaling_message(self, msg: Dict[str, Any]):
        mtype = msg.get("type")
        payload = msg.get("payload") or {}

        if not self.webrtc:
            log.warning("Got signaling message before pipeline ready: %s", mtype)
            return False

        if mtype in ("offer", "answer"):
            sdp = payload.get("sdp") or payload.get("description", {}).get("sdp")
            sdp_type = payload.get("sdpType") or payload.get("type") or mtype
            if not sdp:
                log.warning("No SDP in %s payload", mtype)
                return False
            self._set_remote_sdp(str(sdp), str(sdp_type))
            return False

        if mtype == "ice-candidate":
            cand = payload.get("candidate")
            idx = payload.get("sdpMLineIndex", payload.get("mlineindex", payload.get("sdpMLineindex")))
            if cand is None or idx is None:
                log.warning("Bad ICE payload: %s", payload)
                return False
            self._add_remote_ice(int(idx), str(cand))
            return False

        # ignore unknown types
        return False

    def _set_remote_sdp(self, sdp_text: str, sdp_type: str):
        assert self.webrtc is not None

        log.info("Setting remote SDP (%s) (%d bytes)", sdp_type, len(sdp_text))

        res, sdpmsg = GstSdp.sdp_message_new()
        if res != GstSdp.SDPResult.OK:
            raise RuntimeError("Failed to create SDPMessage")

        parse_res = GstSdp.sdp_message_parse_buffer(sdp_text.encode("utf-8"), sdpmsg)
        if parse_res != GstSdp.SDPResult.OK:
            raise RuntimeError(f"Failed to parse SDP: {parse_res}")

        if sdp_type.lower() == "offer":
            desc = GstWebRTC.WebRTCSessionDescription.new(GstWebRTC.WebRTCSDPType.OFFER, sdpmsg)
        else:
            desc = GstWebRTC.WebRTCSessionDescription.new(GstWebRTC.WebRTCSDPType.ANSWER, sdpmsg)

        self.webrtc.emit("set-remote-description", desc, None)
        self._remote_desc_set = True

        # Apply any ICE candidates we buffered before remote SDP arrived
        if self._pending_remote_ice:
            for item in self._pending_remote_ice:
                self.webrtc.emit("add-ice-candidate", item["mline"], item["cand"])
            self._pending_remote_ice.clear()

        # If we received an OFFER, generate an ANSWER
        if sdp_type.lower() == "offer":
            log.info("Remote OFFER received: creating ANSWER")
            promise = Gst.Promise.new_with_change_func(self._on_answer_created, self.webrtc, None)
            self.webrtc.emit("create-answer", None, promise)

    def _add_remote_ice(self, mline: int, cand: str):
        assert self.webrtc is not None

        if not self._remote_desc_set:
            self._pending_remote_ice.append({"mline": mline, "cand": cand})
            return

        self.webrtc.emit("add-ice-candidate", mline, cand)

    # ---------- bus handling ----------

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

    # ---------- app main ----------

    def run(self):
        self.signaling.start()

        def _sigint(*_):
            log.info("Stopping...")
            self.stop()

        signal.signal(signal.SIGINT, _sigint)
        signal.signal(signal.SIGTERM, _sigint)

        try:
            self.mainloop.run()
        finally:
            self.stop()


def parse_args():
    p = argparse.ArgumentParser()
    p.add_argument("--ws", required=True, help="wss://... signaling server")
    p.add_argument("--token", required=True, help="shared secret for auth")
    p.add_argument("--room", required=True, help="roomId to join")

    p.add_argument("--device", default="/dev/video0")
    p.add_argument("--width", type=int, default=1344)
    p.add_argument("--height", type=int, default=376)
    p.add_argument("--fps", type=int, default=100)

    p.add_argument("--bitrate", type=int, default=12_000_000)
    p.add_argument("--iframeinterval", type=int, default=100)

    p.add_argument("--stun", default="stun://stun.l.google.com:19302",
                   help="STUN URI (webrtcbin stun-server=...)")
    p.add_argument("--turn", default="",
                   help="TURN URI (webrtcbin turn-server=...), e.g. turn://user:pass@host:3478?transport=udp")

    p.add_argument("--offerer", action="store_true",
                   help="If set, Orin creates the offer. Otherwise it waits for a remote offer and answers.")
    p.add_argument("--config-interval", type=int, default=1,
                   help="rtph264pay config-interval (seconds). 1 is usually safer for WebRTC than -1.")
    p.add_argument("--webrtc-latency", type=int, default=0,
                   help="webrtcbin latency (ms)")

    p.add_argument("--verbose", action="store_true")
    return p.parse_args()


def main():
    args = parse_args()
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    Gst.init(None)

    app = OrinWebRTCStreamer(args)
    app.run()


if __name__ == "__main__":
    main()
