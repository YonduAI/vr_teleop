#!/usr/bin/env python3
"""
robot_webrtc_lib.py

Robot-side library:
- WebSocket signaling (auth/join) to your AWS server.js
- WebRTC H264 send-only video via GStreamer webrtcbin (answerer by default)
- Receive control JSON messages (type="control") and expose via a queue for your control loop

Message conventions (matches your server.js):
- client -> {type:"auth", payload:{token}}
- server -> {type:"auth-ok", payload:{clientId}}
- client -> {type:"join", roomId, payload:{role:"robot", name:"orin"}}

WebRTC signaling:
- offer/answer/ice-candidate are forwarded by the server.
Control plane:
- control / control-ack / telemetry are routed by server (usually to role=robot)
"""

import argparse
import asyncio
import json
import logging
import signal
import threading
import time
from dataclasses import dataclass
from queue import Queue, Empty
from typing import Any, Callable, Dict, Optional, List

import websockets

import gi
gi.require_version("Gst", "1.0")
gi.require_version("GstSdp", "1.0")
gi.require_version("GstWebRTC", "1.0")
from gi.repository import Gst, GstSdp, GstWebRTC, GLib  # type: ignore


log = logging.getLogger("robot-webrtc")

@dataclass
class RobotWebRTCConfig:
    ws_url: str
    token: str
    room: str

    # identity in room (used by your server.js)
    role: str = "robot"
    name: str = "orin"

    # video
    enable_video: bool = True
    offerer: bool = False  # usually False: viewer sends OFFER, robot answers
    device: str = "/dev/video0"
    width: int = 1344
    height: int = 376
    capture_fps: int = 100
    send_fps: int = 100  # set to 50 if you want browser-friendlier streaming
    bitrate: int = 12_000_000
    iframeinterval: int = 100
    config_interval: int = 1
    mtu: int = 1200
    webrtc_latency_ms: int = 0
    stun: str = "stun://stun.l.google.com:19302"
    turn: str = ""  # turn://user:pass@host:3478?transport=udp (optional)


def _link_many(*elements: Gst.Element) -> None:
    for a, b in zip(elements, elements[1:]):
        if not a.link(b):
            raise RuntimeError(f"Failed to link {a.get_name()} -> {b.get_name()}")


