from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin


class Task(Base, TimestampMixin):
    """Lightweight team task, optionally linked to a chat (Periskope-style Tasks panel)."""

    __tablename__ = "tasks"

    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    title: Mapped[str] = mapped_column(String(500), nullable=False)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(String(20), default="open")  # open|done
    priority: Mapped[str] = mapped_column(String(20), default="low")  # low|medium|high
    due_date: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    chat_id: Mapped[int | None] = mapped_column(ForeignKey("chats.id"), nullable=True)
    assigned_to: Mapped[int | None] = mapped_column(ForeignKey("agents.id"), nullable=True)
    created_by: Mapped[int | None] = mapped_column(ForeignKey("agents.id"), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    reminder_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    reminder_sent: Mapped[bool] = mapped_column(default=False)
    message_id: Mapped[int | None] = mapped_column(ForeignKey("messages.id"), nullable=True)
