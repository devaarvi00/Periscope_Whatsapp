import uuid

from sqlalchemy import String
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin


def _new_uid() -> str:
    return str(uuid.uuid4())


class OrgSettings(Base, TimestampMixin):
    """Workspace identity shown in the sidebar switcher (single row, id=1)."""

    __tablename__ = "org_settings"

    id: Mapped[int] = mapped_column(primary_key=True)
    uid: Mapped[str] = mapped_column(String(36), default=_new_uid, unique=True)
    name: Mapped[str] = mapped_column(String(120), default="Hyperscope")
    support_email: Mapped[str | None] = mapped_column(String(255), nullable=True)
    support_url: Mapped[str | None] = mapped_column(String(500), nullable=True)
