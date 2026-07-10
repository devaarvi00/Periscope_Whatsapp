"""Public developer API — authenticated with an API key (X-API-Key header).

Mirrors Periskope's "Custom APIs for WhatsApp groups, chats and numbers":
send messages programmatically, list chats/messages, create tickets.
"""
import hashlib
import logging
from datetime import datetime

from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.models.api_key import ApiKey
from app.models.chat import Chat
from app.models.phone import Phone
from app.services.inbox_service import InboxService
from app.services.waha_service import WAHAService

router = APIRouter(prefix="/public/v1", tags=["public-api"])
logger = logging.getLogger(__name__)


def require_api_key(
    x_api_key: str | None = Header(None),
    db: Session = Depends(get_db),
) -> ApiKey:
    if not x_api_key:
        raise HTTPException(401, "Missing X-API-Key header")
    key_hash = hashlib.sha256(x_api_key.encode()).hexdigest()
    key = db.query(ApiKey).filter(
        ApiKey.key_hash == key_hash, ApiKey.is_active == True
    ).first()
    if not key:
        raise HTTPException(401, "Invalid API key")
    key.last_used_at = datetime.utcnow()
    db.commit()
    return key


class PublicSendRequest(BaseModel):
    chat_id: str | None = None      # WID like 9198xxxx@c.us or a group @g.us
    phone_number: str | None = None  # plain number; converted to @c.us
    message: str


@router.post("/messages/send")
async def public_send_message(
    req: PublicSendRequest,
    db: Session = Depends(get_db),
    api_key: ApiKey = Depends(require_api_key),
):
    target = req.chat_id or (f"{req.phone_number}@c.us" if req.phone_number else None)
    if not target:
        raise HTTPException(400, "chat_id or phone_number is required")
    if not req.message.strip():
        raise HTTPException(400, "message is empty")

    chat = db.query(Chat).filter(Chat.chat_wid == target).first()
    phone = None
    if chat:
        phone = db.query(Phone).filter(Phone.id == chat.phone_id).first()
    if not phone:
        phone = db.query(Phone).filter(
            Phone.is_active == True
        ).order_by(Phone.is_default.desc()).first()
    if not phone:
        raise HTTPException(503, "No active WhatsApp number connected")

    waha = WAHAService(session_name=phone.session_name)
    try:
        result = await waha.send_text(target, req.message)
    except Exception as exc:
        raise HTTPException(502, f"Send failed: {exc}")

    if chat:
        try:
            InboxService(db).upsert_message({
                "chat_id": chat.id,
                "phone_id": phone.id,
                "message_wid": result.message_id or f"api_{datetime.utcnow().timestamp()}",
                "from_me": True,
                "sender_name": f"API ({api_key.name})",
                "sender_number": phone.phone_number,
                "body": req.message,
                "message_type": "text",
                "timestamp": datetime.utcnow(),
            })
        except Exception:
            db.rollback()

    from app.services.webhook_dispatcher import dispatch_event
    await dispatch_event("message.sent", {
        "chat_wid": target, "body": req.message, "via": "public_api",
    })
    return {"ok": True, "message_id": result.message_id, "chat_id": target}


@router.get("/chats")
def public_list_chats(
    is_group: bool | None = None,
    limit: int = 50,
    offset: int = 0,
    db: Session = Depends(get_db),
    _key: ApiKey = Depends(require_api_key),
):
    chats = InboxService(db).list_chats(
        is_group=is_group, limit=min(limit, 200), offset=offset
    )
    return [
        {
            "id": c.id, "chat_id": c.chat_wid, "name": c.name,
            "is_group": c.is_group, "unread_count": c.unread_count,
            "last_message": c.last_message,
            "last_message_at": c.last_message_at.isoformat() if c.last_message_at else None,
        }
        for c in chats
    ]


@router.get("/chats/{chat_wid}/messages")
def public_get_messages(
    chat_wid: str,
    limit: int = 50,
    db: Session = Depends(get_db),
    _key: ApiKey = Depends(require_api_key),
):
    chat = db.query(Chat).filter(Chat.chat_wid == chat_wid).first()
    if not chat:
        raise HTTPException(404, "Chat not found")
    msgs = InboxService(db).get_messages(chat.id, limit=min(limit, 200))
    return [
        {
            "id": m.message_wid, "from_me": m.from_me,
            "sender_name": m.sender_name, "sender_number": m.sender_number,
            "body": m.body, "type": m.message_type,
            "timestamp": m.timestamp.isoformat(),
        }
        for m in reversed(msgs)
    ]


class PublicTicketRequest(BaseModel):
    chat_id: str
    title: str
    description: str = ""
    priority: str = "medium"


@router.post("/tickets", status_code=201)
def public_create_ticket(
    req: PublicTicketRequest,
    db: Session = Depends(get_db),
    api_key: ApiKey = Depends(require_api_key),
):
    chat = db.query(Chat).filter(Chat.chat_wid == req.chat_id).first()
    if not chat:
        raise HTTPException(404, "Chat not found")
    from app.services.ticket_service import TicketService
    from app.models.ticket import TicketPriority
    try:
        priority = TicketPriority(req.priority.lower())
    except ValueError:
        priority = TicketPriority.MEDIUM
    ticket = TicketService(db).create_ticket(
        chat_id=chat.id, title=req.title[:500],
        description=req.description, priority=priority,
    )
    return {"id": ticket.id, "title": ticket.title, "status": "open"}


@router.get("/numbers")
def public_list_numbers(
    db: Session = Depends(get_db),
    _key: ApiKey = Depends(require_api_key),
):
    phones = db.query(Phone).filter(Phone.is_active == True).all()
    return [
        {"id": p.id, "name": p.name, "phone_number": p.phone_number,
         "status": p.waha_status}
        for p in phones
    ]
