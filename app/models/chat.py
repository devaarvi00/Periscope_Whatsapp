from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, JSON, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin


class Chat(Base, TimestampMixin):
    __tablename__ = "chats"

    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    chat_wid: Mapped[str] = mapped_column(String(100), unique=True, index=True, nullable=False)
    phone_id: Mapped[int] = mapped_column(ForeignKey("phones.id"), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(500), nullable=False, default="")
    is_group: Mapped[bool] = mapped_column(Boolean, default=False)
    last_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    last_message_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    unread_count: Mapped[int] = mapped_column(Integer, default=0)
    is_flagged: Mapped[bool] = mapped_column(Boolean, default=False)
    is_archived: Mapped[bool] = mapped_column(Boolean, default=False)
    is_pinned: Mapped[bool] = mapped_column(Boolean, default=False)
    is_muted: Mapped[bool] = mapped_column(Boolean, default=False)
    ai_active: Mapped[bool] = mapped_column(Boolean, default=False)
    ai_state: Mapped[str] = mapped_column(String(20), default="INACTIVE")
    assigned_to: Mapped[int | None] = mapped_column(ForeignKey("agents.id"), nullable=True)
    contact_id: Mapped[int | None] = mapped_column(ForeignKey("contacts.id"), nullable=True)
    custom_properties: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    ai_snoozed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class ChatLabel(Base):
    __tablename__ = "chat_labels"

    chat_id: Mapped[int] = mapped_column(ForeignKey("chats.id"), primary_key=True)
    label_id: Mapped[int] = mapped_column(ForeignKey("labels.id"), primary_key=True)
