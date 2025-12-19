#!/usr/bin/env python3
"""
Send demo control JSON to the robot so robot_print_commands.py will print it.

Defaults match the rest of this repo:
  ws   = ws://13.56.253.215:3000
  token= supersecret123
  room = testroom
Identifies as role=controller; signaling server routes control -> robot.
"""

import argparse
import asyncio
import json
import logging
import random
import time
import uuid
from typing import Any, Dict, List, Optional

import websockets


DEFAULT_WS = "ws://13.56.253.215:3000"
DEFAULT_TOKEN = "supersecret123"
DEFAULT_ROOM = "testroom"


def pick_target(
    peers: List[Dict[str, Any]],
    preferred_name: Optional[str],
    preferred_role: str,
) -> Optional[str]:
    if preferred_name:
        for peer in peers:
            if peer.get("name") == preferred_name:
                return str(peer.get("clientId"))
    for peer in peers:
        if (peer.get("role") or "").lower() == preferred_role.lower():
            return str(peer.get("clientId"))
    return None


async def main_async(args):
    async with websockets.connect(args.ws, ping_interval=20, ping_timeout=20) as ws:
        # auth
        await ws.send(json.dumps({"type": "auth", "payload": {"token": args.token}}))
        while True:
            raw = await ws.recv()
            msg = json.loads(raw)
            if msg.get("type") == "auth-ok":
                break

        # join
        await ws.send(
            json.dumps(
                {"type": "join", "roomId": args.room, "payload": {"role": args.role, "name": args.name}}
            )
        )

        # wait for joined to learn peer ids (so we can target the cmd robot)
        peers: List[Dict[str, Any]] = []
        try:
            joined_raw = await asyncio.wait_for(ws.recv(), timeout=2.0)
            joined = json.loads(joined_raw)
            if joined.get("type") == "joined":
                peers = joined.get("payload", {}).get("peers", []) or []
                logging.info("Peers: %s", peers)
        except asyncio.TimeoutError:
            logging.info("No joined message received; sending broadcast")

        target = args.to or pick_target(peers, args.target_name, args.target_role)
        if target:
            logging.info("Sending control to target %s", target)
        else:
            logging.info("Sending control broadcast (no target found) — robot with role=robot will receive")

        stop_after = None
        if args.duration > 0:
            stop_after = time.time() + args.duration

        async def receiver():
            try:
                async for msg in ws:
                    logging.info("Received: %s", msg)
            except websockets.exceptions.ConnectionClosedOK:
                pass
            except asyncio.CancelledError:
                pass
            except Exception:
                logging.exception("Receiver failed")

        recv_task = asyncio.create_task(receiver())

        interval = 1.0 / args.rate if args.rate > 0 else 0.0
        sent = 0
        try:
            while True:
                if args.count > 0 and sent >= args.count:
                    break
                if stop_after and time.time() > stop_after:
                    break
                payload = {
                    "cmd": "demo",
                    "value": random.random(),
                    "ts": time.time(),
                    "uuid": uuid.uuid4().hex,
                    "seq": sent,
                }
                envelope = {"type": "control", "roomId": args.room, "payload": payload}
                if target:
                    envelope["to"] = target
                await ws.send(json.dumps(envelope))
                sent += 1
                if sent % 30 == 0:
                    logging.info("Sent %d messages...", sent)
                if interval > 0:
                    await asyncio.sleep(interval)
        finally:
            await asyncio.sleep(args.wait)
            recv_task.cancel()
            try:
                await recv_task
            except asyncio.CancelledError:
                pass
            except Exception:
                pass


def parse_args():
    p = argparse.ArgumentParser()
    p.add_argument("--ws", default=DEFAULT_WS)
    p.add_argument("--token", default=DEFAULT_TOKEN)
    p.add_argument("--room", default=DEFAULT_ROOM)
    p.add_argument("--role", default="controller")
    p.add_argument("--name", default="control-cli")
    p.add_argument("--target-role", default="robot", help="preferred role to receive control messages")
    p.add_argument("--target-name", default="orin-cmd", help="preferred name to receive control messages")
    p.add_argument("--to", default=None, help="explicit clientId to receive control (overrides target-name/role)")
    p.add_argument("--count", type=int, default=60, help="number of messages to send (0 = unlimited until duration)")
    p.add_argument("--rate", type=float, default=60.0, help="messages per second")
    p.add_argument("--duration", type=float, default=0.0, help="seconds to run if count=0 (0 = use count only)")
    p.add_argument("--wait", type=float, default=1.0, help="seconds to wait after sending before exit")
    p.add_argument("--verbose", action="store_true")
    return p.parse_args()


def main():
    args = parse_args()
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
    )
    asyncio.run(main_async(args))


if __name__ == "__main__":
    main()
