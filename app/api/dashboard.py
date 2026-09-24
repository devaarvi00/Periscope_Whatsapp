"""Dashboard summary: one round-trip for the home page widgets.

Everything is scoped to the phones the calling agent may access
(see app.services.access / app.core.permissions.allowed_phone_ids).
"""
from fastapi import APIRouter, Depends
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.api.auth import get_current_agent as _current_agent
from app.core.permissions import allowed_phone_ids
from app.core.ws_manager import ws_manager
from app.db.session import get_db
from app.models.agent import Agent
from app.models.phone import Phone
from app.models.ticket import Ticket, TicketStatus
from app.schemas.inbox import PhoneOut
from app.services.access import accessible_chat_ids

router = APIRouter(prefix="/dashboard", tags=["dashboard"])

_ACTIVE_TICKET = (TicketStatus.OPEN, TicketStatus.IN_PROGRESS)


def _online_agent_ids() -> set[int]:
    # ws_manager has no public accessor yet; _connections is keyed by agent_id
    # and only holds agents with at least one live socket. Swap for a public
    # method (e.g. ws_manager.online_agent_ids()) once one exists.
    return {int(a) for a, conns in list(ws_manager._connections.items()) if conns}


@router.get("/summary")
async def dashboard_summary(db: Session = Depends(get_db), agent: Agent = Depends(_current_agent)):
    from app.services.mongo_chat_service import MongoInboxService

    allowed = allowed_phone_ids(db, agent)

    # Chats (MongoDB)
    chats = MongoInboxService().db.chats
    base: dict = {} if allowed is None else {"phone_id": {"$in": allowed}}
    total = await chats.count_documents(base)
    unread = await chats.count_documents({**base, "unread_count": {"$gt": 0}})
    flagged = await chats.count_documents({**base, "is_flagged": True})

    # Tickets (MySQL) — restricted agents only see tickets on their chats
    chat_ids = await accessible_chat_ids(db, agent)
    tq = db.query(func.count(Ticket.id))
    if chat_ids is not None:
        tq = tq.filter(Ticket.chat_id.in_(chat_ids or [0]))
    open_count = tq.filter(Ticket.status == TicketStatus.OPEN).scalar() or 0
    mine = tq.filter(Ticket.status.in_(_ACTIVE_TICKET), Ticket.assigned_to == agent.id).scalar() or 0

    # Team
    agents = db.query(Agent.id, Agent.name, Agent.avatar_color).filter(Agent.is_active == True).all()  # noqa: E712
    online_ids = _online_agent_ids()
    online = [
        {"id": a.id, "name": a.name, "avatar_color": a.avatar_color}
        for a in agents if a.id in online_ids
    ]

    # Phones
    pq = db.query(Phone).filter(Phone.is_active == True)  # noqa: E712
    if allowed is not None:
        pq = pq.filter(Phone.id.in_(allowed or [0]))
    phones = [PhoneOut.model_validate(p, from_attributes=True).model_dump() for p in pq.all()]

    return {
        "chats": {"total": total, "unread": unread, "flagged": flagged},
        "team": {"online": online, "total": len(agents)},
        "tickets": {"open": open_count, "assigned_to_me": mine},
        "phones": phones,
    }
