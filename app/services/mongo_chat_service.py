"""MongoDB-backed chat and message CRUD.

Design:
- Each chat document is keyed by (phone_id, chat_wid) — unique per phone number.
- Each message document is keyed by (phone_id, message_wid) — WAHA's native ID
  is only unique within one session.
- Both use an auto-increment integer `id` field so the REST API surface is
  unchanged (clients never see ObjectIds).
- Labels are stored as `label_ids: list[int]` inside the chat document.
- When a phone reconnects the same number, upsert leaves existing metadata
  (labels, AI state, flags) intact and only refreshes WAHA-sourced fields.
"""
from __future__ import annotations

import logging
import re
from datetime import datetime
from typing import Any

from pymongo.errors import DuplicateKeyError

from app.db.mongo import get_mongo_db, next_id

logger = logging.getLogger(__name__)

def _serialize_chat(doc: dict) -> dict:
    """Convert a raw MongoDB chat document into a clean API dict."""
    if not doc:
        return {}
    lma = doc.get("last_message_at")
    return {
        "id": doc["id"],
        "chat_wid": doc["chat_wid"],
        "phone_id": doc["phone_id"],
        "name": doc.get("name") or "",
        "is_group": bool(doc.get("is_group")),
        "last_message": doc.get("last_message") or "",
        "last_message_at": lma.isoformat() if isinstance(lma, datetime) else lma,
        "unread_count": doc.get("unread_count") or 0,
        "is_flagged": bool(doc.get("is_flagged")),
        "is_archived": bool(doc.get("is_archived")),
        "is_pinned": bool(doc.get("is_pinned")),
        "ai_active": bool(doc.get("ai_active")),
        "ai_state": doc.get("ai_state") or "INACTIVE",
        "ai_snoozed_at": doc.get("ai_snoozed_at"),
        "assigned_to": doc.get("assigned_to"),
        "status": doc.get("status") or "open",
        "last_message_from_me": doc.get("last_message_from_me"),
        "labels": doc.get("label_ids") or [],
    }


from app.core.message_labels import _MEDIA_LABELS, _SYSTEM_LABELS


def _body_for_display(doc: dict) -> str:
    """Return a human-readable body string — never empty — from a message document."""
    body = doc.get("body") or ""
    if body:
        return body
    msg_type = (doc.get("message_type") or "text").lower()
    if doc.get("has_media") or msg_type in _MEDIA_LABELS:
        return _MEDIA_LABELS.get(msg_type, "📎 Media")
    return _SYSTEM_LABELS.get(msg_type, "")


def _serialize_message(doc: dict) -> dict:
    if not doc:
        return {}
    ts = doc.get("timestamp")
    return {
        "id": doc["id"],
        "chat_id": doc.get("chat_id"),
        "chat_wid": doc.get("chat_wid") or "",
        "phone_id": doc.get("phone_id"),
        "message_wid": doc.get("message_wid") or "",
        "from_me": bool(doc.get("from_me")),
        "sender_name": doc.get("sender_name") or "",
        "sender_number": doc.get("sender_number") or "",
        "sent_by_agent_id": doc.get("sent_by_agent_id"),
        "body": _body_for_display(doc),
        "message_type": doc.get("message_type") or "text",
        "has_media": bool(doc.get("has_media")),
        "media_url": doc.get("media_url"),
        "is_read": bool(doc.get("is_read")),
        "is_flagged": bool(doc.get("is_flagged")),
        "timestamp": ts.isoformat() if isinstance(ts, datetime) else (ts or ""),
    }


