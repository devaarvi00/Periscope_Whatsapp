from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, String, JSON, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin

# Contacts known only by their WhatsApp LID (no phone number shared) store
# "<lid digits>@lid" in phone_number so the unique, non-null column stays
# valid; the API reports such contacts with phone_number = None.
LID_SUFFIX = "@lid"


class Contact(Base, TimestampMixin):
    __tablename__ = "contacts"

    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    phone_number: Mapped[str] = mapped_column(String(30), unique=True, index=True, nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False, default="")
    email: Mapped[str | None] = mapped_column(String(255), nullable=True)
    company: Mapped[str | None] = mapped_column(String(255), nullable=True)
    is_masked: Mapped[bool] = mapped_column(Boolean, default=False)
    custom_properties: Mapped[dict | None] = mapped_column(JSON, nullable=True)

    # WhatsApp identity (filled by the contacts sync / incoming messages)
    wid: Mapped[str | None] = mapped_column(String(64), nullable=True)        # 91…@c.us
    lid: Mapped[str | None] = mapped_column(String(64), nullable=True)        # 123…@lid
    pushname: Mapped[str | None] = mapped_column(String(255), nullable=True)  # name the user set in WhatsApp
    username: Mapped[str | None] = mapped_column(String(100), nullable=True)
    is_business: Mapped[bool] = mapped_column(Boolean, default=False)
    is_my_contact: Mapped[bool] = mapped_column(Boolean, default=False)       # saved in the phone's address book
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    source: Mapped[str | None] = mapped_column(String(20), nullable=True)     # whatsapp | chat | message | manual
    synced_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    picture_url: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    picture_checked_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    @property
    def has_phone(self) -> bool:
        return bool(self.phone_number) and not self.phone_number.endswith(LID_SUFFIX)


class ContactLabel(Base):
    __tablename__ = "contact_labels"

    contact_id: Mapped[int] = mapped_column(ForeignKey("contacts.id"), primary_key=True)
    label_id: Mapped[int] = mapped_column(ForeignKey("labels.id"), primary_key=True)
