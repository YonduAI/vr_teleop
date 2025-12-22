#!/usr/bin/env python3
"""
Library for receiving control messages on the robot (no video).

Wraps RobotWebRTCNode with enable_video=False so you can embed control handling
without standing up the video pipeline.
"""

from dataclasses import dataclass
from typing import Any, Dict, Optional

from teleop_core.video_sender import RobotWebRTCConfig, RobotWebRTCNode


@dataclass
class ControlReceiverConfig:
    ws_url: str
    token: str
    room: str
    role: str = "robot"
    name: str = "robot-cmd"


class ControlReceiver:
    def __init__(self, cfg: ControlReceiverConfig):
        self.cfg = cfg
        self._node = RobotWebRTCNode(
            RobotWebRTCConfig(
                ws_url=cfg.ws_url,
                token=cfg.token,
                room=cfg.room,
                role=cfg.role,
                name=cfg.name,
                enable_video=False,
            )
        )

    def start(self):
        self._node.start()

    def stop(self):
        self._node.stop()

    def get_control(self, timeout: Optional[float] = 0.0) -> Optional[Dict[str, Any]]:
        return self._node.get_control(timeout=timeout)

    def send_control_ack(self, payload: Dict[str, Any], to: Optional[str] = None):
        self._node.send_control_ack(payload, to=to)

    def send_telemetry(self, payload: Dict[str, Any], to: Optional[str] = None):
        self._node.send_telemetry(payload, to=to)
