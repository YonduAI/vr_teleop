#!/usr/bin/env python3
"""
Minimal, readable example: how to send one control JSON to the robot.

Steps demonstrated:
  1) Connect via WebSocket to the signaling server.
  2) Auth with the shared token.
  3) Join a room with role=controller.
  4) Pick a robot target by name/role (or send broadcast).
  5) Send a single control payload.

Defaults match this repo:
  ws   = ws://13.56.253.215:3000
  token= supersecret123
  room = testroom
  target-name = orin-cmd

Run:
  python3 send_control_tutorial.py --verbose
  python3 send_control_tutorial.py --payload '{"cmd":"forward","speed":0.5}'
"""

import argparse
import asyncio
import json
import logging
from typing import Any, Dict, List, Optional

import websockets


DEFAULT_WS = "ws://13.56.253.215:3000"
DEFAULT_TOKEN = "supersecret123"
DEFAULT_ROOM = "testroom"


def pick_target(peers: List[Dict[str, Any]], preferred_name: Optional[str], preferred_role: str) -> Optional[str]:
    """Return a clientId from peers matching preferred_name, else preferred_role."""
    if preferred_name:
        for peer in peers:
            if peer.get("name") == preferred_name:
                return str(peer.get("clientId"))
    for peer in peers:
        if (peer.get("role") or "").lower() == preferred_role.lower():
            return str(peer.get("clientId"))
    return None


async def send_control(args):
    logging.info("Connecting to %s", args.ws)
    async with websockets.connect(args.ws, ping_interval=20, ping_timeout=20) as ws:
        # 1) Auth
        await ws.send(json.dumps({"type": "auth", "payload": {"token": args.token}}))
        while True:
            msg = json.loads(await ws.recv())
            if msg.get("type") == "auth-ok":
                logging.info("Auth OK (clientId=%s)", msg.get("payload", {}).get("clientId"))
                break

        # 2) Join
        await ws.send(
            json.dumps(
                {"type": "join", "roomId": args.room, "payload": {"role": args.role, "name": args.name}}
            )
        )
        joined = json.loads(await ws.recv())
        peers = joined.get("payload", {}).get("peers", []) or []
        logging.info("Peers in room: %s", peers)

        # 3) Pick target
        target = args.to or pick_target(peers, args.target_name, args.target_role)
        if target:
            logging.info("Targeting clientId=%s", target)
        else:
            logging.info("No target found; sending broadcast (server will route to first robot)")

        # 4) Send control
        envelope = {"type": "control", "roomId": args.room, "payload": args.payload}
        if target:
            envelope["to"] = target
        logging.info("Sending control payload: %s", args.payload)
        await ws.send(json.dumps(envelope))

        # 5) Listen briefly for a response (control-ack/telemetry)
        try:
            reply = await asyncio.wait_for(ws.recv(), timeout=args.wait)
            logging.info("Received: %s", reply)
        except asyncio.TimeoutError:
            logging.info("No response within %.1fs (that's OK)", args.wait)


def parse_args():
    p = argparse.ArgumentParser(description="Tiny tutorial script to send one control JSON to the robot.")
    p.add_argument("--ws", default=DEFAULT_WS, help="Signaling WebSocket URL")
    p.add_argument("--token", default=DEFAULT_TOKEN, help="Shared signaling token")
    p.add_argument("--room", default=DEFAULT_ROOM, help="Room ID")
    p.add_argument("--role", default="controller", help="Role announced when joining")
    p.add_argument("--name", default="control-tutorial", help="Name announced when joining")
    p.add_argument("--target-name", default="orin-cmd", help="Preferred robot name to receive control")
    p.add_argument("--target-role", default="robot", help="Fallback role to pick a target if name not found")
    p.add_argument("--to", default=None, help="Explicit clientId to target (overrides name/role)")
    p.add_argument("--payload", type=json.loads, default='{"cmd":"demo","value":1}', help="Control JSON to send")
    p.add_argument("--wait", type=float, default=2.0, help="Seconds to wait for a response before exiting")
    p.add_argument("--verbose", action="store_true")
    return p.parse_args()


def main():
    args = parse_args()
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
    )
    asyncio.run(send_control(args))


if __name__ == "__main__":
    main()
