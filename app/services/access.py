"""Authorization helpers shared by every chat/phone-scoped route.

Semantics (mirrors app.core.permissions.allowed_phone_ids):
- Admins can access everything.
- An agent with no AgentPhone rows can access every number.
- Otherwise the agent may only touch phones listed in agent_phones, and
  chats / messages / tickets / notes belonging to those phones.
"""
from __future__ import annotations

from typing import Any

from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.core.permissions import allowed_phone_ids
from app.models.agent import Agent, AgentRole


def is_admin(agent: Agent | None) -> bool:
    return bool(agent) and agent.role == AgentRole.ADMIN


def require_admin(agent: Agent, detail: str = "Only admins can perform this action") -> None:
    if not is_admin(agent):
        raise HTTPException(403, detail)


def agent_can_access_phone(db: Session, agent: Agent, phone_id: int | None) -> bool:
    allowed = allowed_phone_ids(db, agent)
    if allowed is None:
        return True
    return phone_id is not None and int(phone_id) in allowed


def assert_phone_access(db: Session, agent: Agent, phone_id: int | None) -> None:
    if not agent_can_access_phone(db, agent, phone_id):
        raise HTTPException(403, "You do not have access to this WhatsApp number")


def agent_can_access_chat(db: Session, agent: Agent, chat: dict | None) -> bool:
    if not chat:
        return False
    return agent_can_access_phone(db, agent, chat.get("phone_id"))


def assert_chat_access(db: Session, agent: Agent, chat: dict | None) -> dict:
    """Raise 404 when the chat is missing or on a phone the agent can't see.

    404 (not 403) so restricted agents can't probe which chat IDs exist.
    """
    if not agent_can_access_chat(db, agent, chat):
        raise HTTPException(404, "Chat not found")
    return chat  # type: ignore[return-value]


async def get_accessible_chat(db: Session, agent: Agent, chat_id: int) -> dict:
    from app.services.mongo_chat_service import MongoInboxService
    chat = await MongoInboxService().get_chat_by_id(chat_id)
    return assert_chat_access(db, agent, chat)


async def assert_chat_id_access(db: Session, agent: Agent, chat_id: int | None) -> None:
    """Check access for an optional chat_id reference (tasks, tickets, notes)."""
    if chat_id is None:
        return
    if allowed_phone_ids(db, agent) is None:
        return
    await get_accessible_chat(db, agent, chat_id)


async def accessible_chat_ids(db: Session, agent: Agent) -> list[int] | None:
    """Mongo chat IDs the agent may see, or None when unrestricted."""
    allowed = allowed_phone_ids(db, agent)
    if allowed is None:
        return None
    from app.services.mongo_chat_service import MongoInboxService
    ids = await MongoInboxService().db.chats.distinct("id", {"phone_id": {"$in": allowed}})
    return [int(i) for i in ids]


async def filter_accessible_chat_ids(db: Session, agent: Agent, chat_ids: list[Any]) -> list[int]:
    """Keep only the chat IDs the agent may touch (used by bulk actions)."""
    ids = [int(c) for c in chat_ids]
    allowed = allowed_phone_ids(db, agent)
    if allowed is None:
        return ids
    from app.services.mongo_chat_service import MongoInboxService
    ok = await MongoInboxService().db.chats.distinct(
        "id", {"id": {"$in": ids}, "phone_id": {"$in": allowed}}
    )
    ok_set = {int(i) for i in ok}
    return [i for i in ids if i in ok_set]


# ── Organization permissions (Settings → Permissions) ─────────────────────── #
# Admins bypass every check. For everyone else the org-wide switches decide.

ACTION_DENIED = {
    "create_chats": "Your organization doesn't allow agents to start new chats",
    "data_export": "Your organization doesn't allow agents to export data",
    "archive_chats": "Your organization doesn't allow agents to archive or close chats",
    "assign": "Your organization doesn't allow agents to assign chats or tickets",
    "update_labels": "Your organization doesn't allow agents to change labels",
    "delete_tickets": "Your organization doesn't allow agents to delete tickets",
}


def org_permissions(db: Session) -> dict:
    from app.models.org_config import get_org_config
    return get_org_config(db)["permissions"]


def effective_permissions(db: Session, agent: Agent) -> dict:
    """What this agent may do / see: the org switches, or everything for admins."""
    perms = org_permissions(db)
    if is_admin(agent):
        return {
            "actions": {k: True for k in perms["actions"]},
            "screens": {k: True for k in perms["screens"]},
        }
    return perms


def has_action(db: Session, agent: Agent, action: str) -> bool:
    if is_admin(agent):
        return True
    return bool(org_permissions(db)["actions"].get(action, False))


def require_action(db: Session, agent: Agent, action: str) -> None:
    if not has_action(db, agent, action):
        raise HTTPException(403, ACTION_DENIED.get(action, "Your organization doesn't allow this action"))


def has_screen(db: Session, agent: Agent, screen: str) -> bool:
    if is_admin(agent):
        return True
    return bool(org_permissions(db)["screens"].get(screen, False))


def require_screen(db: Session, agent: Agent, screen: str, label: str | None = None) -> None:
    if not has_screen(db, agent, screen):
        name = label or screen.replace("_", " ").title()
        raise HTTPException(403, f"Your organization has turned off {name} for agents")


def screen_guard(screen: str, label: str | None = None):
    """FastAPI dependency factory: 403 when the screen is off for this agent."""
    from fastapi import Depends
    from app.api.auth import get_current_agent
    from app.db.session import get_db

    def _guard(db: Session = Depends(get_db), agent: Agent = Depends(get_current_agent)) -> None:
        require_screen(db, agent, screen, label)

    _guard.__name__ = f"screen_guard_{screen}"
    return _guard


def should_mask_numbers(db: Session, agent: Agent) -> bool:
    """Settings → Config → "Mask User Phone Numbers" (never for admins)."""
    if is_admin(agent):
        return False
    from app.models.org_config import get_org_config
    return bool(get_org_config(db)["config"].get("mask_phone_numbers"))


def mask_number(value: str | None) -> str:
    """'919876543210' → '9198******10'. Keeps a WID's @domain suffix."""
    if not value:
        return value or ""
    ident, sep, domain = str(value).partition("@")
    if domain == "g.us":
        return str(value)  # group ids aren't personal numbers
    if len(ident) <= 4:
        masked = "*" * len(ident)
    else:
        keep_head = min(4, max(1, len(ident) - 6))
        masked = ident[:keep_head] + "*" * (len(ident) - keep_head - 2) + ident[-2:]
    return masked + (sep + domain if sep else "")