class MongoInboxService:
    """Async service for chat and message operations backed by MongoDB."""

    def __init__(self) -> None:
        self.db = get_mongo_db()

    # ── Chats ──────────────────────────────────────────────────────────── #

    async def get_chat_by_id(self, chat_id: int) -> dict | None:
        return await self.db.chats.find_one({"id": chat_id})

    async def get_chat_by_wid(self, chat_wid: str, phone_id: int) -> dict | None:
        return await self.db.chats.find_one({"phone_id": phone_id, "chat_wid": chat_wid})

    @staticmethod
    def _is_placeholder_name(name: str, chat_wid: str) -> bool:
        """True for names that are just the raw number / WID (no real name)."""
        if not name:
            return True
        raw_number = chat_wid.split("@")[0]
        return (
            name in (chat_wid, raw_number, f"Group {raw_number[-6:]}")
            or "@" in name
        )

    async def upsert_chat(self, data: dict[str, Any]) -> dict:
        """Atomically insert or update a chat.  Returns the full document after write.

        Existing chats only get WAHA-sourced fields refreshed; user-set metadata
        (labels, flags, assignment, AI state) is preserved.  A placeholder name
        (bare number / WID) is only written on insert so it never clobbers a
        real contact name.
        """
        from pymongo import ReturnDocument

        phone_id: int = data["phone_id"]
        chat_wid: str = data["chat_wid"]
        filt = {"phone_id": phone_id, "chat_wid": chat_wid}
        now = datetime.utcnow()

        set_fields: dict = {"updated_at": now}
        for f in ("is_group", "last_message", "last_message_at"):
            if f in data:
                set_fields[f] = data[f]
        # Allow caller to explicitly set metadata fields (e.g. from webhook)
        for f in ("ai_active", "ai_state", "unread_count"):
            if f in data:
                set_fields[f] = data[f]
        name = str(data.get("name") or "")
        if name and not self._is_placeholder_name(name, chat_wid):
            set_fields["name"] = name

        on_insert: dict = {
            "phone_id": phone_id,
            "chat_wid": chat_wid,
            "name": name,
            "is_group": bool(data.get("is_group")),
            "last_message": data.get("last_message") or "",
            "last_message_at": data.get("last_message_at"),
            "unread_count": data.get("unread_count", 0),
            "is_flagged": bool(data.get("is_flagged")),
            "is_archived": False,
            "is_pinned": False,
            "ai_active": bool(data.get("ai_active")),
            "ai_state": data.get("ai_state") or "INACTIVE",
            "ai_snoozed_at": None,
            "assigned_to": data.get("assigned_to"),
            "status": "open",
            "label_ids": [],
            "created_at": now,
        }
        on_insert = {k: v for k, v in on_insert.items() if k not in set_fields and k not in filt}

        # Fast path: chat already exists
        doc = await self.db.chats.find_one_and_update(
            filt, {"$set": set_fields}, return_document=ReturnDocument.AFTER,
        )
        if doc:
            return doc

        # Insert path. The id is only consumed if this call actually inserts;
        # a concurrent insert makes $setOnInsert a no-op (wasted counter value).
        on_insert["id"] = await next_id("chats")
        try:
            doc = await self.db.chats.find_one_and_update(
                filt,
                {"$set": set_fields, "$setOnInsert": on_insert},
                upsert=True,
                return_document=ReturnDocument.AFTER,
            )
        except DuplicateKeyError:
            # Lost a simultaneous upsert race — the other writer inserted it.
            doc = await self.db.chats.find_one_and_update(
                filt, {"$set": set_fields}, return_document=ReturnDocument.AFTER,
            )
        return doc

    async def update_chat(self, chat_id: int, **kwargs: Any) -> dict | None:
        allowed = {
            "name", "is_archived", "is_pinned", "is_flagged",
            "ai_active", "ai_state", "ai_snoozed_at",
            "assigned_to", "unread_count", "last_message", "last_message_at",
            "custom_properties", "status",
        }
        # None is a real value for nullable fields (unassign, clear snooze);
        # for the rest it means "leave unchanged".
        nullable = {"assigned_to", "ai_snoozed_at"}
        updates = {
            k: v for k, v in kwargs.items()
            if k in allowed and (v is not None or k in nullable)
        }
        if not updates:
            return await self.get_chat_by_id(chat_id)
        updates["updated_at"] = datetime.utcnow()
        await self.db.chats.update_one({"id": chat_id}, {"$set": updates})
        return await self.get_chat_by_id(chat_id)

    async def list_chats(
        self,
        phone_ids: list[int] | None = None,
        phone_id: int | None = None,
        is_archived: bool = False,
        is_flagged: bool | None = None,
        label_id: int | None = None,
        search: str | None = None,
        assigned_to: int | None = None,
        is_group: bool | None = None,
        limit: int = 200,
        offset: int = 0,
        status: str | None = None,
    ) -> list[dict]:
        filt: dict = {"is_archived": is_archived}

        if phone_id is not None:
            filt["phone_id"] = phone_id
        elif phone_ids is not None:
            filt["phone_id"] = {"$in": phone_ids}

        if is_flagged is not None:
            filt["is_flagged"] = is_flagged
        if label_id is not None:
            filt["label_ids"] = label_id
        if assigned_to is not None:
            filt["assigned_to"] = assigned_to
        if is_group is not None:
            filt["is_group"] = is_group
        if status == "resolved":
            filt["status"] = "resolved"
        elif status == "open":
            filt["status"] = {"$ne": "resolved"}  # legacy chats have no status
        if search:
            filt["name"] = {"$regex": re.escape(search), "$options": "i"}

        cursor = (
            self.db.chats.find(filt)
            .sort("last_message_at", -1)
            .skip(offset)
            .limit(limit)
        )
        return await cursor.to_list(length=limit)

    async def mark_chat_read(self, chat_id: int) -> None:
        await self.db.chats.update_one(
            {"id": chat_id},
            {"$set": {"unread_count": 0, "updated_at": datetime.utcnow()}},
        )
        await self.db.messages.update_many(
            {"chat_id": chat_id, "is_read": False},
            {"$set": {"is_read": True}},
        )

    async def bulk_update_chats(self, chat_ids: list[int], **kwargs: Any) -> int:
        allowed = {"is_archived", "is_pinned", "ai_active", "ai_state", "is_flagged", "unread_count", "status"}
        updates = {k: v for k, v in kwargs.items() if k in allowed}
        if not updates:
            return 0
        updates["updated_at"] = datetime.utcnow()
        result = await self.db.chats.update_many({"id": {"$in": chat_ids}}, {"$set": updates})
        return result.modified_count

    # ── Labels (embedded in chat document) ─────────────────────────────── #

    async def add_label_to_chat(self, chat_id: int, label_id: int) -> None:
        await self.db.chats.update_one(
            {"id": chat_id},
            {"$addToSet": {"label_ids": label_id}, "$set": {"updated_at": datetime.utcnow()}},
        )

    async def remove_label_from_chat(self, chat_id: int, label_id: int) -> None:
        await self.db.chats.update_one(
            {"id": chat_id},
            {"$pull": {"label_ids": label_id}, "$set": {"updated_at": datetime.utcnow()}},
        )

    # ── Messages ────────────────────────────────────────────────────────── #

    async def upsert_message(self, data: dict[str, Any]) -> dict:
        """Insert a message once per (phone_id, message_wid). Returns the stored doc."""
        msg_wid = data["message_wid"]
        key = {"phone_id": data["phone_id"], "message_wid": msg_wid}
        existing = await self.db.messages.find_one(key)
        if existing:
            return existing

        new_id = await next_id("messages")
        ts = data.get("timestamp") or datetime.utcnow()
        doc: dict = {
            "id": new_id,
            "phone_id": data["phone_id"],
            "chat_id": data.get("chat_id"),
            "chat_wid": data.get("chat_wid") or "",
            "message_wid": msg_wid,
            "from_me": bool(data.get("from_me")),
            "sender_name": data.get("sender_name") or "",
            "sender_number": data.get("sender_number") or "",
            "sent_by_agent_id": data.get("sent_by_agent_id"),
            "body": data.get("body") or "",
            "message_type": data.get("message_type") or "text",
            "has_media": bool(data.get("has_media")),
            "media_url": data.get("media_url"),
            "is_read": False,
            "is_flagged": False,
            "timestamp": ts,
            "created_at": datetime.utcnow(),
        }
        try:
            await self.db.messages.insert_one(doc)
        except DuplicateKeyError:
            # Concurrent insert — return the existing one
            existing = await self.db.messages.find_one(key)
            return existing or doc

        # Keep last_message_at on the parent chat in sync
        await self._update_chat_last_message(
            data.get("chat_id"),
            data.get("chat_wid") or "",
            data.get("phone_id"),
            data.get("body") or "",
            data.get("message_type") or "text",
            ts,
            bool(data.get("from_me")),
        )
        return doc

    async def _update_chat_last_message(
        self,
        chat_id: int | None,
        chat_wid: str,
        phone_id: int | None,
        body: str,
        message_type: str,
        ts: datetime,
        from_me: bool | None = None,
    ) -> None:
        preview = body[:200] if body else _MEDIA_LABELS.get(message_type, "📎 Media")
        filt: dict = {"id": chat_id} if chat_id else {"chat_wid": chat_wid, "phone_id": phone_id}
        # Never let an older (back-filled) message replace a newer preview
        filt["$or"] = [
            {"last_message_at": None},  # also matches a missing field
            {"last_message_at": {"$lte": ts}},
        ]
        fields: dict = {"last_message": preview, "last_message_at": ts, "updated_at": datetime.utcnow()}
        if from_me is not None:
            fields["last_message_from_me"] = from_me
        await self.db.chats.update_one(filt, {"$set": fields})

    async def get_messages(
        self,
        chat_id: int | None = None,
        chat_wid: str | None = None,
        phone_id: int | None = None,
        limit: int = 50,
        before_id: int | None = None,
        before_ts: str | None = None,
    ) -> list[dict]:
        if chat_id is not None:
            filt: dict = {"chat_id": chat_id}
        elif chat_wid and phone_id is not None:
            filt = {"chat_wid": chat_wid, "phone_id": phone_id}
        else:
            return []

        if before_id:
            pivot = await self.db.messages.find_one({"id": before_id}, {"timestamp": 1, "id": 1})
            if pivot:
                pivot_ts = pivot["timestamp"]
                filt["$or"] = [
                    {"timestamp": {"$lt": pivot_ts}},
                    {"timestamp": pivot_ts, "id": {"$lt": before_id}},
                ]
        elif before_ts:
            try:
                ts = datetime.fromisoformat(before_ts.replace("Z", "").split(".")[0])
                filt["timestamp"] = {"$lt": ts}
            except ValueError:
                pass

        docs = await (
            self.db.messages.find(filt)
            .sort([("timestamp", -1), ("id", -1)])
            .limit(limit)
            .to_list(length=limit)
        )
        return list(reversed(docs))

    # WAHA message IDs are only unique per session, so every lookup by
    # message_wid is scoped to a phone.

    async def message_exists(self, message_wid: str, phone_id: int) -> bool:
        doc = await self.db.messages.find_one(
            {"phone_id": phone_id, "message_wid": message_wid}, {"_id": 1}
        )
        return doc is not None

    async def get_message_by_wid(self, message_wid: str, phone_id: int) -> dict | None:
        return await self.db.messages.find_one({"phone_id": phone_id, "message_wid": message_wid})

    async def flag_message(self, message_wid: str, phone_id: int, flagged: bool = True) -> None:
        await self.db.messages.update_one(
            {"phone_id": phone_id, "message_wid": message_wid}, {"$set": {"is_flagged": flagged}}
        )

    # ── Cleanup ─────────────────────────────────────────────────────────── #

    async def delete_phone_data(self, phone_id: int) -> None:
        """Remove all chats and messages for a phone — called on Clear Data."""
        await self.db.messages.delete_many({"phone_id": phone_id})
        await self.db.chats.delete_many({"phone_id": phone_id})
