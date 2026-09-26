"""Organization-wide feature switches (Settings → Config, Permissions, Tickets,
Group Settings) plus the media library and group templates.

All switches live in one JSON document on a single row (id=1) so adding an
option never needs a migration. Readers always go through get_org_config(),
which overlays the stored values on DEFAULTS — a missing row or key simply
means "default", so GET never has to write.
"""
from __future__ import annotations

import copy
from typing import Any

from sqlalchemy import Boolean, ForeignKey, Integer, JSON, String, Text
from sqlalchemy.orm import Mapped, Session, mapped_column

from app.models.base import Base, TimestampMixin

ACTION_KEYS = (
    "create_chats", "data_export", "archive_chats", "assign", "update_labels", "delete_tickets",
)
MAIN_SCREEN_KEYS = (
    "analytics", "bulk", "contacts", "media", "ai", "automation", "chat_list", "logs",
)
SETTINGS_SCREEN_KEYS = (
    "phones", "labels", "tickets", "quick_replies", "custom_properties",
    "media_library", "group_templates", "integrations",
)
SCREEN_KEYS = MAIN_SCREEN_KEYS + SETTINGS_SCREEN_KEYS

DEFAULT_TICKET_TEMPLATE = "Automated ticket raised: {{ticket_id}}"
DEFAULT_INVITE_TEMPLATE = "You're invited to join our WhatsApp group {{group_name}}: {{invite_link}}"

DEFAULTS: dict[str, dict[str, Any]] = {
    "config": {
        "translation_enabled": False,
        "translation_language": "en",
        "auto_translate": False,
        "show_sender_names": False,
        "mask_phone_numbers": False,
        "show_deleted_messages": False,
        # On = only the chat's own number renders on the right (current
        # behaviour); off = messages from any of our numbers render right.
        "active_phone_right": True,
    },
    "permissions": {
        "actions": {k: True for k in ACTION_KEYS},
        "screens": {
            **{k: True for k in MAIN_SCREEN_KEYS},
            **{k: k == "phones" for k in SETTINGS_SCREEN_KEYS},
        },
    },
    "tickets": {
        "prefix": "TKT",
        "auto_attach": False,
        "emoji_ticketing": True,
        "auto_message": False,
        "auto_message_template": DEFAULT_TICKET_TEMPLATE,
    },
    "groups": {
        "invite_message_enabled": False,
        "invite_template": DEFAULT_INVITE_TEMPLATE,
    },
}


class OrgConfig(Base, TimestampMixin):
    __tablename__ = "org_config"

    id: Mapped[int] = mapped_column(primary_key=True)
    data: Mapped[dict | None] = mapped_column(JSON, nullable=True, default=None)


class MediaLibraryItem(Base, TimestampMixin):
    """A file uploaded to the shared media library (bytes live on disk)."""

    __tablename__ = "media_library_items"

    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    kind: Mapped[str] = mapped_column(String(10), default="media", index=True)  # media | doc
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    stored_name: Mapped[str] = mapped_column(String(80), unique=True, nullable=False)
    mimetype: Mapped[str] = mapped_column(String(100), default="application/octet-stream")
    size: Mapped[int] = mapped_column(Integer, default=0)
    uploaded_by: Mapped[int | None] = mapped_column(ForeignKey("agents.id"), nullable=True)


class GroupTemplate(Base, TimestampMixin):
    __tablename__ = "group_templates"

    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    description: Mapped[str] = mapped_column(Text, default="")
    participants: Mapped[list | None] = mapped_column(JSON, nullable=True, default=None)
    messages_admin_only: Mapped[bool] = mapped_column(Boolean, default=False)
    info_admin_only: Mapped[bool] = mapped_column(Boolean, default=False)
    created_by: Mapped[int | None] = mapped_column(ForeignKey("agents.id"), nullable=True)


def _merge(defaults: dict, stored: Any) -> dict:
    out = copy.deepcopy(defaults)
    if not isinstance(stored, dict):
        return out
    for k, v in stored.items():
        if k not in out:
            continue  # drop keys we no longer know
        if isinstance(out[k], dict):
            out[k] = _merge(out[k], v)
        elif isinstance(out[k], bool):
            out[k] = bool(v)
        elif v is not None:
            out[k] = v
    return out


def get_org_config(db: Session) -> dict[str, dict[str, Any]]:
    """Every section with defaults filled in. Never writes."""
    row = db.get(OrgConfig, 1)
    return _merge(DEFAULTS, row.data if row else None)


def update_org_config(db: Session, section: str, values: dict[str, Any]) -> dict[str, Any]:
    """Merge `values` into one section and persist; returns the full config."""
    if section not in DEFAULTS:
        raise KeyError(section)
    row = db.get(OrgConfig, 1)
    if not row:
        row = OrgConfig(id=1, data={})
        db.add(row)
    data = copy.deepcopy(row.data) if isinstance(row.data, dict) else {}
    current = data.get(section) if isinstance(data.get(section), dict) else {}
    for k, v in values.items():
        if isinstance(v, dict) and isinstance(current.get(k), dict):
            current[k] = {**current[k], **v}
        else:
            current[k] = v
    data[section] = current
    row.data = data  # reassign so SQLAlchemy sees the JSON change
    db.commit()
    return get_org_config(db)
