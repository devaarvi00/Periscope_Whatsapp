from sqlalchemy import Boolean, ForeignKey, String, JSON
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin


class Contact(Base, TimestampMixin):
    __tablename__ = "contacts"

    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    phone_number: Mapped[str] = mapped_column(String(30), unique=True, index=True, nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False, default="")
    email: Mapped[str | None] = mapped_column(String(255), nullable=True)
    company: Mapped[str | None] = mapped_column(String(255), nullable=True)
    is_masked: Mapped[bool] = mapped_column(Boolean, default=False)
    custom_properties: Mapped[dict | None] = mapped_column(JSON, nullable=True)


class ContactLabel(Base):
    __tablename__ = "contact_labels"

    contact_id: Mapped[int] = mapped_column(ForeignKey("contacts.id"), primary_key=True)
    label_id: Mapped[int] = mapped_column(ForeignKey("labels.id"), primary_key=True)
