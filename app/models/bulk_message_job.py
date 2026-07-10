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
    status: Mapped[str] = mapped_column(String(20), default="pending")  # pending|running|done|failed
    message_type: Mapped[str] = mapped_column(String(20), default="text")  # text|image|file|poll
    media_url: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    poll_options: Mapped[list | None] = mapped_column(JSON, nullable=True)
    error_message: Mapped[str | None] = mapped_column(String(500), nullable=True)
    sent_count: Mapped[int] = mapped_column(Integer, default=0)
    failed_count: Mapped[int] = mapped_column(Integer, default=0)
    credits_used: Mapped[int] = mapped_column(Integer, default=0)
    created_by: Mapped[int | None] = mapped_column(ForeignKey("agents.id"), nullable=True)
