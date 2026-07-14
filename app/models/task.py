from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin


class Task(Base, TimestampMixin):
    """Lightweight team task, optionally linked to a chat."""

    __tablename__ = "tasks"

    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    title: Mapped[str] = mapped_column(String(500), nullable=False)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(String(20), default="open")  # open|done
    priority: Mapped[str] = mapped_column(String(20), default="low")  # low|medium|high
    due_date: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    # chat_id references MongoDB chat.id — no FK since chats live in MongoDB
    chat_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    # message_wid is the WAHA message ID string (replaced message_id FK to MySQL messages)
    message_wid: Mapped[str | None] = mapped_column(String(200), nullable=True)
    assigned_to: Mapped[int | None] = mapped_column(ForeignKey("agents.id"), nullable=True)
    created_by: Mapped[int | None] = mapped_column(ForeignKey("agents.id"), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    reminder_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    reminder_sent: Mapped[bool] = mapped_column(default=False)
