import csv
import io
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Query
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

from app.core.permissions import allowed_phone_ids
from app.services.access import (
    is_admin, mask_number, require_action, require_admin, should_mask_numbers,
)


def _require_export_permission(
    db: Session = Depends(get_db), agent: Agent = Depends(get_current_agent),
) -> Agent:
    # Settings → Permissions → "Data Export" (admins always may export)
    require_action(db, agent, "data_export")
    return agent


def _admin_only(agent: Agent = Depends(get_current_agent)) -> Agent:
    require_admin(agent, "Only admins can export this data")
    return agent


# Agents with "Data Export" may export chats, messages and tickets — scoped to
# the numbers they can access. Org-wide data (contacts, logs, notes, phones,
# group events) stays admin-only.
router = APIRouter(
    prefix="/exports", tags=["exports"], dependencies=[Depends(_require_export_permission)],
)


def _mask_digits(text: str) -> str:
    """Mask every phone-number-like digit run (message ids embed the number)."""
    import re
    return re.sub(r"\d{7,}", lambda m: mask_number(m.group(0)), text)


def _phone_scope(db: Session, agent: Agent) -> list[int] | None:
    return None if is_admin(agent) else allowed_phone_ids(db, agent)

# Cells starting with these are interpreted as formulas by Excel/Sheets/LibreOffice
_FORMULA_PREFIXES = ("=", "+", "-", "@", "\t", "\r")


def _safe_cell(value):
    """Neutralise CSV/formula injection by prefixing a single quote."""
    if isinstance(value, str) and value.startswith(_FORMULA_PREFIXES):
        return "'" + value
    return value


