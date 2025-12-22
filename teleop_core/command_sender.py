#!/usr/bin/env python3
"""
Library for sending control messages via the signaling server.

Key features:
- Auth + join room as controller (role/name configurable).
- Target selection by explicit clientId, preferred name, or preferred role.
- Async context manager for easy lifecycle.
- Helper to send a single control payload.
"""

import asyncio
import json
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

import websockets


@dataclass
class ControlClientConfig:
    ws_url: str
    token: str
    room: str
    role: str = "controller"
    name: str = "control-client"
    target_name: Optional[str] = None
    target_role: str = "robot"
    target: Optional[str] = None  # explicit clientId


def _pick_target(
    peers: List[Dict[str, Any]],
    preferred_name: Optional[str],
    preferred_role: str,
) -> Optional[str]:
    if preferred_name:
        for peer in peers:
            if (peer.get("name") or "").lower() == preferred_name.lower():
                cid = peer.get("clientId")
                if cid:
                    return str(cid)
    for peer in peers:
        if (peer.get("role") or "").lower() == preferred_role.lower():
            cid = peer.get("clientId")
            if cid:
                return str(cid)
    return None


class ControlClient:
    def __init__(self, cfg: ControlClientConfig):
        self.cfg = cfg
        self._ws: Optional[websockets.WebSocketClientProtocol] = None
        self._target: Optional[str] = cfg.target

    async def __aenter__(self):
        await self.connect()
        return self

    async def __aexit__(self, exc_type, exc, tb):
        await self.close()

    async def connect(self):
        if self._ws:
            return
        self._ws = await websockets.connect(self.cfg.ws_url, ping_interval=20, ping_timeout=20)
        await self._auth_and_join()

    async def close(self):
        if self._ws:
            try:
                await self._ws.close()
            finally:
                self._ws = None

    async def _auth_and_join(self):
        assert self._ws is not None
        await self._ws.send(json.dumps({"type": "auth", "payload": {"token": self.cfg.token}}))
        while True:
            msg = json.loads(await self._ws.recv())
            if msg.get("type") == "auth-ok":
                break

        await self._ws.send(
            json.dumps(
                {
                    "type": "join",
                    "roomId": self.cfg.room,
                    "payload": {"role": self.cfg.role, "name": self.cfg.name},
                }
            )
        )
        joined = json.loads(await self._ws.recv())
        peers = joined.get("payload", {}).get("peers", []) or []
        if self._target is None:
            self._target = _pick_target(peers, self.cfg.target_name, self.cfg.target_role)

    async def send_control(self, payload: Dict[str, Any], to: Optional[str] = None):
        assert self._ws is not None
        target = to or self._target
        envelope: Dict[str, Any] = {"type": "control", "roomId": self.cfg.room, "payload": payload}
        if target:
            envelope["to"] = target
        await self._ws.send(json.dumps(envelope))


async def send_control_once(
    cfg: ControlClientConfig,
    payload: Dict[str, Any],
    wait_reply: float = 0.0,
) -> Optional[Dict[str, Any]]:
    """
    Convenience helper: connect, send one control payload, optionally wait for one reply, then close.
    """
    async with ControlClient(cfg) as client:
        await client.send_control(payload)
        if wait_reply > 0:
            assert client._ws is not None
            try:
                raw = await asyncio.wait_for(client._ws.recv(), timeout=wait_reply)
                return json.loads(raw)
            except asyncio.TimeoutError:
                return None
    return None
