from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, JSON, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin


class ScheduledMessage(Base, TimestampMixin):
    """A message scheduled to be sent to a single chat at a future time,
    optionally recurring (daily/weekly)."""

    __tablename__ = "scheduled_messages"

    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    # chat_id references MongoDB chat.id; chat_wid is the WhatsApp identifier used for sending
    chat_id: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    chat_wid: Mapped[str] = mapped_column(String(100), nullable=False, default="")
    phone_id: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    body: Mapped[str] = mapped_column(Text, nullable=False)
    send_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, index=True)
    repeat: Mapped[str] = mapped_column(String(20), default="none")  # none|daily|weekly|monthly
    interval: Mapped[int] = mapped_column(Integer, default=1)        # every N days/weeks/months
    days_of_week: Mapped[list | None] = mapped_column(JSON, nullable=True)  # [0-6], Mon=0 (daily repeat)
    day_of_month: Mapped[int | None] = mapped_column(Integer, nullable=True)  # 1-31 (monthly repeat)
    end_date: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    status: Mapped[str] = mapped_column(String(20), default="pending")  # pending|sent|failed|cancelled
    sent_count: Mapped[int] = mapped_column(Integer, default=0)
    last_error: Mapped[str | None] = mapped_column(String(500), nullable=True)
    created_by: Mapped[int | None] = mapped_column(ForeignKey("agents.id"), nullable=True)
