"""AI agent bookkeeping: Gemini usage, per-message run log, internal
contacts and admin-defined custom HTTP tools."""
from datetime import datetime

from sqlalchemy import JSON, Boolean, DateTime, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin


class AIUsage(Base):
    """One Gemini API call (successful or not) and the tokens it used."""

    __tablename__ = "ai_usage"

    id: Mapped[int] = mapped_column(primary_key=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, index=True)
    model: Mapped[str] = mapped_column(String(100), default="")
    # reply | classify | playground | translate | suggest | summary | flag | polish |
    # assistant | self_training | other
    purpose: Mapped[str] = mapped_column(String(30), default="other", index=True)
    chat_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    prompt_tokens: Mapped[int] = mapped_column(Integer, default=0)
    candidate_tokens: Mapped[int] = mapped_column(Integer, default=0)
    total_tokens: Mapped[int] = mapped_column(Integer, default=0)
    latency_ms: Mapped[int] = mapped_column(Integer, default=0)
    ok: Mapped[bool] = mapped_column(Boolean, default=True)


class AIRunLog(Base):
    """What the agent decided for one inbound message (kept 30 days)."""

    __tablename__ = "ai_run_logs"

    id: Mapped[int] = mapped_column(primary_key=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, index=True)
    chat_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    chat_name: Mapped[str] = mapped_column(String(255), default="")
    phone_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    message_wid: Mapped[str | None] = mapped_column(String(200), nullable=True)
    message_preview: Mapped[str] = mapped_column(String(300), default="")
    # replied | drafted | skipped | error
    decision: Mapped[str] = mapped_column(String(20), default="skipped", index=True)
    # outside_hours | snoozed | not_activated | phone_not_allowed | internal_contact |
    # rules | superseded | group | no_reply | error ...
    reason: Mapped[str] = mapped_column(String(40), default="")
    detail: Mapped[str] = mapped_column(String(500), default="")
    reply: Mapped[str | None] = mapped_column(Text, nullable=True)
    tools: Mapped[list | None] = mapped_column(JSON, nullable=True)  # [{"name":..., "ok":..., ...}]
    kb_titles: Mapped[list | None] = mapped_column(JSON, nullable=True)
    kb_miss: Mapped[bool] = mapped_column(Boolean, default=False)  # AI said it didn't know
    tokens: Mapped[int] = mapped_column(Integer, default=0)
    latency_ms: Mapped[int] = mapped_column(Integer, default=0)


class AIInternalContact(Base, TimestampMixin):
    """A team/internal number the agent must never reply to or flag."""

    __tablename__ = "ai_internal_contacts"

    id: Mapped[int] = mapped_column(primary_key=True)
    number: Mapped[str] = mapped_column(String(30), unique=True, index=True)  # digits only
    label: Mapped[str] = mapped_column(String(255), default="")


class AICustomTool(Base, TimestampMixin):
    """An admin-configured HTTP endpoint the agent may call."""

    __tablename__ = "ai_custom_tools"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(64), unique=True)  # snake_case, shown to the model
    description: Mapped[str] = mapped_column(Text, default="")
    method: Mapped[str] = mapped_column(String(6), default="GET")  # GET | POST
    url: Mapped[str] = mapped_column(String(1000), default="")
    headers: Mapped[dict | None] = mapped_column(JSON, nullable=True)  # write-only secrets
    params: Mapped[list | None] = mapped_column(JSON, nullable=True)   # [{name,type,description,required}]
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    timeout_seconds: Mapped[int] = mapped_column(Integer, default=8)
