#!/usr/bin/env python3
"""
Example: Controller sending different commands to multiple robots (different rooms/names).
Uses teleop_core.command_sender for targeting by name/role and optional ack waiting.
"""

import asyncio

from teleop_core.command_sender import ControlClient, ControlClientConfig, send_control_once

WS = "ws://13.56.253.215:3000"
TOKEN = "supersecret123"


async def send_to_robot(room, target_name, payload, wait_ack=False):
    cfg = ControlClientConfig(
        ws_url=WS,
        token=TOKEN,
        room=room,
        role="controller",
        name="controller-ops",
        target_name=target_name,  # prefer this robot name
        target_role="robot",      # fallback
    )
    if wait_ack:
        reply = await send_control_once(cfg, payload, wait_reply=2.0)
        print(room, target_name, "reply:", reply)
    else:
        async with ControlClient(cfg) as client:
            await client.send_control(payload)


async def main():
    tasks = [
        send_to_robot("room-alpha", "robot-alpha-cmd", {"cmd": "move", "dir": "left", "seq": 1}, wait_ack=True),
        send_to_robot("room-beta", "robot-beta-cmd", {"cmd": "move", "dir": "right", "seq": 2}),
        send_to_robot("room-gamma", "robot-gamma-cmd", {"cmd": "stop", "seq": 3}, wait_ack=True),
        send_to_robot("room-delta", "robot-delta-cmd", {"cmd": "set_speed", "value": 0.8, "seq": 4}),
        send_to_robot("room-epsilon", "robot-epsilon-cmd", {"cmd": "lights", "state": "on", "seq": 5}, wait_ack=True),
    ]
    await asyncio.gather(*tasks)


if __name__ == "__main__":
    asyncio.run(main())
