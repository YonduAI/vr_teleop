#!/usr/bin/env python3
"""
CLI video receiver that targets the signaling layout used by robot_stream_video (teleop_core.video_sender).

- Announces itself as role=viewer so the signaling server can route offers to the robot.
- Auto-selects the first peer with role=robot (or a specific --target clientId) and re-offers when that changes.
- Defaults match robot_stream_video.py (ws/token/room) but can be overridden with flags.
"""

import logging

import gi

gi.require_version("Gst", "1.0")
from gi.repository import Gst  # type: ignore

from teleop_core.video_receiver_core import WebRTCReceiver, build_arg_parser


DEFAULT_WS = "ws://13.56.253.215:3000"
DEFAULT_TOKEN = "supersecret123"
DEFAULT_ROOM = "testroom"


def main():
    parser = build_arg_parser(require_all=False)
    parser.set_defaults(
        ws=DEFAULT_WS,
        token=DEFAULT_TOKEN,
        room=DEFAULT_ROOM,
        role="viewer",
        name="viewer-cli",
        target_role="robot",
        target_name="orin",
    )
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    Gst.init(None)
    WebRTCReceiver(args).run()


if __name__ == "__main__":
    main()
