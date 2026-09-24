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
