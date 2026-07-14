from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.api.auth import get_current_agent
from app.db.session import get_db
from app.models.agent import Agent
from app.models.scheduled_message import ScheduledMessage
from app.services.activity_service import log_activity
from app.services.mongo_chat_service import MongoInboxService

router = APIRouter(prefix="/scheduled", tags=["scheduled-messages"])

REPEATS = ("none", "daily", "weekly", "monthly")


class ScheduledMessageCreate(BaseModel):
    chat_id: int
    body: str
    send_at: str  # ISO 8601
    repeat: str = "none"
    interval: int = 1                       # every N days/weeks/months
    days_of_week: list[int] | None = None   # 0=Mon … 6=Sun (daily repeat)
    day_of_month: int | None = None         # 1-31 (monthly repeat)
    end_date: str | None = None             # ISO 8601, optional


class ScheduledMessageUpdate(BaseModel):
    body: str | None = None
    send_at: str | None = None
    repeat: str | None = None
    interval: int | None = None
    days_of_week: list[int] | None = None
    day_of_month: int | None = None
    end_date: str | None = None


def _repeat_summary(m: ScheduledMessage) -> str:
    if m.repeat == "none":
        return "Once"
    every = f"every {m.interval} " if (m.interval or 1) > 1 else ""
    if m.repeat == "daily":
        days = m.days_of_week or []
        names = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
        picked = ", ".join(names[d] for d in sorted(days) if 0 <= d <= 6)
        base = f"Daily ({picked})" if picked and len(days) < 7 else "Daily"
        return f"{base}" + (f" · every {m.interval} days" if (m.interval or 1) > 1 else "")
    if m.repeat == "weekly":
        return f"{every}week" if every else "Weekly"
    if m.repeat == "monthly":
        dom = f" on day {m.day_of_month}" if m.day_of_month else ""
        return (f"{every}month" if every else "Monthly") + dom
    return m.repeat


def _serialize(m: ScheduledMessage, chat_names: dict[int, str]) -> dict:
    return {
        "id": m.id, "chat_id": m.chat_id,
        "chat_name": chat_names.get(m.chat_id, ""),
        "body": m.body, "send_at": m.send_at.isoformat() + "Z",
        "repeat": m.repeat, "interval": m.interval or 1,
        "days_of_week": m.days_of_week or [],
        "day_of_month": m.day_of_month,
        "end_date": (m.end_date.isoformat() + "Z") if m.end_date else None,
        "repeat_summary": _repeat_summary(m),
        "status": m.status,
        "sent_count": m.sent_count, "last_error": m.last_error,
    }


def _parse_recurrence(req) -> dict:
    if req.repeat is not None and req.repeat not in REPEATS:
        raise HTTPException(400, f"repeat must be one of {REPEATS}")
    out: dict = {}
    if req.repeat is not None:
        out["repeat"] = req.repeat
    if req.interval is not None:
        if not (1 <= req.interval <= 30):
            raise HTTPException(400, "interval must be between 1 and 30")
        out["interval"] = req.interval
    if req.days_of_week is not None:
        days = sorted({d for d in req.days_of_week if 0 <= int(d) <= 6})
        out["days_of_week"] = days or None
    if req.day_of_month is not None:
        if not (1 <= req.day_of_month <= 31):
            raise HTTPException(400, "day_of_month must be 1-31")
        out["day_of_month"] = req.day_of_month
    if req.end_date is not None:
        if req.end_date == "":
            out["end_date"] = None
        else:
            try:
                out["end_date"] = datetime.fromisoformat(req.end_date)
            except ValueError:
                raise HTTPException(400, "Invalid end_date (use ISO 8601)")
    return out


@router.get("")
async def list_scheduled(
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

    # Fetch chat names from MongoDB
    inbox = MongoInboxService()
    chat_ids = list({i.chat_id for i in items})
    chat_names: dict[int, str] = {}
    for cid in chat_ids:
        chat = await inbox.get_chat_by_id(cid)
        if chat:
            chat_names[cid] = chat.get("name") or ""

    return [_serialize(m, chat_names) for m in items]


@router.post("", status_code=201)
async def create_scheduled(
    req: ScheduledMessageCreate,
    db: Session = Depends(get_db),
    agent: Agent = Depends(get_current_agent),
):
    inbox = MongoInboxService()
    chat = await inbox.get_chat_by_id(req.chat_id)
    if not chat:
        raise HTTPException(404, "Chat not found")
    try:
        send_at = datetime.fromisoformat(req.send_at)
    except ValueError:
        raise HTTPException(400, "Invalid send_at (use ISO 8601)")
    if not req.body.strip():
        raise HTTPException(400, "Message body is empty")
    rec = _parse_recurrence(req)

    msg = ScheduledMessage(
        chat_id=req.chat_id,
        chat_wid=chat["chat_wid"],
        phone_id=chat["phone_id"],
        body=req.body, send_at=send_at,
        repeat=rec.get("repeat", "none"),
        interval=rec.get("interval", 1),
        days_of_week=rec.get("days_of_week"),
        day_of_month=rec.get("day_of_month"),
        end_date=rec.get("end_date"),
        created_by=agent.id,
    )
    db.add(msg)
    db.commit()
    db.refresh(msg)
    chat_name = chat.get("name") or ""
    log_activity(
        db, "scheduled_message_created", entity_type="scheduled_message",
        entity_id=msg.id, agent_id=agent.id,
        description=f"Message scheduled for '{chat_name}' at {send_at.isoformat()} ({_repeat_summary(msg)})",
    )
    return _serialize(msg, {chat["id"]: chat_name})


@router.patch("/{msg_id}")
async def update_scheduled(
    msg_id: int,
    req: ScheduledMessageUpdate,
    db: Session = Depends(get_db),
    agent: Agent = Depends(get_current_agent),
):
    msg = db.query(ScheduledMessage).filter(ScheduledMessage.id == msg_id).first()
    if not msg:
        raise HTTPException(404, "Scheduled message not found")
    if msg.status not in ("pending",):
        raise HTTPException(400, "Only pending schedules can be edited")
    rec = _parse_recurrence(req)
    if req.body is not None:
        if not req.body.strip():
            raise HTTPException(400, "Message body is empty")
        msg.body = req.body
    if req.send_at is not None:
        try:
            msg.send_at = datetime.fromisoformat(req.send_at)
        except ValueError:
            raise HTTPException(400, "Invalid send_at")
    for k, v in rec.items():
        setattr(msg, k, v)
    db.commit()
    db.refresh(msg)
    log_activity(
        db, "scheduled_message_updated", entity_type="scheduled_message",
        entity_id=msg.id, agent_id=agent.id,
        description=f"Scheduled message #{msg.id} updated ({_repeat_summary(msg)})",
    )
    inbox = MongoInboxService()
    chat = await inbox.get_chat_by_id(msg.chat_id)
    chat_name = (chat.get("name") if chat else None) or ""
    return _serialize(msg, {msg.chat_id: chat_name})


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
