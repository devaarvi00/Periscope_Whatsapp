from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin


class ScheduledMessage(Base, TimestampMixin):
    """A message scheduled to be sent to a single chat at a future time,
    optionally recurring (daily/weekly)."""

    __tablename__ = "scheduled_messages"

    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    chat_id: Mapped[int] = mapped_column(ForeignKey("chats.id"), nullable=False, index=True)
    body: Mapped[str] = mapped_column(Text, nullable=False)
    send_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, index=True)
    repeat: Mapped[str] = mapped_column(String(20), default="none")  # none|daily|weekly
    status: Mapped[str] = mapped_column(String(20), default="pending")  # pending|sent|failed|cancelled
    sent_count: Mapped[int] = mapped_column(Integer, default=0)
    last_error: Mapped[str | None] = mapped_column(String(500), nullable=True)
    created_by: Mapped[int | None] = mapped_column(ForeignKey("agents.id"), nullable=True)