class _SignalingClient:
    """
    Runs in its own thread with an asyncio loop.
    Reconnects automatically.
    """
    def __init__(
        self,
        cfg: RobotWebRTCConfig,
        on_ready: Callable[[], None],
        on_message: Callable[[Dict[str, Any]], None],
    ):
        self.cfg = cfg
        self.on_ready = on_ready
        self.on_message = on_message

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

        backoff = 1.0
        while not self._stop_evt.is_set():
            try:
                await self._connect_once()
                backoff = 1.0
            except asyncio.CancelledError:
                return
            except Exception as e:
                log.warning("Signaling connection failed: %s", e)
                await asyncio.sleep(backoff)
                backoff = min(10.0, backoff * 1.5)

    async def _connect_once(self):
        assert self._outq is not None
        assert self._stop_evt is not None

        async with websockets.connect(self.cfg.ws_url, ping_interval=20, ping_timeout=20) as ws:
            log.info("WebSocket connected: %s", self.cfg.ws_url)

            # auth
            await ws.send(json.dumps({"type": "auth", "payload": {"token": self.cfg.token}}))

            while True:
                raw = await ws.recv()
                msg = json.loads(raw)
                if msg.get("type") == "auth-ok":
                    log.info("Auth OK")
                    break

            # join with role/name
            await ws.send(json.dumps({
                "type": "join",
                "roomId": self.cfg.room,
                "payload": {"role": self.cfg.role, "name": self.cfg.name},
            }))
            log.info("Joined room: %s (role=%s name=%s)", self.cfg.room, self.cfg.role, self.cfg.name)

            # tell app it's ready (start pipeline etc.)
            try:
                self.on_ready()
            except Exception:
                log.exception("on_ready failed")

            sender = asyncio.create_task(self._sender_loop(ws))
            receiver = asyncio.create_task(self._recv_loop(ws))
            stopper = asyncio.create_task(self._stop_evt.wait())

            done, pending = await asyncio.wait(
                [sender, receiver, stopper],
                return_when=asyncio.FIRST_COMPLETED,
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
                msg = json.loads(raw)
            except Exception:
                continue
            try:
                self.on_message(msg)
            except Exception:
                log.exception("on_message failed")


class RobotWebRTCNode:
    """
    The main "library" object you embed in your robot code.

    - start(): non-blocking, spins internal GLib + signaling threads
    - stop(): stop everything
    - get_control(): poll for control messages (recommended for robot control loops)
    - send_telemetry()/send_control_ack(): optional outbound messages
    """
    def __init__(self, cfg: RobotWebRTCConfig):
        self.cfg = cfg

        self._glib_loop = GLib.MainLoop()
        self._glib_thread: Optional[threading.Thread] = None

        self._pipeline: Optional[Gst.Pipeline] = None
        self._webrtc: Optional[Gst.Element] = None
        self._pay: Optional[Gst.Element] = None

        self._remote_desc_set = False
        self._pending_remote_ice: List[Dict[str, Any]] = []
        self._target_peer_id: Optional[str] = None

        self._control_q: "Queue[Dict[str, Any]]" = Queue()
        self._stopping = False

        self.signaling = _SignalingClient(
            cfg=cfg,
            on_ready=self._on_signaling_ready_threadsafe,
            on_message=self._on_signaling_message_threadsafe,
        )

    # ---------- public API ----------

    def start(self):
        if self._glib_thread is None:
            Gst.init(None)
            self._glib_thread = threading.Thread(target=self._run_glib, daemon=True)
            self._glib_thread.start()

        self.signaling.start()

    def stop(self):
        self._stopping = True
        try:
            self.signaling.stop()
        except Exception:
            pass
        GLib.idle_add(self._stop_pipeline)
        try:
            self._glib_loop.quit()
        except Exception:
            pass

    def get_control(self, timeout: Optional[float] = 0.0) -> Optional[Dict[str, Any]]:
        try:
            return self._control_q.get(timeout=timeout if timeout is not None else 0.0)
        except Empty:
            return None

    def send_telemetry(self, payload: Dict[str, Any], to: Optional[str] = None):
        msg = {"type": "telemetry", "roomId": self.cfg.room, "payload": payload}
        if to:
            msg["to"] = to
        self.signaling.send(msg)

    def send_control_ack(self, payload: Dict[str, Any], to: Optional[str] = None):
        msg = {"type": "control-ack", "roomId": self.cfg.room, "payload": payload}
        if to:
            msg["to"] = to
        self.signaling.send(msg)

    # ---------- internal thread plumbing ----------

    def _run_glib(self):
        try:
            self._glib_loop.run()
        except Exception:
            log.exception("GLib loop crashed")

    def _on_signaling_ready_threadsafe(self):
        # If we're the offerer, start pipeline immediately; otherwise wait for an offer.
        if self.cfg.enable_video and self.cfg.offerer:
            GLib.idle_add(self._ensure_pipeline_running)

    def _on_signaling_message_threadsafe(self, msg: Dict[str, Any]):
        GLib.idle_add(self._handle_message, msg)

    # ---------- GStreamer pipeline ----------

    def _make(self, factory: str, name: Optional[str] = None) -> Gst.Element:
        e = Gst.ElementFactory.make(factory, name)
        if not e:
            raise RuntimeError(f"Failed to create element: {factory}")
        return e

    def _ensure_pipeline_running(self):
        if self._pipeline is None and self.cfg.enable_video:
            self._build_pipeline()
            assert self._pipeline is not None
            log.info("Pipeline built; setting PLAYING (capture_fps=%d send_fps=%d)", self.cfg.capture_fps, self.cfg.send_fps)
            self._pipeline.set_state(Gst.State.PLAYING)
        return False

    def _stop_pipeline(self):
        if self._pipeline:
            self._pipeline.set_state(Gst.State.NULL)
        self._pipeline = None
        self._webrtc = None
        self._pay = None
        self._remote_desc_set = False
        self._pending_remote_ice.clear()
        self._target_peer_id = None
        return False

    def _build_pipeline(self):
        cfg = self.cfg

        pipe = Gst.Pipeline.new("robot-webrtc-pipe")

        webrtc = self._make("webrtcbin", "webrtc")
        webrtc.set_property("bundle-policy", "max-bundle")
        webrtc.set_property("latency", int(cfg.webrtc_latency_ms))
        if cfg.stun:
            webrtc.set_property("stun-server", cfg.stun)
        if cfg.turn and webrtc.find_property("turn-server") is not None:
            webrtc.set_property("turn-server", cfg.turn)

        v4l2 = self._make("v4l2src", "src")
        v4l2.set_property("device", cfg.device)
        if v4l2.find_property("io-mode") is not None:
            v4l2.set_property("io-mode", 2)

        caps_in = self._make("capsfilter", "caps_in")
        caps_in.set_property(
            "caps",
            Gst.Caps.from_string(
                f"video/x-raw,format=YUY2,width={cfg.width},height={cfg.height},framerate={cfg.capture_fps}/1"
            ),
        )

        # Drop frames if needed to match send_fps (keeps "latest frame" behavior)
        videorate = self._make("videorate", "videorate")
        if videorate.find_property("drop-only") is not None:
            videorate.set_property("drop-only", True)

        caps_outfps = self._make("capsfilter", "caps_outfps")
        caps_outfps.set_property("caps", Gst.Caps.from_string(f"video/x-raw,framerate={cfg.send_fps}/1"))

        q1 = self._make("queue", "q1")
        q1.set_property("leaky", 2)  # downstream
        q1.set_property("max-size-buffers", 1)

        nvvidconv = self._make("nvvidconv", "nvvidconv")

        caps_nv12 = self._make("capsfilter", "caps_nv12")
        caps_nv12.set_property("caps", Gst.Caps.from_string("video/x-raw(memory:NVMM),format=NV12"))

        q2 = self._make("queue", "q2")
        q2.set_property("leaky", 2)
        q2.set_property("max-size-buffers", 1)

        enc = self._make("nvv4l2h264enc", "enc")
        enc.set_property("bitrate", int(cfg.bitrate))
        enc.set_property("iframeinterval", int(cfg.iframeinterval))
        if enc.find_property("insert-sps-pps") is not None:
            enc.set_property("insert-sps-pps", True)
        if enc.find_property("profile") is not None:
            enc.set_property("profile", 0)  # baseline

        h264parse = self._make("h264parse", "h264parse")

        pay = self._make("rtph264pay", "pay")
        pay.set_property("pt", 96)  # may be overridden based on OFFER
        pay.set_property("config-interval", int(cfg.config_interval))
        if pay.find_property("mtu") is not None:
            pay.set_property("mtu", int(cfg.mtu))
        if pay.find_property("aggregate-mode") is not None:
            pay.set_property("aggregate-mode", "zero-latency")

        for e in [v4l2, caps_in, videorate, caps_outfps, q1, nvvidconv, caps_nv12, q2, enc, h264parse, pay, webrtc]:
            pipe.add(e)

        _link_many(v4l2, caps_in, videorate, caps_outfps, q1, nvvidconv, caps_nv12, q2, enc, h264parse, pay)

        pay_src = pay.get_static_pad("src")
        if not pay_src:
            raise RuntimeError("rtph264pay has no src pad")

        if hasattr(webrtc, "request_pad_simple"):
            webrtc_sink = webrtc.request_pad_simple("sink_%u")
        else:
            webrtc_sink = webrtc.get_request_pad("sink_%u")

        if not webrtc_sink:
            raise RuntimeError("Failed to request webrtcbin sink pad")

        if pay_src.link(webrtc_sink) != Gst.PadLinkReturn.OK:
            caps = pay_src.get_current_caps() or pay_src.query_caps(None)
            raise RuntimeError(f"Failed to link pay -> webrtc (caps={caps.to_string() if caps else 'None'})")

        # webrtc callbacks
        webrtc.connect("on-new-transceiver", self._on_new_transceiver)
        webrtc.connect("on-negotiation-needed", self._on_negotiation_needed)
        webrtc.connect("on-ice-candidate", self._on_ice_candidate)
        webrtc.connect("notify::connection-state", self._on_conn_state)
        webrtc.connect("notify::ice-connection-state", self._on_ice_state)
        webrtc.connect("notify::ice-gathering-state", self._on_ice_gathering)

        bus = pipe.get_bus()
        bus.add_signal_watch()
        bus.connect("message", self._on_bus_message)

        self._pipeline = pipe
        self._webrtc = webrtc
        self._pay = pay

    # ---------- webrtcbin handlers ----------

    def _on_new_transceiver(self, _element, transceiver):
        try:
            transceiver.props.direction = GstWebRTC.WebRTCRTPTransceiverDirection.SENDONLY
            log.info("Transceiver set to SENDONLY")
        except Exception:
            log.exception("Failed to set transceiver direction")

    def _on_negotiation_needed(self, element):
        # Usually we are answerer, so we ignore.
        if not self.cfg.offerer:
            log.info("Negotiation-needed (ignored; answerer mode)")
            return
        log.info("Negotiation-needed: creating OFFER (robot is offerer)")
        promise = Gst.Promise.new_with_change_func(self._on_offer_created, None)
        element.emit("create-offer", None, promise)

    def _on_offer_created(self, promise: Gst.Promise, _user_data):
        try:
            assert self._webrtc is not None
            reply = promise.get_reply()
            offer = reply.get_value("offer")
            self._webrtc.emit("set-local-description", offer, None)
            sdp_text = offer.sdp.as_text()
            envelope: Dict[str, Any] = {
                "type": "offer",
                "roomId": self.cfg.room,
                "payload": {"sdp": sdp_text, "sdpType": "offer"},
            }
            if self._target_peer_id:
                envelope["to"] = self._target_peer_id
            self.signaling.send(envelope)
            log.info("Sent OFFER (%d bytes)", len(sdp_text))
        except Exception:
            log.exception("Offer creation failed")

    def _on_answer_created(self, promise: Gst.Promise, _user_data):
        try:
            assert self._webrtc is not None
            reply = promise.get_reply()
            answer = reply.get_value("answer")
            self._webrtc.emit("set-local-description", answer, None)
            sdp_text = answer.sdp.as_text()
            envelope: Dict[str, Any] = {
                "type": "answer",
                "roomId": self.cfg.room,
                "payload": {"sdp": sdp_text, "sdpType": "answer"},
            }
            if self._target_peer_id:
                envelope["to"] = self._target_peer_id
            self.signaling.send(envelope)
            log.info("Sent ANSWER (%d bytes)", len(sdp_text))
        except Exception:
            log.exception("Answer creation failed")

    def _on_ice_candidate(self, _element, mlineindex: int, candidate: str):
        envelope: Dict[str, Any] = {
            "type": "ice-candidate",
            "roomId": self.cfg.room,
            "payload": {"candidate": candidate, "sdpMLineIndex": int(mlineindex)},
        }
        if self._target_peer_id:
            envelope["to"] = self._target_peer_id
        self.signaling.send(envelope)

    def _on_conn_state(self, *args):
        if self._webrtc:
            state = self._webrtc.get_property("connection-state").value_nick
            log.info("PeerConnection state: %s", state)
            self._maybe_stop_pipeline_on_disconnect(state, source="pc")

    def _on_ice_state(self, *args):
        if self._webrtc:
            state = self._webrtc.get_property("ice-connection-state").value_nick
            log.info("ICE state: %s", state)
            self._maybe_stop_pipeline_on_disconnect(state, source="ice")

    def _on_ice_gathering(self, *args):
        if self._webrtc:
            log.info("ICE gathering: %s", self._webrtc.get_property("ice-gathering-state").value_nick)

    # ---------- signaling message handling (GLib thread) ----------

    def _handle_message(self, msg: Dict[str, Any]):
        mtype = msg.get("type")
        payload = msg.get("payload") or {}

        # control plane (always allowed even if video disabled)
        if mtype == "control":
            # push whole envelope so you can see msg["from"] etc.
            self._control_q.put(msg)
            return False

        if mtype == "peer-left":
            cid = payload.get("clientId")
            if cid and self._target_peer_id and str(cid) == self._target_peer_id:
                log.info("Target peer left (%s); resetting pipeline", cid)
                self._target_peer_id = None
                self._stop_pipeline()
            return False

        if not self.cfg.enable_video:
            return False

        if self._webrtc is None:
            # Start pipeline lazily: only when we actually need WebRTC (offer/answer/ICE or offerer mode).
            if self.cfg.offerer or mtype in ("offer", "answer", "ice-candidate"):
                self._ensure_pipeline_running()
            if self._webrtc is None:
                return False

        if mtype == "offer":
            sdp = payload.get("sdp")
            if sdp:
                sender = msg.get("from")
                if sender:
                    # If a new viewer connects, retarget and restart pipeline cleanly.
                    if self._target_peer_id and str(sender) != self._target_peer_id:
                        log.info(
                            "New offer from %s; switching target from %s and restarting pipeline",
                            sender,
                            self._target_peer_id,
                        )
                        self._stop_pipeline()
                    self._target_peer_id = str(sender)
                # match H264 payload type if possible
                pt = self._pick_h264_pt_from_offer(str(sdp))
                if pt is not None and self._pay is not None:
                    try:
                        self._pay.set_property("pt", int(pt))
                        log.info("Using H264 payload type from offer: pt=%d", pt)
                    except Exception:
                        log.exception("Failed setting rtph264pay pt")

                # always accept new offers (supports reconnect/reload)
                self._remote_desc_set = False
                self._pending_remote_ice.clear()
                self._set_remote_sdp(str(sdp), "offer")
            return False

        if mtype == "answer":
            sdp = payload.get("sdp")
            if sdp:
                sender = msg.get("from")
                if self._target_peer_id and sender and str(sender) != self._target_peer_id:
                    log.info("Ignoring ANSWER from non-target peer: %s", sender)
                    return False
                if sender:
                    self._target_peer_id = str(sender)
                self._set_remote_sdp(str(sdp), "answer")
            return False

        if mtype == "ice-candidate":
            cand = payload.get("candidate")
            idx = payload.get("sdpMLineIndex")
            sender = msg.get("from")
            if self._target_peer_id and sender and str(sender) != self._target_peer_id:
                log.info("Ignoring ICE from non-target peer: %s", sender)
                return False
            if not self._target_peer_id and sender:
                self._target_peer_id = str(sender)
            if cand is not None and idx is not None:
                self._add_remote_ice(int(idx), str(cand))
            return False

        return False

    def _pick_h264_pt_from_offer(self, sdp_text: str) -> Optional[int]:
        res, sdpmsg = GstSdp.sdp_message_new()
        if res != GstSdp.SDPResult.OK:
            return None
        if GstSdp.sdp_message_parse_buffer(sdp_text.encode("utf-8"), sdpmsg) != GstSdp.SDPResult.OK:
            return None

        for mi in range(sdpmsg.medias_len()):
            media = sdpmsg.get_media(mi)
            if media.get_media() != "video":
                continue

            pt_to_codec: Dict[int, str] = {}
            for ai in range(media.attributes_len()):
                attr = media.get_attribute(ai)
                if attr.key != "rtpmap":
                    continue
                val = attr.value or ""
                parts = val.split()
                if len(parts) < 2:
                    continue
                try:
                    pt = int(parts[0])
                except ValueError:
                    continue
                codec = parts[1].split("/")[0].upper()
                pt_to_codec[pt] = codec

            for fi in range(media.formats_len()):
                try:
                    pt = int(media.get_format(fi))
                except Exception:
                    continue
                if pt_to_codec.get(pt) == "H264":
                    return pt

        return None

    def _set_remote_sdp(self, sdp_text: str, sdp_type: str):
        assert self._webrtc is not None

        res, sdpmsg = GstSdp.sdp_message_new()
        if res != GstSdp.SDPResult.OK:
            raise RuntimeError("Failed to create SDPMessage")

        if GstSdp.sdp_message_parse_buffer(sdp_text.encode("utf-8"), sdpmsg) != GstSdp.SDPResult.OK:
            raise RuntimeError("Failed to parse SDP")

        desc_type = GstWebRTC.WebRTCSDPType.OFFER if sdp_type == "offer" else GstWebRTC.WebRTCSDPType.ANSWER
        desc = GstWebRTC.WebRTCSessionDescription.new(desc_type, sdpmsg)

        self._webrtc.emit("set-remote-description", desc, None)
        self._remote_desc_set = True

        # flush buffered ICE
        for item in self._pending_remote_ice:
            self._webrtc.emit("add-ice-candidate", item["mline"], item["cand"])
        self._pending_remote_ice.clear()

        if sdp_type == "offer":
            log.info("Remote OFFER received: creating ANSWER")
            promise = Gst.Promise.new_with_change_func(self._on_answer_created, None)
            self._webrtc.emit("create-answer", None, promise)

    def _add_remote_ice(self, mline: int, cand: str):
        assert self._webrtc is not None
        if not self._remote_desc_set:
            self._pending_remote_ice.append({"mline": mline, "cand": cand})
            return
        self._webrtc.emit("add-ice-candidate", mline, cand)

    def _maybe_stop_pipeline_on_disconnect(self, state: str, source: str):
        """
        Tear down pipeline when WebRTC/ICE transitions to disconnected/failed/closed.
        This frees camera/encoder resources until a fresh offer arrives.
        """
        bad = {"disconnected", "failed", "closed"}
        if state.lower() in bad:
            log.info("Stopping pipeline due to %s state=%s", source, state)
            self._stop_pipeline()

    # ---------- bus ----------

    def _on_bus_message(self, _bus: Gst.Bus, msg: Gst.Message):
        t = msg.type
        if t == Gst.MessageType.ERROR:
            err, dbg = msg.parse_error()
            log.error("GStreamer ERROR: %s (%s)", err, dbg)
            # If something goes wrong, tear down pipeline so next offer can restart cleanly
            self._stop_pipeline()
        elif t == Gst.MessageType.EOS:
            log.warning("GStreamer EOS")
            self._stop_pipeline()
        return True


# Aliases for clearer naming
VideoSenderConfig = RobotWebRTCConfig
VideoSender = RobotWebRTCNode
