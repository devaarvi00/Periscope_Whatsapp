import uuid
from datetime import datetime

from sqlalchemy import DateTime, LargeBinary, String
from sqlalchemy.dialects.mysql import MEDIUMBLOB
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
    # Workspace logo (PNG/JPEG, <= 512 KB) — small enough to keep in the row,
    # so it survives container rebuilds without a volume.
    logo_data: Mapped[bytes | None] = mapped_column(
        LargeBinary().with_variant(MEDIUMBLOB(), "mysql"), nullable=True, deferred=True
    )
    logo_mime: Mapped[str | None] = mapped_column(String(32), nullable=True)
    logo_updated_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
