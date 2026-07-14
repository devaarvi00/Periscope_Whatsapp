from sqlalchemy import Boolean, String
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin


class Phone(Base, TimestampMixin):
    """A connected WhatsApp number (WAHA session)."""

    __tablename__ = "phones"

    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    phone_number: Mapped[str] = mapped_column(String(30), unique=True, index=True, nullable=False)
    session_name: Mapped[str] = mapped_column(String(100), unique=True, nullable=False)
    waha_status: Mapped[str] = mapped_column(String(30), default="STOPPED")
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    is_default: Mapped[bool] = mapped_column(Boolean, default=False)
    # Per-phone WAHA connection — overrides global settings when set
    waha_base_url: Mapped[str | None] = mapped_column(String(500), nullable=True)
    waha_api_key: Mapped[str | None] = mapped_column(String(500), nullable=True)
