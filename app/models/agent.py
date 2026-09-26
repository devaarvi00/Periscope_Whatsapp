import enum

from sqlalchemy import Boolean, Enum as SAEnum, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin


class AgentRole(str, enum.Enum):
    ADMIN = "admin"
    AGENT = "agent"
    VIEWER = "viewer"


class Agent(Base, TimestampMixin):
    __tablename__ = "agents"

    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    email: Mapped[str] = mapped_column(String(255), unique=True, index=True, nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    role: Mapped[AgentRole] = mapped_column(SAEnum(AgentRole), default=AgentRole.AGENT)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    avatar_color: Mapped[str] = mapped_column(String(20), default="#0D8C7C")
    # JSON-encoded per-agent notification settings (see schemas.auth.NotificationPrefs);
    # NULL means "use defaults".
    notification_prefs: Mapped[str | None] = mapped_column(Text, nullable=True)
    # JSON-encoded per-agent interface preferences (see schemas.auth.UiPrefs);
    # NULL means "use defaults / whatever this browser has".
    ui_prefs: Mapped[str | None] = mapped_column(Text, nullable=True)
