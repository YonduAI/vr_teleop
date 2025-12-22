#!/usr/bin/env python3
"""
Command-only robot listener using teleop_core.command_receiver.
"""

import argparse
import logging

from teleop_core.command_receiver import ControlReceiver, ControlReceiverConfig


def parse_args():
    p = argparse.ArgumentParser(description="Robot control listener (no video)")
    p.add_argument("--ws", default="ws://13.56.253.215:3000")
    p.add_argument("--token", default="supersecret123")
    p.add_argument("--room", default="testroom")
    p.add_argument("--role", default="robot")
    p.add_argument("--name", default="orin-cmd")
    p.add_argument("--verbose", action="true", help=argparse.SUPPRESS)
    return p.parse_args()


def main():
    args = parse_args()
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    cfg = ControlReceiverConfig(
        ws_url=args.ws,
        token=args.token,
        room=args.room,
        role=args.role,
        name=args.name,
    )

    rx = ControlReceiver(cfg)
    rx.start()

    print("Waiting for control messages... Ctrl+C to stop.")
    try:
        while True:
            msg = rx.get_control(timeout=0.5)
            if msg:
                print("CONTROL:", msg.get("from"), msg.get("payload"))
    except KeyboardInterrupt:
        pass
    finally:
        rx.stop()


if __name__ == "__main__":
    main()
