from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.api.auth import get_current_agent
from app.db.session import get_db
from app.models.agent import Agent
from app.models.chat import Chat
from app.models.scheduled_message import ScheduledMessage
from app.services.activity_service import log_activity

router = APIRouter(prefix="/scheduled", tags=["scheduled-messages"])

REPEATS = ("none", "daily", "weekly")


class ScheduledMessageCreate(BaseModel):
    chat_id: int
    body: str
    send_at: str  # ISO 8601
    repeat: str = "none"


def _serialize(m: ScheduledMessage, chat_names: dict[int, str]) -> dict:
    return {
        "id": m.id, "chat_id": m.chat_id,
        "chat_name": chat_names.get(m.chat_id, ""),
        "body": m.body, "send_at": m.send_at.isoformat(),
        "repeat": m.repeat, "status": m.status,
        "sent_count": m.sent_count, "last_error": m.last_error,
    }


@router.get("")
def list_scheduled(
    chat_id: int | None = None,
    status: str | None = None,
    db: Session = Depends(get_db),
):
    q = db.query(ScheduledMessage)
    if chat_id:
        q = q.filter(ScheduledMessage.chat_id == chat_id)
    if status:
        q = q.filter(ScheduledMessage.status == status)
    items = q.order_by(ScheduledMessage.send_at.asc()).limit(200).all()
    chat_names = {
        c.id: c.name for c in
        db.query(Chat).filter(Chat.id.in_([i.chat_id for i in items] or [0])).all()
    }
    return [_serialize(m, chat_names) for m in items]


@router.post("", status_code=201)
def create_scheduled(
    req: ScheduledMessageCreate,
    db: Session = Depends(get_db),
    agent: Agent = Depends(get_current_agent),
):
    chat = db.query(Chat).filter(Chat.id == req.chat_id).first()
    if not chat:
        raise HTTPException(404, "Chat not found")
    if req.repeat not in REPEATS:
        raise HTTPException(400, f"repeat must be one of {REPEATS}")
    try:
        send_at = datetime.fromisoformat(req.send_at)
    except ValueError:
        raise HTTPException(400, "Invalid send_at (use ISO 8601)")
    if not req.body.strip():
        raise HTTPException(400, "Message body is empty")

    msg = ScheduledMessage(
        chat_id=req.chat_id, body=req.body, send_at=send_at,
        repeat=req.repeat, created_by=agent.id,
    )
    db.add(msg)
    db.commit()
    db.refresh(msg)
    log_activity(
        db, "scheduled_message_created", entity_type="scheduled_message",
        entity_id=msg.id, agent_id=agent.id,
        description=f"Message scheduled for '{chat.name}' at {send_at.isoformat()} ({req.repeat})",
    )
    return _serialize(msg, {chat.id: chat.name})


@router.delete("/{msg_id}", status_code=204)
def cancel_scheduled(
    msg_id: int,
    db: Session = Depends(get_db),
    agent: Agent = Depends(get_current_agent),
):
    msg = db.query(ScheduledMessage).filter(ScheduledMessage.id == msg_id).first()
    if not msg:
        raise HTTPException(404, "Scheduled message not found")
    msg.status = "cancelled"
    db.commit()
    log_activity(
        db, "scheduled_message_cancelled", entity_type="scheduled_message",
        entity_id=msg_id, agent_id=agent.id,
        description=f"Scheduled message #{msg_id} cancelled",
    )
