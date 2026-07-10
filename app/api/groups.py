from datetime import datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.api.auth import get_current_agent
from app.db.session import get_db
from app.models.agent import Agent
from app.models.chat import Chat
from app.models.message import Message
from app.models.phone import Phone
from app.services.waha_service import WAHAService

router = APIRouter(prefix="/groups", tags=["groups"])


@router.get("")
def list_groups(
    search: str | None = None,
    limit: int = 100,
    offset: int = 0,
    db: Session = Depends(get_db),
    agent: Agent = Depends(get_current_agent),
):
    from app.core.permissions import allowed_phone_ids
    q = db.query(Chat).filter(Chat.is_group == True, Chat.is_archived == False)
    allowed = allowed_phone_ids(db, agent)
    if allowed is not None:
        q = q.filter(Chat.phone_id.in_(allowed or [0]))
    if search:
        q = q.filter(Chat.name.ilike(f"%{search}%"))
    groups = q.order_by(Chat.last_message_at.desc()).offset(offset).limit(limit).all()

    week_ago = datetime.utcnow() - timedelta(days=7)
    counts = dict(
        db.query(Message.chat_id, func.count(Message.id))
        .filter(Message.chat_id.in_([g.id for g in groups] or [0]),
                Message.timestamp >= week_ago)
        .group_by(Message.chat_id)
        .all()
    )
    return [
        {
            "id": g.id, "chat_wid": g.chat_wid, "name": g.name,
            "phone_id": g.phone_id, "unread_count": g.unread_count,
            "is_flagged": g.is_flagged, "assigned_to": g.assigned_to,
            "last_message": g.last_message,
            "last_message_at": g.last_message_at.isoformat() if g.last_message_at else None,
            "messages_7d": counts.get(g.id, 0),
        }
        for g in groups
    ]


@router.get("/{chat_id}/participants")
async def group_participants(chat_id: int, db: Session = Depends(get_db)):
    chat = db.query(Chat).filter(Chat.id == chat_id, Chat.is_group == True).first()
    if not chat:
        raise HTTPException(404, "Group not found")
    phone = db.query(Phone).filter(Phone.id == chat.phone_id).first()
    if not phone:
        raise HTTPException(404, "Phone not found")
    waha = WAHAService(session_name=phone.session_name)
    participants = await waha.get_group_participants(chat.chat_wid)
    result = []
    for p in participants:
        pid = p.get("id")
        if isinstance(pid, dict):
            pid = pid.get("_serialized") or pid.get("user", "")
        result.append({
            "id": str(pid),
            "number": str(pid).split("@")[0],
            "is_admin": bool(p.get("isAdmin") or p.get("admin")),
        })
    return {"group": chat.name, "count": len(result), "participants": result}


@router.get("/{chat_id}/analytics")
def group_analytics(
    chat_id: int,
    days: int = 30,
    db: Session = Depends(get_db),
):
    """Group activity: daily message volume, top senders, in/out split."""
    chat = db.query(Chat).filter(Chat.id == chat_id, Chat.is_group == True).first()
    if not chat:
        raise HTTPException(404, "Group not found")
    since = datetime.utcnow() - timedelta(days=min(days, 180))

    total = db.query(func.count(Message.id)).filter(
        Message.chat_id == chat_id, Message.timestamp >= since
    ).scalar() or 0
    incoming = db.query(func.count(Message.id)).filter(
        Message.chat_id == chat_id, Message.from_me == False,
        Message.timestamp >= since
    ).scalar() or 0

    daily = (
        db.query(func.date(Message.timestamp), func.count(Message.id))
        .filter(Message.chat_id == chat_id, Message.timestamp >= since)
        .group_by(func.date(Message.timestamp))
        .order_by(func.date(Message.timestamp))
        .all()
    )
    top_senders = (
        db.query(Message.sender_name, Message.sender_number, func.count(Message.id).label("n"))
        .filter(Message.chat_id == chat_id, Message.from_me == False,
                Message.timestamp >= since)
        .group_by(Message.sender_name, Message.sender_number)
        .order_by(func.count(Message.id).desc())
        .limit(10)
        .all()
    )
    return {
        "group": chat.name,
        "days": days,
        "total_messages": total,
        "incoming": incoming,
        "outgoing": total - incoming,
        "daily_volume": [{"date": str(d), "count": n} for d, n in daily],
        "top_senders": [
            {"name": name or number, "number": number, "messages": n}
            for name, number, n in top_senders
        ],
    }
