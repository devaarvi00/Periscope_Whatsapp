import csv
import io
from datetime import datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session

from app.api.auth import get_current_agent
from app.db.session import get_db
from app.models.agent import Agent
from app.models.contact import Contact
from app.models.ticket import Ticket
from app.models.activity_log import ActivityLog
from app.services.activity_service import log_activity
from app.services.mongo_chat_service import MongoInboxService

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
async def export_chats(
    db: Session = Depends(get_db),
    agent: Agent = Depends(get_current_agent),
):
    inbox = MongoInboxService()
    chats = await inbox.list_chats(limit=10000)
    agents_map = {a.id: a.name for a in db.query(Agent).all()}

    from app.models.label import Label
    from app.models.property_definition import PropertyDefinition

    prop_defs = {str(p.id): p.name for p in db.query(PropertyDefinition).filter(PropertyDefinition.entity == "chat").all()}

    rows = []
    for c in chats:
        label_ids = c.get("label_ids") or []
        label_names = []
        for lid in label_ids:
            lbl = db.query(Label).filter(Label.id == lid).first()
            if lbl:
                label_names.append(lbl.name)

        props_list = []
        for pid, val in (c.get("custom_properties") or {}).items():
            pname = prop_defs.get(str(pid), f"Property {pid}")
            props_list.append(f"{pname}: {val}")

        lma = c.get("last_message_at")
        rows.append([
            c["id"], c["chat_wid"], c.get("name") or "", "group" if c.get("is_group") else "1:1",
            c["phone_id"], c.get("unread_count") or 0, bool(c.get("is_flagged")), bool(c.get("is_archived")),
            agents_map.get(c.get("assigned_to"), "") if c.get("assigned_to") else "",
            ", ".join(label_names),
            "; ".join(props_list),
            lma.isoformat() if isinstance(lma, datetime) else (lma or ""),
        ])

    _log_export(db, agent, "chats", len(rows))
    return _csv_response(
        "chats.csv",
        ["id", "chat_wid", "name", "type", "phone_id", "unread", "flagged",
         "archived", "assigned_agent", "labels", "custom_properties", "last_message_at"],
        rows,
    )


@router.get("/messages.csv")
async def export_messages(
    days: int = 30,
    chat_id: int | None = None,
    db: Session = Depends(get_db),
    agent: Agent = Depends(get_current_agent),
):
    inbox = MongoInboxService()
    since = datetime.utcnow() - timedelta(days=min(days, 365))
    filt: dict = {"timestamp": {"$gte": since}}
    if chat_id:
        filt["chat_id"] = chat_id
    msg_docs = await (
        inbox.db.messages.find(filt)
        .sort("timestamp", 1)
        .limit(50000)
        .to_list(50000)
    )
    rows = []
    for m in msg_docs:
        ts = m.get("timestamp")
        rows.append([
            m["id"], m.get("chat_id"), m.get("phone_id"), m.get("message_wid") or "",
            "out" if m.get("from_me") else "in",
            m.get("sender_name") or "", m.get("sender_number") or "",
            (m.get("body") or "").replace("\n", " "),
            m.get("message_type") or "text",
            ts.isoformat() if isinstance(ts, datetime) else (ts or ""),
        ])
    _log_export(db, agent, "messages", len(rows))
    return _csv_response(
        "messages.csv",
        ["id", "chat_id", "phone_id", "message_wid", "direction", "sender_name",
         "sender_number", "body", "type", "timestamp"],
        rows,
    )


@router.get("/tickets.csv")
def export_tickets(
    db: Session = Depends(get_db),
    agent: Agent = Depends(get_current_agent),
):
    tickets = db.query(Ticket).order_by(Ticket.created_at.desc()).all()
    agents = {a.id: a.name for a in db.query(Agent).all()}

    from app.models.label import Label
    from app.models.ticket import TicketLabel
    from app.models.property_definition import PropertyDefinition

    ticket_labels = db.query(TicketLabel.ticket_id, Label.name).join(Label, TicketLabel.label_id == Label.id).all()
    labels_by_ticket = {}
    for tid, lname in ticket_labels:
        labels_by_ticket.setdefault(tid, []).append(lname)

    prop_defs = {str(p.id): p.name for p in db.query(PropertyDefinition).filter(PropertyDefinition.entity == "ticket").all()}

    rows = []
    for t in tickets:
        props_list = []
        if t.custom_properties:
            for pid, val in t.custom_properties.items():
                pname = prop_defs.get(str(pid), f"Property {pid}")
                props_list.append(f"{pname}: {val}")
        props_str = "; ".join(props_list)

        rows.append([
            t.id, t.chat_id, t.title, t.status.value, t.priority.value,
            agents.get(t.assigned_to, "") if t.assigned_to else "",
            agents.get(t.created_by, "") if t.created_by else "",
            ", ".join(labels_by_ticket.get(t.id, [])),
            t.sla_breached,
            props_str,
            t.due_date.isoformat() if t.due_date else "",
            t.resolved_at.isoformat() if t.resolved_at else "",
            t.created_at.isoformat() if t.created_at else ""
        ])

    _log_export(db, agent, "tickets", len(rows))
    return _csv_response(
        "tickets.csv",
        ["id", "chat_id", "title", "status", "priority", "assigned_agent",
         "created_by_agent", "labels", "sla_breached", "custom_properties", "due_date", "resolved_at", "created_at"],
        rows,
    )


@router.get("/contacts.csv")
def export_contacts(
    db: Session = Depends(get_db),
    agent: Agent = Depends(get_current_agent),
):
    contacts = db.query(Contact).order_by(Contact.name.asc()).all()

    from app.models.label import Label
    from app.models.contact import ContactLabel

    contact_labels = db.query(ContactLabel.contact_id, Label.name).join(Label, ContactLabel.label_id == Label.id).all()
    labels_by_contact = {}
    for cid, lname in contact_labels:
        labels_by_contact.setdefault(cid, []).append(lname)

    rows = []
    for c in contacts:
        number = c.phone_number
        if c.is_masked:
            number = number[:4] + "****" + number[-2:] if len(number) > 6 else "****"

        props_list = []
        if c.custom_properties:
            for k, val in c.custom_properties.items():
                props_list.append(f"{k}: {val}")
        props_str = "; ".join(props_list)

        rows.append([
            c.id, c.name, number, c.email or "", c.company or "", c.is_masked,
            ", ".join(labels_by_contact.get(c.id, [])), props_str
        ])

    _log_export(db, agent, "contacts", len(rows))
    return _csv_response(
        "contacts.csv",
        ["id", "name", "phone_number", "email", "company", "masked", "labels", "custom_properties"],
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
