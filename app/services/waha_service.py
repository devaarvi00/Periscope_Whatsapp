import asyncio
import logging
import random
from dataclasses import dataclass
from typing import Any

import httpx

from app.core.config import settings

logger = logging.getLogger(__name__)

# Events our webhook subscribes to. group.v2.participants feeds the group
# analytics (members joined / exited); reactions feed tickets + analytics.
WEBHOOK_EVENTS = ["message.any", "message.reaction", "group.v2.participants", "session.status"]


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
    def __init__(self, session_name: str = "",
                 base_url: str | None = None,
                 api_key: str | None = None) -> None:
        self.base = (base_url or settings.waha_base_url).rstrip("/")
        self.session = session_name or settings.waha_session_name
        self._headers = {
            "X-Api-Key": api_key or settings.waha_api_key,
            "Content-Type": "application/json",
            "Accept": "application/json",
        }

    @classmethod
    def from_phone(cls, phone: object) -> "WAHAService":
        """Build WAHAService using a Phone model's own WAHA URL/key.

        Falls back to the global settings, but the global API key is only ever
        sent to the global WAHA base URL — a phone pointing at a different
        server must carry its own key, otherwise requests go out keyless.
        """
        global_base = (settings.waha_base_url or "").strip().rstrip("/")
        phone_base = (getattr(phone, "waha_base_url", None) or "").strip().rstrip("/")
        phone_key = getattr(phone, "waha_api_key", None) or None
        svc = cls(
            session_name=getattr(phone, "session_name", ""),
            base_url=phone_base or None,
            api_key=phone_key,
        )
        if phone_base and phone_base.lower() != global_base.lower() and not phone_key:
            svc._headers.pop("X-Api-Key", None)
        return svc

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

    async def ensure_session_exists(self, webhook_url: str = "", webhook_secret: str = "") -> bool:
        """Create the WAHA session if it doesn't exist yet. Returns True if ready."""
        from app.core.http_client import get_http_client
        # Check if session already exists
        status_url = f"{self.base}/api/sessions/{self.session}"
        resp = await get_http_client().get(status_url, headers=self._headers)
        if resp.is_success:
            return True  # already exists
        # Create it
        payload: dict = {"name": self.session}
        if webhook_url:
            payload["config"] = {
                "webhooks": [{
                    "url": webhook_url,
                    "events": WEBHOOK_EVENTS,
                    "customHeaders": [{"name": "X-Webhook-Secret", "value": webhook_secret}],
                }]
            }
        create_url = f"{self.base}/api/sessions"
        resp = await get_http_client().post(create_url, headers=self._headers, json=payload)
        return resp.is_success or resp.status_code == 409  # 409 = already exists, fine

    async def start_session(self) -> bool:
        from app.core.http_client import get_http_client
        url = f"{self.base}/api/sessions/{self.session}/start"
        resp = await get_http_client().post(url, headers=self._headers, json={})
        return resp.is_success

    async def delete_waha_session(self) -> bool:
        """Stop and permanently delete the WAHA session."""
        from app.core.http_client import get_http_client
        await self.stop_session()
        url = f"{self.base}/api/sessions/{self.session}"
        try:
            resp = await get_http_client().delete(url, headers=self._headers)
            return resp.is_success or resp.status_code == 404
        except Exception:
            return False

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

    async def logout_session(self) -> bool:
        """Logout from WhatsApp and clear saved auth — next start will require QR scan."""
        from app.core.http_client import get_http_client
        await self.stop_session()
        url = f"{self.base}/api/sessions/{self.session}/logout"
        try:
            resp = await get_http_client().post(url, headers=self._headers, json={})
            return resp.is_success
        except Exception:
            return False

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

    async def get_message(self, chat_id: str, message_id: str, download_media: bool = True) -> dict[str, Any]:
        """One message by id; with download_media WAHA returns `media.url`."""
        from urllib.parse import quote
        from app.core.http_client import get_http_client
        url = f"{self.base}/api/{self.session}/chats/{quote(chat_id, safe='@.')}/messages/{quote(message_id, safe='')}"
        try:
            resp = await get_http_client().get(
                url, headers=self._headers,
                params={"downloadMedia": "true" if download_media else "false"},
            )
            if resp.is_success:
                data = resp.json()
                return data if isinstance(data, dict) else {}
        except Exception as exc:
            logger.warning("WAHA get_message error: %s", exc)
        return {}

    async def get_chat_picture(self, chat_id: str) -> str | None:
        """Profile / group picture URL (WhatsApp CDN), or None when hidden."""
        from urllib.parse import quote
        from app.core.http_client import get_http_client
        url = f"{self.base}/api/{self.session}/chats/{quote(chat_id, safe='@.')}/picture"
        try:
            resp = await get_http_client().get(url, headers=self._headers)
            if resp.is_success:
                data = resp.json()
                return (data or {}).get("url") if isinstance(data, dict) else None
        except Exception as exc:
            logger.warning("WAHA get_chat_picture error: %s", exc)
        return None

    def files_path(self, media_url: str) -> str | None:
        """Path of a WAHA-served media file (`/api/files/...`), else None.

        WAHA reports its own public base URL in media.url, which may differ
        from the address we reach it on — only the path is trusted and it is
        always fetched from this phone's configured WAHA base.
        """
        from urllib.parse import urlsplit
        try:
            path = urlsplit(media_url or "").path
        except ValueError:
            return None
        if not path.startswith("/api/files/") or ".." in path:
            return None
        return path

    async def fetch_file(self, path: str, max_bytes: int = 64 * 1024 * 1024) -> tuple[bytes, str] | None:
        """Download a WAHA media file by path. Returns (content, content_type)."""
        from app.core.http_client import get_http_client
        headers = {k: v for k, v in self._headers.items() if k != "Content-Type"}
        headers["Accept"] = "*/*"
        try:
            resp = await get_http_client().get(f"{self.base}{path}", headers=headers)
        except Exception as exc:
            logger.warning("WAHA fetch_file error: %s", exc)
            return None
        if not resp.is_success or len(resp.content) > max_bytes:
            return None
        return resp.content, resp.headers.get("content-type", "application/octet-stream")

    # ── Groups ────────────────────────────────────────────────────────────────

    async def get_group_participants_with_status(self, group_id: str) -> tuple[list[dict[str, Any]], bool]:
        """Returns (participants_list, api_available). api_available=False when WAHA returned an error."""
        from app.core.http_client import get_http_client
        url = f"{self.base}/api/{self.session}/groups/{group_id}/participants"
        try:
            resp = await get_http_client().get(url, headers=self._headers)
            if resp.is_success:
                data = resp.json()
                participants = data if isinstance(data, list) else data.get("participants", [])
                return participants, True
            else:
                logger.warning("WAHA get_group_participants returned %s for %s", resp.status_code, group_id)
                return [], False
        except Exception as exc:
            logger.warning("WAHA get_group_participants error: %s", exc)
            return [], False

    async def add_group_participants(self, group_id: str, participants: list[str]) -> bool:
        """Add phone numbers (as WIDs) to a WhatsApp group."""
        from app.core.http_client import get_http_client
        url = f"{self.base}/api/{self.session}/groups/{group_id}/participants/add"
        payload = {"participants": [{"id": p} for p in participants]}
        try:
            resp = await get_http_client().post(url, headers=self._headers, json=payload)
            return resp.is_success
        except Exception as exc:
            logger.warning("WAHA add_group_participants error: %s", exc)
            return False

    async def get_group_info(self, group_id: str) -> dict[str, Any]:
        from app.core.http_client import get_http_client
        url = f"{self.base}/api/{self.session}/groups/{self._gid(group_id)}"
        try:
            resp = await get_http_client().get(url, headers=self._headers)
            if resp.is_success:
                data = resp.json()
                return data if isinstance(data, dict) else {}
        except Exception as exc:
            logger.warning("WAHA get_group_info error: %s", exc)
        return {}

    async def get_group_participants_v2(self, group_id: str) -> list[dict[str, Any]] | None:
        """`[{id, pn, role}]` — role is participant/admin/superadmin and `pn`
        carries the phone number even in LID-addressed groups. None on error."""
        from app.core.http_client import get_http_client
        url = f"{self.base}/api/{self.session}/groups/{self._gid(group_id)}/participants/v2"
        try:
            resp = await get_http_client().get(url, headers=self._headers)
            if resp.is_success:
                data = resp.json()
                return data if isinstance(data, list) else None
            logger.warning("WAHA participants/v2 returned %s for %s", resp.status_code, group_id)
        except Exception as exc:
            logger.warning("WAHA participants/v2 error: %s", exc)
        return None

    async def get_contact_picture(self, contact_id: str) -> str | None:
        """Profile picture URL of any contact (WhatsApp CDN), None when hidden."""
        from app.core.http_client import get_http_client
        url = f"{self.base}/api/contacts/profile-picture"
        try:
            resp = await get_http_client().get(
                url, headers=self._headers,
                params={"contactId": contact_id, "session": self.session},
            )
            if resp.is_success:
                data = resp.json()
                return (data or {}).get("profilePictureURL") if isinstance(data, dict) else None
        except Exception as exc:
            logger.warning("WAHA get_contact_picture error: %s", exc)
        return None

    # Group administration — each raises WAHAError when WhatsApp refuses
    # (e.g. our number is not an admin of the group).
    _PARTICIPANT_ACTIONS = {
        "add": "participants/add", "remove": "participants/remove",
        "promote": "admin/promote", "demote": "admin/demote",
    }

    async def group_participants_action(self, group_id: str, action: str, ids: list[str]) -> dict[str, Any]:
        path = self._PARTICIPANT_ACTIONS[action]
        res = await self._request(
            "POST", f"/api/{self.session}/groups/{self._gid(group_id)}/{path}",
            {"participants": [{"id": i} for i in ids]},
        )
        return res if isinstance(res, dict) else {"result": res}

    async def get_group_invite_code(self, group_id: str) -> str:
        res = await self._request("GET", f"/api/{self.session}/groups/{self._gid(group_id)}/invite-code")
        if isinstance(res, dict):
            res = res.get("code") or res.get("inviteCode") or ""
        return str(res or "").strip().strip('"')

    async def set_group_subject(self, group_id: str, subject: str) -> None:
        await self._request("PUT", f"/api/{self.session}/groups/{self._gid(group_id)}/subject", {"subject": subject})

    async def set_group_description(self, group_id: str, description: str) -> None:
        await self._request("PUT", f"/api/{self.session}/groups/{self._gid(group_id)}/description",
                            {"description": description})

    async def set_group_admin_only(self, group_id: str, setting: str, admins_only: bool) -> None:
        """setting: 'messages' (who can send) or 'info' (who can edit group info)."""
        path = {"messages": "messages-admin-only", "info": "info-admin-only"}[setting]
        await self._request("PUT", f"/api/{self.session}/groups/{self._gid(group_id)}/settings/security/{path}",
                            {"adminsOnly": bool(admins_only)})

    @staticmethod
    def _gid(group_id: str) -> str:
        from urllib.parse import quote
        return quote(group_id, safe="@.")

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

    async def send_file(self, chat_id: str, url: str, filename: str = "", caption: str = "") -> SendResult:
        if not chat_id.endswith(("@c.us", "@g.us")):
            chat_id = f"{chat_id}@c.us"
        payload = {
            "session": self.session,
            "chatId": chat_id,
            "file": {"url": url, "filename": filename or url.rsplit("/", 1)[-1]},
            "caption": caption,
        }
        return await self._post("/api/sendFile", payload)

    async def send_poll(self, chat_id: str, question: str, options: list[str],
                        multiple_answers: bool = False) -> SendResult:
        if not chat_id.endswith(("@c.us", "@g.us")):
            chat_id = f"{chat_id}@c.us"
        payload = {
            "session": self.session,
            "chatId": chat_id,
            "poll": {
                "name": question,
                "options": options,
                "multipleAnswers": multiple_answers,
            },
        }
        return await self._post("/api/sendPoll", payload)

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
                    "events": WEBHOOK_EVENTS,
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

    async def _request(self, method: str, path: str, payload: dict[str, Any] | None = None) -> Any:
        """JSON request that raises WAHAError on failure; returns the parsed body."""
        from app.core.http_client import get_http_client
        try:
            resp = await get_http_client().request(
                method, f"{self.base}{path}", headers=self._headers,
                json=payload if payload is not None else None,
            )
        except httpx.TimeoutException as exc:
            raise WAHAError("TIMEOUT", "WhatsApp API timed out") from exc
        except httpx.HTTPError as exc:
            raise WAHAError("TRANSPORT", "WhatsApp API unreachable") from exc
        if resp.status_code == 401:
            raise WAHAError("AUTH", "WhatsApp API auth failed", 401)
        if not resp.is_success:
            detail = ""
            try:
                body = resp.json()
                detail = str(body.get("message") or body.get("error") or "") if isinstance(body, dict) else ""
            except Exception:
                detail = resp.text[:200]
            raise WAHAError("API_ERROR", detail[:200] or f"WhatsApp API error {resp.status_code}", resp.status_code)
        if not resp.content:
            return None
        try:
            return resp.json()
        except Exception:
            return resp.text

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
