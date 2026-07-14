from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.models.contact import Contact
from app.models.ticket import Ticket
from app.services.mongo_chat_service import MongoInboxService

router = APIRouter(prefix="/search", tags=["search"])


@router.get("")
async def universal_search(q: str, limit: int = 10, db: Session = Depends(get_db)):
    if not q or len(q.strip()) < 2:
        return {"chats": [], "messages": [], "tickets": [], "contacts": []}

    pattern = f"%{q}%"
    inbox = MongoInboxService()

    # Chat search in MongoDB
    mongo_chats = await inbox.list_chats(search=q, limit=limit)
    chats_out = [{"id": c["id"], "name": c.get("name") or "", "type": "chat"} for c in mongo_chats]

    # Message search in MongoDB
    msg_regex = {"$regex": q, "$options": "i"}
    msg_docs = await (
        inbox.db.messages.find({"body": msg_regex})
        .sort("timestamp", -1)
        .limit(limit)
        .to_list(limit)
    )
    messages_out = [
        {"id": m["id"], "chat_id": m.get("chat_id"), "body": (m.get("body") or "")[:100], "type": "message"}
        for m in msg_docs
    ]

    tickets = db.query(Ticket).filter(
        Ticket.title.ilike(pattern) | Ticket.description.ilike(pattern)
    ).limit(limit).all()
    contacts = db.query(Contact).filter(
        Contact.name.ilike(pattern) | Contact.phone_number.ilike(pattern)
    ).limit(limit).all()

    return {
        "chats": chats_out,
        "messages": messages_out,
        "tickets": [{"id": t.id, "title": t.title, "status": t.status, "type": "ticket"} for t in tickets],
        "contacts": [{"id": c.id, "name": c.name, "phone": c.phone_number, "type": "contact"} for c in contacts],
    }
