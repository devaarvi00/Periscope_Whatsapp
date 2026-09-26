"""Operation logs — the Logs page's Group / API / Webhooks / Rules / Scheduled tabs.

One row per operation (a bulk group change, a public-API request, an outbound
webhook dispatch, an automation rule run, a scheduled send / bulk run) with
per-item success / failed / pending counts. Rows older than 7 days are purged
(app.services.operation_log.cleanup). Never holds secrets: API keys, webhook
secrets, auth headers or message bodies beyond a short preview.
"""
from datetime import datetime

from sqlalchemy import DateTime, Index, Integer, JSON, String
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base

OPERATION_KINDS = ("group", "api", "webhook", "rule", "scheduled")
OPERATION_STATUSES = ("success", "failed", "partial", "pending")


class OperationLog(Base):
    __tablename__ = "operation_logs"
    __table_args__ = (Index("ix_operation_logs_kind_created", "kind", "created_at"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    uid: Mapped[str] = mapped_column(String(16), unique=True, nullable=False)   # shown as "Log ID"
    kind: Mapped[str] = mapped_column(String(16), nullable=False)
    operation: Mapped[str] = mapped_column(String(255), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="success")
    success_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    failed_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    pending_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    status_code: Mapped[int | None] = mapped_column(Integer, nullable=True)   # HTTP status (API / webhooks)
    duration_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # agents.id of the member who did it (no FK: logs outlive deleted agents)
    performed_by_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # Label when no member did it: "API key: <name>", "Automation", "Scheduler", "System"
    performed_by: Mapped[str | None] = mapped_column(String(120), nullable=True)
    details: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=datetime.utcnow, index=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=datetime.utcnow,
                                                 onupdate=datetime.utcnow)
