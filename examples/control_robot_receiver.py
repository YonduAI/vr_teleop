#!/usr/bin/env python3
"""
Example: Control-only robot receiver using teleop_core.command_receiver.
Starts a control listener (no video) and prints incoming control payloads, replies with an ack.
"""

from teleop_core.command_receiver import ControlReceiver, ControlReceiverConfig


def main():
    cfg = ControlReceiverConfig(
        ws_url="ws://13.56.253.215:3000",
        token="supersecret123",
        room="room-alpha",       # change per robot for isolation
        role="robot",
        name="robot-alpha-cmd",  # unique name if sharing rooms
    )
    rx = ControlReceiver(cfg)
    rx.start()
    print("Waiting for control...")
    try:
        while True:
            msg = rx.get_control(timeout=0.5)
            if not msg:
                continue
            payload = msg.get("payload")
            sender = msg.get("from")
            print("CONTROL:", sender, payload)
            # Optional ack back to sender
            rx.send_control_ack({"ok": True, "seq": payload.get("seq")}, to=sender)
    finally:
        rx.stop()


if __name__ == "__main__":
    main()
