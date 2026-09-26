import re

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.api.auth import get_current_agent
from app.api.contacts import mask_phone
from app.core.permissions import allowed_phone_ids
from app.db.session import get_db
from app.models.agent import Agent
from app.models.contact import Contact
from app.models.ticket import Ticket
from app.services.access import accessible_chat_ids, is_admin
from app.services.mongo_chat_service import MongoInboxService

router = APIRouter(prefix="/search", tags=["search"])

MAX_SEARCH_LIMIT = 100


def _like_escape(value: str) -> str:
    """Escape SQL LIKE wildcards so user input matches literally."""
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


@router.get("")
async def universal_search(
    q: str,
    limit: int = 10,
    db: Session = Depends(get_db),
    agent: Agent = Depends(get_current_agent),
):
    if not q or len(q.strip()) < 2:
        return {"chats": [], "messages": [], "tickets": [], "contacts": []}
    q = q.strip()[:200]
    limit = max(1, min(limit, MAX_SEARCH_LIMIT))

    pattern = f"%{_like_escape(q)}%"
    inbox = MongoInboxService()
    phone_ids = allowed_phone_ids(db, agent)

    # Chat search in MongoDB (list_chats escapes the regex itself)
    mongo_chats = await inbox.list_chats(search=q, phone_ids=phone_ids, limit=limit)
    chats_out = [{"id": c["id"], "name": c.get("name") or "", "type": "chat"} for c in mongo_chats]

    # Message search in MongoDB — literal match, scoped to accessible phones
    msg_filter: dict = {"body": {"$regex": re.escape(q), "$options": "i"}}
    if phone_ids is not None:
        msg_filter["phone_id"] = {"$in": phone_ids}
    msg_docs = await (
        inbox.db.messages.find(msg_filter)
        .sort("timestamp", -1)
        .limit(limit)
        .to_list(limit)
    )
    messages_out = [
        {"id": m["id"], "chat_id": m.get("chat_id"), "body": (m.get("body") or "")[:100], "type": "message"}
        for m in msg_docs
    ]

    tq = db.query(Ticket).filter(
        Ticket.title.ilike(pattern, escape="\\") | Ticket.description.ilike(pattern, escape="\\")
    )
    chat_ids = await accessible_chat_ids(db, agent)
    if chat_ids is not None:
        tq = tq.filter(Ticket.chat_id.in_(chat_ids or [0]))
    tickets = tq.limit(limit).all()

    admin = is_admin(agent)
    cq = db.query(Contact)
    if admin:
        cq = cq.filter(Contact.name.ilike(pattern, escape="\\") | Contact.phone_number.ilike(pattern, escape="\\"))
    else:
        # Non-admins must not be able to discover a masked contact's digits
        cq = cq.filter(
            Contact.name.ilike(pattern, escape="\\")
            | ((Contact.is_masked == False) & Contact.phone_number.ilike(pattern, escape="\\"))  # noqa: E712
        )
    contacts = cq.limit(limit).all()

    return {
        "chats": chats_out,
        "messages": messages_out,
        "tickets": [{"id": t.id, "title": t.title, "status": t.status, "type": "ticket"} for t in tickets],
        "contacts": [
            {
                "id": c.id, "name": c.name, "type": "contact",
                "phone": mask_phone(c.phone_number) if (c.is_masked and not admin) else c.phone_number,
            }
            for c in contacts
        ],
    }