def _csv_response(filename: str, header: list[str], rows: list[list]) -> StreamingResponse:
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow([_safe_cell(h) for h in header])
    writer.writerows([[_safe_cell(v) for v in row] for row in rows])
    buf.seek(0)
    return StreamingResponse(
        iter([buf.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


def _parse_dt(value: str | None, name: str) -> datetime | None:
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError:
        raise HTTPException(422, f"Invalid '{name}' datetime")
    if dt.tzinfo is not None:
        dt = dt.astimezone(timezone.utc).replace(tzinfo=None)
    return dt


def _range(from_: str | None, to: str | None, days: int | None) -> tuple[datetime | None, datetime | None]:
    """Optional [from, to) window: explicit ISO bounds win, else the last `days` days."""
    start, end = _parse_dt(from_, "from"), _parse_dt(to, "to")
    if start is None and days:
        start = (end or datetime.utcnow()) - timedelta(days=min(max(days, 1), 365))
    if start and end and start >= end:
        raise HTTPException(422, "'from' must be before 'to'")
    return start, end


def _window(start: datetime | None, end: datetime | None) -> dict:
    w: dict = {}
    if start:
        w["$gte"] = start
    if end:
        w["$lt"] = end
    return w


def _iso(v) -> str:
    return v.isoformat() if isinstance(v, datetime) else (v or "")


def _log_export(db: Session, agent: Agent, entity: str, count: int) -> None:
    log_activity(
        db, "data_exported", entity_type=entity, agent_id=agent.id,
        description=f"{agent.name} exported {count} {entity} rows to CSV",
    )


@router.get("/chats.csv")
async def export_chats(
    from_: str | None = Query(None, alias="from"),
    to: str | None = None,
    db: Session = Depends(get_db),
    agent: Agent = Depends(get_current_agent),
):
    """All chats (archived included); with a range, chats active in it."""
    start, end = _range(from_, to, None)
    filt: dict = {}
    if start or end:
        filt["last_message_at"] = _window(start, end)
    scope = _phone_scope(db, agent)
    if scope is not None:
        filt["phone_id"] = {"$in": scope}
    mask = should_mask_numbers(db, agent)
    inbox = MongoInboxService()
    chats = await inbox.db.chats.find(filt).sort("last_message_at", -1).limit(50000).to_list(50000)
    agents_map = {a.id: a.name for a in db.query(Agent).all()}

    from app.models.label import Label
    from app.models.property_definition import PropertyDefinition

    label_names_by_id = {lbl.id: lbl.name for lbl in db.query(Label).all()}
    prop_defs = {str(p.id): p.name for p in db.query(PropertyDefinition).filter(PropertyDefinition.entity == "chat").all()}

    rows = []
    for c in chats:
        label_names = [label_names_by_id[lid] for lid in (c.get("label_ids") or []) if lid in label_names_by_id]

        props_list = []
        for pid, val in (c.get("custom_properties") or {}).items():
            pname = prop_defs.get(str(pid), f"Property {pid}")
            props_list.append(f"{pname}: {val}")

        rows.append([
            c["id"], mask_number(c["chat_wid"]) if mask else c["chat_wid"],
            (mask_number(c.get("name") or "") if mask and (c.get("name") or "").lstrip("+").isdigit() else c.get("name") or ""),
            "group" if c.get("is_group") else "1:1",
            c["phone_id"], c.get("unread_count") or 0, bool(c.get("is_flagged")), bool(c.get("is_archived")),
            agents_map.get(c.get("assigned_to"), "") if c.get("assigned_to") else "",
            ", ".join(label_names),
            "; ".join(props_list),
            _iso(c.get("created_at")),
            _iso(c.get("last_message_at")),
        ])

    _log_export(db, agent, "chats", len(rows))
    return _csv_response(
        "chats.csv",
        ["id", "chat_wid", "name", "type", "phone_id", "unread", "flagged",
         "archived", "assigned_agent", "labels", "custom_properties", "created_at", "last_message_at"],
        rows,
    )


@router.get("/messages.csv")
async def export_messages(
    days: int = 30,
    chat_id: int | None = None,
    from_: str | None = Query(None, alias="from"),
    to: str | None = None,
    flagged_only: bool = False,
    db: Session = Depends(get_db),
    agent: Agent = Depends(get_current_agent),
):
    inbox = MongoInboxService()
    start, end = _range(from_, to, days)
    filt: dict = {"timestamp": _window(start, end)}
    if chat_id:
        filt["chat_id"] = chat_id
    if flagged_only:
        filt["is_flagged"] = True
    scope = _phone_scope(db, agent)
    if scope is not None:
        filt["phone_id"] = {"$in": scope}
    mask = should_mask_numbers(db, agent)
    msg_docs = await (
        inbox.db.messages.find(filt)
        .sort("timestamp", 1)
        .limit(50000)
        .to_list(50000)
    )
    agents_map = {a.id: a.name for a in db.query(Agent).all()}
    rows = []
    for m in msg_docs:
        rows.append([
            m["id"], m.get("chat_id"), m.get("phone_id"),
            _mask_digits(m.get("message_wid") or "") if mask else m.get("message_wid") or "",
            "out" if m.get("from_me") else "in",
            m.get("sender_name") or "",
            mask_number(m.get("sender_number") or "") if mask else m.get("sender_number") or "",
            agents_map.get(m.get("sent_by_agent_id"), "") if m.get("sent_by_agent_id") else "",
            (m.get("body") or "").replace("\n", " "),
            m.get("message_type") or "text",
            bool(m.get("is_flagged")),
            _iso(m.get("timestamp")),
        ])
    _log_export(db, agent, "messages", len(rows))
    return _csv_response(
        "messages.csv",
        ["id", "chat_id", "phone_id", "message_wid", "direction", "sender_name",
         "sender_number", "sent_by_agent", "body", "type", "flagged", "timestamp"],
        rows,
    )


@router.get("/tickets.csv")
async def export_tickets(
    from_: str | None = Query(None, alias="from"),
    to: str | None = None,
    db: Session = Depends(get_db),
    agent: Agent = Depends(get_current_agent),
):
    start, end = _range(from_, to, None)
    tq = db.query(Ticket)
    if _phone_scope(db, agent) is not None:
        from app.services.access import accessible_chat_ids
        ids = await accessible_chat_ids(db, agent)
        tq = tq.filter(Ticket.chat_id.in_(ids or [0]))
    if start:
        tq = tq.filter(Ticket.created_at >= start)
    if end:
        tq = tq.filter(Ticket.created_at < end)
    tickets = tq.order_by(Ticket.created_at.desc()).all()
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


@router.get("/contacts.csv", dependencies=[Depends(_admin_only)])
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


@router.get("/logs.csv", dependencies=[Depends(_admin_only)])
def export_logs(
    days: int = 30,
    from_: str | None = Query(None, alias="from"),
    to: str | None = None,
    db: Session = Depends(get_db),
    agent: Agent = Depends(get_current_agent),
):
    start, end = _range(from_, to, days)
    lq = db.query(ActivityLog).filter(ActivityLog.created_at >= start)
    if end:
        lq = lq.filter(ActivityLog.created_at < end)
    logs = (
        lq
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


@router.get("/notes.csv", dependencies=[Depends(_admin_only)])
async def export_notes(
    from_: str | None = Query(None, alias="from"),
    to: str | None = None,
    db: Session = Depends(get_db),
    agent: Agent = Depends(get_current_agent),
):
    """Private team notes (never sent to customers)."""
    from app.models.note import Note
    start, end = _range(from_, to, None)
    nq = db.query(Note)
    if start:
        nq = nq.filter(Note.created_at >= start)
    if end:
        nq = nq.filter(Note.created_at < end)
    notes = nq.order_by(Note.created_at.desc()).limit(50000).all()
    agents_map = {a.id: a.name for a in db.query(Agent).all()}
    chat_ids = list({n.chat_id for n in notes})
    chats = {}
    if chat_ids:
        async for c in MongoInboxService().db.chats.find(
            {"id": {"$in": chat_ids}}, {"id": 1, "name": 1, "chat_wid": 1, "phone_id": 1}
        ):
            chats[c["id"]] = c
    rows = []
    for n in notes:
        c = chats.get(n.chat_id) or {}
        rows.append([
            n.id, n.chat_id, c.get("name") or c.get("chat_wid") or "", c.get("phone_id") or "",
            agents_map.get(n.agent_id, ""), (n.content or "").replace("\n", " "), _iso(n.created_at),
        ])
    _log_export(db, agent, "notes", len(rows))
    return _csv_response(
        "private_notes.csv",
        ["id", "chat_id", "chat_name", "phone_id", "author", "content", "created_at"],
        rows,
    )


@router.get("/phones.csv", dependencies=[Depends(_admin_only)])
def export_phones(
    db: Session = Depends(get_db),
    agent: Agent = Depends(get_current_agent),
):
    from app.models.phone import Phone
    phones = db.query(Phone).order_by(Phone.id.asc()).all()
    rows = [
        [p.id, p.name, p.phone_number, p.session_name, p.waha_status, bool(p.is_active), _iso(p.created_at)]
        for p in phones
    ]
    _log_export(db, agent, "phones", len(rows))
    return _csv_response(
        "phones.csv",
        ["id", "name", "phone_number", "session_name", "status", "active", "created_at"],
        rows,
    )


@router.get("/chat_actions.csv", dependencies=[Depends(_admin_only)])
async def export_chat_actions(
    from_: str | None = Query(None, alias="from"),
    to: str | None = None,
    days: int = 30,
    db: Session = Depends(get_db),
    agent: Agent = Depends(get_current_agent),
):
    """Group membership events (join / add / leave / remove / promote / demote)."""
    start, end = _range(from_, to, days)
    mdb = MongoInboxService().db
    events = await (
        mdb.group_events.find({"timestamp": _window(start, end)})
        .sort("timestamp", 1).limit(50000).to_list(50000)
    )
    chat_ids = list({e.get("chat_id") for e in events if e.get("chat_id") is not None})
    names = {}
    if chat_ids:
        async for c in mdb.chats.find({"id": {"$in": chat_ids}}, {"id": 1, "name": 1}):
            names[c["id"]] = c.get("name") or ""
    rows = [
        [_iso(e.get("timestamp")), e.get("phone_id") or "", e.get("chat_id") or "",
         names.get(e.get("chat_id"), "") or e.get("chat_wid") or "",
         e.get("type") or "", e.get("participant") or "", e.get("actor") or ""]
        for e in events
    ]
    _log_export(db, agent, "chat_actions", len(rows))
    return _csv_response(
        "chat_actions.csv",
        ["timestamp", "phone_id", "chat_id", "chat_name", "action", "participant", "actor"],
        rows,
    )
