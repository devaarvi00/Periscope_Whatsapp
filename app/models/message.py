from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, Text, JSON
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class Message(Base):
    __tablename__ = "messages"

    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    chat_id: Mapped[int] = mapped_column(ForeignKey("chats.id"), nullable=False, index=True)
    phone_id: Mapped[int] = mapped_column(ForeignKey("phones.id"), nullable=False)
    message_wid: Mapped[str] = mapped_column(String(200), unique=True, index=True, nullable=False)
    from_me: Mapped[bool] = mapped_column(Boolean, default=False)
    sender_name: Mapped[str] = mapped_column(String(255), default="")
    sender_number: Mapped[str] = mapped_column(String(30), default="")
    sent_by_agent_id: Mapped[int | None] = mapped_column(ForeignKey("agents.id"), nullable=True)
    body: Mapped[str] = mapped_column(Text, default="")
    message_type: Mapped[str] = mapped_column(String(30), default="text")
    has_media: Mapped[bool] = mapped_column(Boolean, default=False)
    media_url: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    is_read: Mapped[bool] = mapped_column(Boolean, default=False)
    is_flagged: Mapped[bool] = mapped_column(Boolean, default=False)
    timestamp: Mapped[datetime] = mapped_column(DateTime, nullable=False, index=True)
    metadata_: Mapped[dict | None] = mapped_column("metadata", JSON, nullable=True)
