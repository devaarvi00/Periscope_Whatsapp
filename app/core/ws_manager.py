import asyncio
import json
import logging
from typing import Any

from fastapi import WebSocket

logger = logging.getLogger(__name__)


class ConnectionManager:
    def __init__(self) -> None:
        # agent_id -> set of active WebSocket connections
        self._connections: dict[int, list[WebSocket]] = {}

    async def connect(self, websocket: WebSocket, agent_id: int) -> None:
        await websocket.accept()
        self._connections.setdefault(agent_id, []).append(websocket)
        logger.info("WS connected: agent_id=%s  total_agents=%s", agent_id, len(self._connections))
        # Confirm connection to the client
        await self._send(websocket, {"type": "connected", "agent_id": agent_id})

    def disconnect(self, websocket: WebSocket, agent_id: int) -> None:
        conns = self._connections.get(agent_id, [])
        if websocket in conns:
            conns.remove(websocket)
        if not conns:
            self._connections.pop(agent_id, None)
        logger.info("WS disconnected: agent_id=%s  total_agents=%s", agent_id, len(self._connections))

    # ── Internal helpers ──────────────────────────────────────────── #

    async def _send(self, websocket: WebSocket, payload: dict) -> bool:
        try:
            await websocket.send_text(json.dumps(payload))
            return True
        except Exception:
            return False

    def _dead_cleanup(self, agent_id: int, dead: list[WebSocket]) -> None:
        for ws in dead:
            self.disconnect(ws, agent_id)

    # ── Broadcast to all connected agents ─────────────────────────── #

    async def broadcast(self, event: str, data: Any) -> None:
        payload = {"event": event, "data": data}
        for agent_id, conns in list(self._connections.items()):
            dead: list[WebSocket] = []
            for ws in conns:
                ok = await self._send(ws, payload)
                if not ok:
                    dead.append(ws)
            self._dead_cleanup(agent_id, dead)

    # ── Send to a specific agent (all their tabs) ──────────────────── #

    async def send_to_agent(self, agent_id: int, event: str, data: Any) -> None:
        payload = {"event": event, "data": data}
        dead: list[WebSocket] = []
        for ws in list(self._connections.get(agent_id, [])):
            ok = await self._send(ws, payload)
            if not ok:
                dead.append(ws)
        self._dead_cleanup(agent_id, dead)

    # ── Convenience event emitters ─────────────────────────────────── #

    async def emit_new_message(self, chat_id: int, chat_wid: str,
                               body: str, from_me: bool,
                               sender_name: str = "", timestamp: int = 0,
                               message_type: str = "text", has_media: bool = False,
                               sender_number: str = "") -> None:
        await self.broadcast("new_message", {
            "chat_id": chat_id,
            "chat_wid": chat_wid,
            "body": body,
            "from_me": from_me,
            "sender_name": sender_name,
            "sender_number": sender_number,
            "timestamp": timestamp,
            "message_type": message_type,
            "has_media": has_media,
        })

    async def emit_chat_updated(self, chat_id: int, fields: dict) -> None:
        await self.broadcast("chat_updated", {"chat_id": chat_id, **fields})

    async def emit_ticket_event(self, event: str, ticket_id: int, fields: dict) -> None:
        await self.broadcast(event, {"ticket_id": ticket_id, **fields})

    async def emit_typing(self, chat_id: int, is_typing: bool) -> None:
        await self.broadcast("typing", {"chat_id": chat_id, "is_typing": is_typing})

    @property
    def online_count(self) -> int:
        return sum(len(c) for c in self._connections.values())

    @property
    def online_agent_ids(self) -> list[int]:
        return list(self._connections.keys())


ws_manager = ConnectionManager()
