from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.models.chat import Chat
from app.models.contact import Contact
from app.models.message import Message
from app.models.ticket import Ticket

router = APIRouter(prefix="/search", tags=["search"])


@router.get("")
def universal_search(q: str, limit: int = 10, db: Session = Depends(get_db)):
    if not q or len(q.strip()) < 2:
        return {"chats": [], "messages": [], "tickets": [], "contacts": []}

    pattern = f"%{q}%"

    chats = db.query(Chat).filter(
        Chat.name.ilike(pattern) | Chat.chat_wid.ilike(pattern)
    ).limit(limit).all()
    messages = db.query(Message).filter(Message.body.ilike(pattern)).order_by(
        Message.timestamp.desc()
    ).limit(limit).all()
    tickets = db.query(Ticket).filter(
        Ticket.title.ilike(pattern) | Ticket.description.ilike(pattern)
    ).limit(limit).all()
    contacts = db.query(Contact).filter(
        Contact.name.ilike(pattern) | Contact.phone_number.ilike(pattern)
    ).limit(limit).all()

    return {
        "chats": [{"id": c.id, "name": c.name, "type": "chat"} for c in chats],
        "messages": [
            {"id": m.id, "chat_id": m.chat_id, "body": m.body[:100], "type": "message"}
            for m in messages
        ],
        "tickets": [{"id": t.id, "title": t.title, "status": t.status, "type": "ticket"} for t in tickets],
        "contacts": [{"id": c.id, "name": c.name, "phone": c.phone_number, "type": "contact"} for c in contacts],
    }
