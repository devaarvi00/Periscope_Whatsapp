import csv
import io
from datetime import datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session

from app.api.auth import get_current_agent
from app.db.session import get_db
from app.models.agent import Agent
from app.models.chat import Chat
from app.models.contact import Contact
from app.models.message import Message
from app.models.ticket import Ticket
from app.models.activity_log import ActivityLog
from app.services.activity_service import log_activity

router = APIRouter(prefix="/exports", tags=["exports"])


def _csv_response(filename: str, header: list[str], rows: list[list]) -> StreamingResponse:
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(header)
    writer.writerows(rows)
    buf.seek(0)
    return StreamingResponse(
        iter([buf.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


def _log_export(db: Session, agent: Agent, entity: str, count: int) -> None:
    log_activity(
        db, "data_exported", entity_type=entity, agent_id=agent.id,
        description=f"{agent.name} exported {count} {entity} rows to CSV",
    )


@router.get("/chats.csv")
def export_chats(
    db: Session = Depends(get_db),
    agent: Agent = Depends(get_current_agent),
):
    chats = db.query(Chat).order_by(Chat.last_message_at.desc()).all()
    rows = [
        [c.id, c.chat_wid, c.name, "group" if c.is_group else "1:1", c.phone_id,
         c.unread_count, c.is_flagged, c.is_archived, c.assigned_to,
         c.last_message_at.isoformat() if c.last_message_at else ""]
        for c in chats
    ]
    _log_export(db, agent, "chats", len(rows))
    return _csv_response(
        "chats.csv",
        ["id", "chat_wid", "name", "type", "phone_id", "unread", "flagged",
         "archived", "assigned_to", "last_message_at"],
        rows,
    )


@router.get("/messages.csv")
def export_messages(
    days: int = 30,
    chat_id: int | None = None,
    db: Session = Depends(get_db),
    agent: Agent = Depends(get_current_agent),
):
    since = datetime.utcnow() - timedelta(days=min(days, 365))
    q = db.query(Message).filter(Message.timestamp >= since)
    if chat_id:
        q = q.filter(Message.chat_id == chat_id)
    msgs = q.order_by(Message.timestamp.asc()).limit(50000).all()
    rows = [
        [m.id, m.chat_id, m.message_wid, "out" if m.from_me else "in",
         m.sender_name, m.sender_number, (m.body or "").replace("\n", " "),
         m.message_type, m.timestamp.isoformat()]
        for m in msgs
    ]
    _log_export(db, agent, "messages", len(rows))
    return _csv_response(
        "messages.csv",
        ["id", "chat_id", "message_wid", "direction", "sender_name",
         "sender_number", "body", "type", "timestamp"],
        rows,
    )


@router.get("/tickets.csv")
def export_tickets(
    db: Session = Depends(get_db),
    agent: Agent = Depends(get_current_agent),
):
    tickets = db.query(Ticket).order_by(Ticket.created_at.desc()).all()
    rows = [
        [t.id, t.chat_id, t.title, t.status.value, t.priority.value,
         t.assigned_to, t.created_by, t.sla_breached,
         t.due_date.isoformat() if t.due_date else "",
         t.resolved_at.isoformat() if t.resolved_at else "",
         t.created_at.isoformat() if t.created_at else ""]
        for t in tickets
    ]
    _log_export(db, agent, "tickets", len(rows))
    return _csv_response(
        "tickets.csv",
        ["id", "chat_id", "title", "status", "priority", "assigned_to",
         "created_by", "sla_breached", "due_date", "resolved_at", "created_at"],
        rows,
    )


@router.get("/contacts.csv")
def export_contacts(
    db: Session = Depends(get_db),
    agent: Agent = Depends(get_current_agent),
):
    contacts = db.query(Contact).order_by(Contact.name.asc()).all()
    rows = []
    for c in contacts:
        number = c.phone_number
        if c.is_masked:
            number = number[:4] + "****" + number[-2:] if len(number) > 6 else "****"
        rows.append([c.id, c.name, number, c.email or "", c.company or "", c.is_masked])
    _log_export(db, agent, "contacts", len(rows))
    return _csv_response(
        "contacts.csv",
        ["id", "name", "phone_number", "email", "company", "masked"],
        rows,
    )


@router.get("/logs.csv")
def export_logs(
    days: int = 30,
    db: Session = Depends(get_db),
    agent: Agent = Depends(get_current_agent),
):
    from app.models.agent import AgentRole
    if agent.role != AgentRole.ADMIN:
        raise HTTPException(403, "Only admins can export audit logs")
    since = datetime.utcnow() - timedelta(days=min(days, 365))
    logs = (
        db.query(ActivityLog)
        .filter(ActivityLog.created_at >= since)
        .order_by(ActivityLog.created_at.desc())
        .limit(50000)
        .all()
    )
    rows = [
        [l.id, l.action, l.entity_type or "", l.entity_id or "", l.agent_id or "",
         (l.description or "").replace("\n", " "),
         l.created_at.isoformat() if l.created_at else ""]
        for l in logs
    ]
    _log_export(db, agent, "logs", len(rows))
    return _csv_response(
        "audit_logs.csv",
        ["id", "action", "entity_type", "entity_id", "agent_id", "description", "created_at"],
        rows,
    )
