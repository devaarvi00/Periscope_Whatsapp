from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Index
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class AgentSession(Base):
    """One presence span: the agent had at least one live websocket.

    Opened when the agent's first socket connects and closed when the last
    one disconnects (multiple tabs share a span). ``last_seen_at`` is bumped
    periodically so a span left open by a crashed process can be closed at
    the last time it was known to be alive. Feeds "User uptime" analytics.
    """

    __tablename__ = "agent_sessions"
    __table_args__ = (Index("ix_agent_sessions_agent_started", "agent_id", "started_at"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    agent_id: Mapped[int] = mapped_column(ForeignKey("agents.id"), nullable=False)
    started_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, index=True)
    ended_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True, index=True)
    last_seen_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
