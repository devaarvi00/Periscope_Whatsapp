import logging
from datetime import datetime
from typing import Any

from sqlalchemy import desc, func, select
from sqlalchemy.orm import Session

from app.models.chat import Chat, ChatLabel
from app.models.label import Label
from app.models.message import Message
from app.models.phone import Phone

logger = logging.getLogger(__name__)


class InboxService:
    def __init__(self, db: Session) -> None:
        self.db = db

    def list_chats(
        self,
        phone_id: int | None = None,
        is_archived: bool = False,
        is_flagged: bool | None = None,
        label_id: int | None = None,
        search: str | None = None,
        assigned_to: int | None = None,
        limit: int = 50,
        offset: int = 0,
        phone_ids: list[int] | None = None,
        is_group: bool | None = None,
    ) -> list[Chat]:
        q = self.db.query(Chat).filter(Chat.is_archived == is_archived)
        if phone_id:
            q = q.filter(Chat.phone_id == phone_id)
        if phone_ids is not None:
            q = q.filter(Chat.phone_id.in_(phone_ids or [0]))
        if is_group is not None:
            q = q.filter(Chat.is_group == is_group)
        if is_flagged is not None:
            q = q.filter(Chat.is_flagged == is_flagged)
        if assigned_to is not None:
            q = q.filter(Chat.assigned_to == assigned_to)
        if label_id is not None:
            q = q.join(ChatLabel, Chat.id == ChatLabel.chat_id).filter(ChatLabel.label_id == label_id)
        if search:
            q = q.filter(Chat.name.ilike(f"%{search}%"))
        return q.order_by(desc(Chat.last_message_at)).offset(offset).limit(limit).all()

    def get_chat(self, chat_id: int) -> Chat | None:
        return self.db.query(Chat).filter(Chat.id == chat_id).first()

    def get_chat_by_wid(self, chat_wid: str, phone_id: int | None = None) -> Chat | None:
        q = self.db.query(Chat).filter(Chat.chat_wid == chat_wid)
        if phone_id is not None:
            q = q.filter(Chat.phone_id == phone_id)
        return q.first()

    def upsert_chat(self, data: dict[str, Any]) -> Chat:
        chat_wid = data["chat_wid"]
        phone_id = data.get("phone_id")
        chat = self.get_chat_by_wid(chat_wid, phone_id)
        if not chat:
            chat = Chat(**data)
            self.db.add(chat)
        else:
            for k, v in data.items():
                if hasattr(chat, k):
                    setattr(chat, k, v)
        self.db.commit()
        self.db.refresh(chat)
        return chat

    def update_chat(self, chat_id: int, **kwargs: Any) -> Chat | None:
        chat = self.get_chat(chat_id)
        if not chat:
            return None
        for k, v in kwargs.items():
            if hasattr(chat, k) and v is not None:
                setattr(chat, k, v)
        self.db.commit()
        self.db.refresh(chat)
        return chat

    def get_messages(self, chat_id: int, limit: int = 50,
                     before_id: int | None = None,
                     before_ts: str | None = None) -> list[Message]:
        q = self.db.query(Message).filter(Message.chat_id == chat_id)
        if before_ts:
            # Timestamp cursor: correct even for historically-synced messages that
            # arrive with new high IDs but old timestamps (id-based cursor misses them)
            try:
                ts = datetime.fromisoformat(before_ts.replace("Z", "").split(".")[0])
                q = q.filter(Message.timestamp < ts)
            except ValueError:
                pass
        elif before_id:
            # Legacy fallback: find the pivot message and use its timestamp
            pivot = self.db.query(Message.timestamp).filter(
                Message.id == before_id, Message.chat_id == chat_id
            ).scalar()
            if pivot:
                q = q.filter(Message.timestamp < pivot)
        return q.order_by(desc(Message.timestamp)).limit(limit).all()

    def upsert_message(self, data: dict[str, Any]) -> Message:
        from sqlalchemy.exc import IntegrityError
        existing = self.db.query(Message).filter(
            Message.message_wid == data["message_wid"]
        ).first()
        if existing:
            return existing
        try:
            with self.db.begin_nested():
                msg = Message(**data)
                self.db.add(msg)
            self.db.commit()
            self.db.refresh(msg)
            self._update_chat_last_message(
                data["chat_id"], data.get("body", ""),
                data.get("timestamp"), data.get("message_type", "text"),
            )
            return msg
        except IntegrityError:
            existing = self.db.query(Message).filter(
                Message.message_wid == data["message_wid"]
            ).first()
            if existing:
                return existing
            raise

    def _update_chat_last_message(self, chat_id: int, body: str, ts: datetime | None,
                                   message_type: str = "text") -> None:
        chat = self.get_chat(chat_id)
        if chat:
            if body:
                chat.last_message = body[:200]
            else:
                _type_labels = {
                    "image": "📷 Photo", "photo": "📷 Photo",
                    "video": "🎬 Video",
                    "audio": "🎤 Voice message", "voice": "🎤 Voice message", "ptt": "🎤 Voice message",
                    "document": "📄 Document", "pdf": "📄 Document",
                    "sticker": "🖼 Sticker", "location": "📍 Location",
                    "contact": "👤 Contact", "vcard": "👤 Contact",
                }
                chat.last_message = _type_labels.get(message_type, "📎 Media")
            if ts:
                chat.last_message_at = ts
            self.db.commit()

    def mark_chat_read(self, chat_id: int) -> None:
        self.db.query(Message).filter(
            Message.chat_id == chat_id, Message.is_read == False
        ).update({"is_read": True})
        self.db.query(Chat).filter(Chat.id == chat_id).update({"unread_count": 0})
        self.db.commit()

    def add_label_to_chat(self, chat_id: int, label_id: int) -> None:
        existing = self.db.query(ChatLabel).filter(
            ChatLabel.chat_id == chat_id, ChatLabel.label_id == label_id
        ).first()
        if not existing:
            self.db.add(ChatLabel(chat_id=chat_id, label_id=label_id))
            self.db.commit()

    def remove_label_from_chat(self, chat_id: int, label_id: int) -> None:
        self.db.query(ChatLabel).filter(
            ChatLabel.chat_id == chat_id, ChatLabel.label_id == label_id
        ).delete()
        self.db.commit()

    def get_chat_label_ids(self, chat_id: int) -> list[int]:
        rows = self.db.query(ChatLabel.label_id).filter(ChatLabel.chat_id == chat_id).all()
        return [r[0] for r in rows]

    def bulk_update_chats(self, chat_ids: list[int], **kwargs: Any) -> int:
        updated = self.db.query(Chat).filter(Chat.id.in_(chat_ids)).update(kwargs)
        self.db.commit()
        return updated
