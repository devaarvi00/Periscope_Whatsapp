import asyncio
import logging
import random
from dataclasses import dataclass
from typing import Any

import httpx

from app.core.config import settings

logger = logging.getLogger(__name__)


class WAHAError(Exception):
    def __init__(self, code: str, message: str, status_code: int | None = None) -> None:
        super().__init__(message)
        self.code = code
        self.status_code = status_code


@dataclass(slots=True)
class SendResult:
    message_id: str | None
    raw: dict[str, Any]


class WAHAService:
    def __init__(self, session_name: str = "") -> None:
        self.base = settings.waha_base_url.rstrip("/")
        self.session = session_name or settings.waha_session_name
        self._headers = {
            "X-Api-Key": settings.waha_api_key,
            "Content-Type": "application/json",
            "Accept": "application/json",
        }

    # ── Session ──────────────────────────────────────────────────────────────

    async def get_session_status(self) -> str:
        from app.core.http_client import get_http_client
        url = f"{self.base}/api/sessions/{self.session}"
        resp = await get_http_client().get(url, headers=self._headers)
        if resp.is_success:
            data = resp.json()
            return str(data.get("status", "UNKNOWN")).upper()
        return "UNKNOWN"

    async def get_me(self) -> dict[str, Any]:
        from app.core.http_client import get_http_client
        url = f"{self.base}/api/sessions/{self.session}"
        resp = await get_http_client().get(url, headers=self._headers)
        if resp.is_success:
            data = resp.json()
            return data.get("me") or {}
        return {}

    async def start_session(self) -> bool:
        from app.core.http_client import get_http_client
        url = f"{self.base}/api/sessions/{self.session}/start"
        resp = await get_http_client().post(url, headers=self._headers, json={})
        return resp.is_success

    async def stop_session(self) -> bool:
        from app.core.http_client import get_http_client
        url = f"{self.base}/api/sessions/{self.session}/stop"
        resp = await get_http_client().post(url, headers=self._headers, json={})
        return resp.is_success

    async def restart_session(self) -> bool:
        from app.core.http_client import get_http_client
        url = f"{self.base}/api/sessions/{self.session}/restart"
        resp = await get_http_client().post(url, headers=self._headers, json={})
        return resp.is_success

    async def get_qr(self) -> str | None:
        """Return QR code as a base64 data URI for display."""
        from app.core.http_client import get_http_client
        url = f"{self.base}/api/{self.session}/auth/qr"
        headers = {**self._headers, "Accept": "image/png"}
        resp = await get_http_client().get(url, headers=headers, params={"format": "image"})
        if resp.is_success and resp.content:
            import base64
            b64 = base64.b64encode(resp.content).decode()
            return f"data:image/png;base64,{b64}"
        return None

    # ── Chats ─────────────────────────────────────────────────────────────────

    async def get_chats(self, limit: int = 100, offset: int = 0) -> list[dict[str, Any]]:
        from app.core.http_client import get_http_client
        url = f"{self.base}/api/{self.session}/chats"
        params = {"limit": limit, "offset": offset}
        try:
            resp = await get_http_client().get(url, headers=self._headers, params=params)
            if resp.is_success:
                data = resp.json()
                return data if isinstance(data, list) else data.get("chats", [])
        except Exception as exc:
            logger.warning("WAHA get_chats error: %s", exc)
        return []

    async def get_messages(self, chat_id: str, limit: int = 50, offset: int = 0) -> list[dict[str, Any]]:
        from app.core.http_client import get_http_client
        url = f"{self.base}/api/{self.session}/chats/{chat_id}/messages"
        params = {"limit": limit, "offset": offset, "downloadMedia": False}
        try:
            resp = await get_http_client().get(url, headers=self._headers, params=params)
            if resp.is_success:
                data = resp.json()
                return data if isinstance(data, list) else data.get("messages", [])
        except Exception as exc:
            logger.warning("WAHA get_messages error: %s", exc)
        return []

    # ── Sending ────────────────────────────────────────────────────────────────

    async def send_text(self, chat_id: str, text: str) -> SendResult:
        if not chat_id.endswith(("@c.us", "@g.us", "@lid")):
            chat_id = f"{chat_id}@c.us"
        if settings.waha_human_simulation_enabled:
            await self._simulate_typing(chat_id, text)
        payload = {"session": self.session, "chatId": chat_id, "text": text}
        return await self._post("/api/sendText", payload)

    async def send_image(self, chat_id: str, url: str, caption: str = "") -> SendResult:
        if not chat_id.endswith(("@c.us", "@g.us")):
            chat_id = f"{chat_id}@c.us"
        payload = {
            "session": self.session,
            "chatId": chat_id,
            "file": {"url": url},
            "caption": caption,
        }
        return await self._post("/api/sendImage", payload)

    async def send_seen(self, chat_id: str) -> None:
        try:
            await self._post("/api/sendSeen", {"session": self.session, "chatId": chat_id})
        except Exception:
            pass

    async def configure_webhook(self, webhook_url: str, secret: str) -> bool:
        from app.core.http_client import get_http_client
        url = f"{self.base}/api/sessions/{self.session}"
        desired = {
            "config": {
                "webhooks": [{
                    "url": webhook_url,
                    "events": ["message", "message.any", "message.reaction", "session.status"],
                    "customHeaders": [{"name": "X-Webhook-Secret", "value": secret}],
                }]
            }
        }
        try:
            resp = await get_http_client().put(url, headers=self._headers, json=desired)
            return resp.is_success
        except Exception:
            return False

    # ── Internals ─────────────────────────────────────────────────────────────

    async def _simulate_typing(self, chat_id: str, text: str) -> None:
        try:
            payload = {"session": self.session, "chatId": chat_id}
            await self._post("/api/sendSeen", payload)
            await self._post("/api/startTyping", payload)
            chars_per_sec = max(settings.waha_typing_chars_per_second, 1.0)
            delay = min(max(len(text) / chars_per_sec + random.uniform(0.1, 0.5),
                            settings.waha_typing_min_seconds),
                        settings.waha_typing_max_seconds)
            await asyncio.sleep(delay)
            await self._post("/api/stopTyping", payload)
        except Exception:
            pass

    async def _post(self, path: str, payload: dict[str, Any]) -> SendResult:
        from app.core.http_client import get_http_client
        url = f"{self.base}{path}"
        try:
            resp = await get_http_client().post(url, json=payload, headers=self._headers)
        except httpx.TimeoutException as exc:
            raise WAHAError("TIMEOUT", "WAHA timeout") from exc
        except httpx.HTTPError as exc:
            raise WAHAError("TRANSPORT", "WAHA transport error") from exc

        if resp.status_code == 401:
            raise WAHAError("AUTH", "WAHA auth failed", 401)
        if not resp.is_success:
            raise WAHAError("API_ERROR", resp.text[:200], resp.status_code)

        try:
            data = resp.json()
        except Exception:
            return SendResult(message_id=None, raw={})

        msg_id = None
        if isinstance(data, dict):
            raw_id = data.get("id")
            if isinstance(raw_id, dict):
                msg_id = raw_id.get("_serialized") or raw_id.get("id")
            elif isinstance(raw_id, str):
                msg_id = raw_id
        return SendResult(message_id=msg_id, raw=data if isinstance(data, dict) else {})
