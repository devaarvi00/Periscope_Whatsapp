from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, JSON, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin


class BulkMessageJob(Base, TimestampMixin):
    __tablename__ = "bulk_message_jobs"

    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    message: Mapped[str] = mapped_column(Text, nullable=False)
    phone_id: Mapped[int] = mapped_column(ForeignKey("phones.id"), nullable=False)
    recipient_chat_ids: Mapped[list | None] = mapped_column(JSON, nullable=True)
    scheduled_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    status: Mapped[str] = mapped_column(String(20), default="pending")  # pending|running|done|failed|cancelled
    message_type: Mapped[str] = mapped_column(String(20), default="text")  # text|image|file|poll
    media_url: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    poll_options: Mapped[list | None] = mapped_column(JSON, nullable=True)
    error_message: Mapped[str | None] = mapped_column(String(500), nullable=True)
    sent_count: Mapped[int] = mapped_column(Integer, default=0)
    failed_count: Mapped[int] = mapped_column(Integer, default=0)
    credits_used: Mapped[int] = mapped_column(Integer, default=0)
    created_by: Mapped[int | None] = mapped_column(ForeignKey("agents.id"), nullable=True)
    # Delivery + recurrence (repeating broadcasts)
    delay_seconds: Mapped[int] = mapped_column(Integer, default=1)      # gap between recipients
    repeat: Mapped[str] = mapped_column(String(20), default="none")     # none|daily|weekly|monthly
    interval: Mapped[int] = mapped_column(Integer, default=1)
    days_of_week: Mapped[list | None] = mapped_column(JSON, nullable=True)
    day_of_month: Mapped[int | None] = mapped_column(Integer, nullable=True)
    end_date: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    runs_count: Mapped[int] = mapped_column(Integer, default=0)


class MessageTemplate(Base, TimestampMixin):
    """Reusable message body for bulk campaigns (supports {{variables}})."""

    __tablename__ = "message_templates"

    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    name: Mapped[str] = mapped_column(String(255), unique=True, nullable=False)
    body: Mapped[str] = mapped_column(Text, nullable=False)
    created_by: Mapped[int | None] = mapped_column(ForeignKey("agents.id"), nullable=True)


class SavedChatList(Base, TimestampMixin):
    """Named list of chats reusable as bulk-message recipients."""

    __tablename__ = "saved_chat_lists"

    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    name: Mapped[str] = mapped_column(String(255), unique=True, nullable=False)
    chat_ids: Mapped[list | None] = mapped_column(JSON, nullable=True)
    created_by: Mapped[int | None] = mapped_column(ForeignKey("agents.id"), nullable=True)


class BulkMessageLog(Base, TimestampMixin):
    """Per-recipient delivery record for a bulk job run."""

    __tablename__ = "bulk_message_logs"

    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    job_id: Mapped[int] = mapped_column(ForeignKey("bulk_message_jobs.id"), nullable=False, index=True)
    # chat_id references MongoDB chat.id (integer) — no FK since chats live in MongoDB
    chat_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    chat_name: Mapped[str] = mapped_column(String(500), default="")
    status: Mapped[str] = mapped_column(String(20), default="sent")  # sent|failed|skipped
    error: Mapped[str | None] = mapped_column(String(500), nullable=True)
    run_number: Mapped[int] = mapped_column(Integer, default=1)
