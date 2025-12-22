#!/usr/bin/env python3
"""
Robot video sender entrypoint using teleop_core.video_sender.

Defaults match your test environment but can be overridden via CLI flags.
Pipeline starts lazily when a viewer connects (unless offerer mode is enabled).
"""

import argparse
import logging
import time

from teleop_core.video_sender import VideoSenderConfig, VideoSender


def parse_args():
    p = argparse.ArgumentParser(description="Robot video sender (WebRTC)")
    p.add_argument("--ws", default="ws://13.56.253.215:3000")
    p.add_argument("--token", default="supersecret123")
    p.add_argument("--room", default="testroom")
    p.add_argument("--role", default="robot")
    p.add_argument("--name", default="orin")
    p.add_argument("--device", default="/dev/video0")
    p.add_argument("--width", type=int, default=1344)
    p.add_argument("--height", type=int, default=376)
    p.add_argument("--capture-fps", type=int, default=100)
    p.add_argument("--send-fps", type=int, default=100)
    p.add_argument("--bitrate", type=int, default=12_000_000)
    p.add_argument("--iframeinterval", type=int, default=100)
    p.add_argument("--stun", default="stun://stun.l.google.com:19302")
    p.add_argument("--turn", default="", help="turn://user:pass@host:port?transport=udp")
    p.add_argument("--offerer", action="store_true", help="make robot create offers (default: answerer)")
    p.add_argument("--verbose", action="store_true")
    return p.parse_args()


def main():
    args = parse_args()
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    cfg = VideoSenderConfig(
        ws_url=args.ws,
        token=args.token,
        room=args.room,
        role=args.role,
        name=args.name,
        enable_video=True,
        device=args.device,
        width=args.width,
        height=args.height,
        capture_fps=args.capture_fps,
        send_fps=args.send_fps,
        bitrate=args.bitrate,
        iframeinterval=args.iframeinterval,
        stun=args.stun,
        turn=args.turn,
        offerer=args.offerer,
    )

    node = VideoSender(cfg)
    node.start()

    print("Streaming... Ctrl+C to stop.")
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        pass
    finally:
        node.stop()


if __name__ == "__main__":
    main()
