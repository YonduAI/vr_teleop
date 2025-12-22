#!/usr/bin/env python3
"""
Video receiver wrapper (imports the full WebRTC receiver core).
"""

from dataclasses import dataclass
from types import SimpleNamespace
from typing import Optional, Callable

from teleop_core.video_receiver_core import WebRTCReceiver
VideoReceiver = WebRTCReceiver


@dataclass
class VideoReceiverConfig:
    ws_url: str
    token: str
    room: str
    role: str = "viewer"
    name: str = "python-viewer"
    target_name: str = "orin"
    target_role: str = "robot"
    target: Optional[str] = None
    stun: str = "stun://stun.l.google.com:19302"
    turn: str = ""
    verbose: bool = False
    on_frame: Optional[Callable[[bytes, object], None]] = None  # callback(data, sample)


def _to_args(cfg: VideoReceiverConfig):
    return SimpleNamespace(
        ws=cfg.ws_url,
        token=cfg.token,
        room=cfg.room,
        role=cfg.role,
        name=cfg.name,
        target_name=cfg.target_name,
        target_role=cfg.target_role,
        target=cfg.target,
        stun=cfg.stun,
        turn=cfg.turn,
        verbose=cfg.verbose,
        on_frame=cfg.on_frame,
    )


def run_video_receiver(cfg: VideoReceiverConfig):
    args = _to_args(cfg)
    r = WebRTCReceiver(args)
    r.run()
