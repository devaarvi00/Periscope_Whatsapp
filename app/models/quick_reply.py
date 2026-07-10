from sqlalchemy import ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin


class QuickReply(Base, TimestampMixin):
    """Org-wide command shortcut (e.g. /pricing → full pricing message)."""

    __tablename__ = "quick_replies"

    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    command: Mapped[str] = mapped_column(String(100), unique=True, nullable=False)
    message: Mapped[str] = mapped_column(Text, nullable=False)
    created_by: Mapped[int | None] = mapped_column(ForeignKey("agents.id"), nullable=True)
