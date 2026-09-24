from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query
from fastapi.encoders import jsonable_encoder
from sqlalchemy.orm import Session

from app.api.auth import get_current_agent
from app.db.session import get_db
from app.models.agent import Agent
from app.schemas.ticket import TicketCreate, TicketOut, TicketUpdate
from app.services.access import accessible_chat_ids, assert_chat_id_access
from app.services.activity_service import log_activity
from app.services.automation_service import fire_trigger
from app.services.ticket_service import TicketService

router = APIRouter(prefix="/tickets", tags=["tickets"])


async def _notify_assignee(agent_id: int, payload: dict) -> None:
    from app.core.ws_manager import ws_manager
    await ws_manager.send_to_agent(agent_id, "ticket_assigned", payload)


async def _get_accessible_ticket(db: Session, agent: Agent, ticket_id: int):
    ticket = TicketService(db).get_ticket(ticket_id)
    if not ticket:
        raise HTTPException(404, "Ticket not found")
    try:
        await assert_chat_id_access(db, agent, ticket.chat_id)
    except HTTPException:
        raise HTTPException(404, "Ticket not found")
    return ticket


def _trigger_context(ticket) -> dict:
    return {
        "chat_id": ticket.chat_id,
        "ticket_id": ticket.id,
        "title": ticket.title,
        "status": ticket.status.value if hasattr(ticket.status, "value") else str(ticket.status),
        "priority": ticket.priority.value if hasattr(ticket.priority, "value") else str(ticket.priority),
        "assigned_to": ticket.assigned_to,
    }


@router.get("", response_model=list[TicketOut])
async def list_tickets(
    chat_id: int | None = None,
    status: str | None = None,
    assigned_to: int | None = None,
    priority: str | None = None,
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
    agent: Agent = Depends(get_current_agent),
):
    return TicketService(db).list_tickets(
        chat_id=chat_id, status=status,
        assigned_to=assigned_to, priority=priority,
        limit=limit, offset=offset,
        chat_ids=await accessible_chat_ids(db, agent),
    )


@router.post("", response_model=TicketOut, status_code=201)
async def create_ticket(
    req: TicketCreate,
    background: BackgroundTasks,
    db: Session = Depends(get_db),
    agent: Agent = Depends(get_current_agent),
):
    await assert_chat_id_access(db, agent, req.chat_id)
    data = req.model_dump()
    data.setdefault("created_by", agent.id)
    from app.models.ticket import TicketPriority, TicketStatus
    try:
        data["status"] = TicketStatus(str(data.get("status", "open")).lower())
    except ValueError:
        data["status"] = TicketStatus.OPEN
    try:
        data["priority"] = TicketPriority(str(data.get("priority", "medium")).lower())
    except ValueError:
        data["priority"] = TicketPriority.MEDIUM
    ticket = TicketService(db).create_ticket(**data)
    log_activity(
        db, "ticket_created", entity_type="ticket", entity_id=ticket.id,
        agent_id=agent.id, description=f"Ticket '{ticket.title}' created",
    )
    background.add_task(fire_trigger, "ticket_created", _trigger_context(ticket))
    from app.services.webhook_dispatcher import dispatch_event
    background.add_task(dispatch_event, "ticket.created", _trigger_context(ticket))
    if ticket.assigned_to and ticket.assigned_to != agent.id:
        background.add_task(_notify_assignee, ticket.assigned_to, {
            "ticket_id": ticket.id, "title": ticket.title,
            "by": agent.name,
            "priority": ticket.priority.value if hasattr(ticket.priority, "value") else str(ticket.priority),
        })
    return ticket


@router.get("/{ticket_id}", response_model=TicketOut)
async def get_ticket(
    ticket_id: int,
    db: Session = Depends(get_db),
    agent: Agent = Depends(get_current_agent),
):
    return await _get_accessible_ticket(db, agent, ticket_id)


@router.patch("/{ticket_id}", response_model=TicketOut)
async def update_ticket(
    ticket_id: int,
    req: TicketUpdate,
    background: BackgroundTasks,
    db: Session = Depends(get_db),
    agent: Agent = Depends(get_current_agent),
):
    await _get_accessible_ticket(db, agent, ticket_id)
    # exclude_unset: only fields the client sent; "assigned_to": null unassigns
    changes = req.model_dump(exclude_unset=True)
    from app.models.ticket import TicketPriority, TicketStatus
    for field, enum_cls in (("status", TicketStatus), ("priority", TicketPriority)):
        if changes.get(field) is not None:
            try:
                changes[field] = enum_cls(str(changes[field]).lower())
            except ValueError:
                raise HTTPException(400, f"Invalid {field}")
    ticket = TicketService(db).update_ticket(ticket_id, **changes)
    if not ticket:
        raise HTTPException(404, "Ticket not found")
    log_activity(
        db, "ticket_updated", entity_type="ticket", entity_id=ticket.id,
        agent_id=agent.id,
        description=f"Ticket '{ticket.title}' updated: {', '.join(changes.keys())}",
        metadata=jsonable_encoder(changes),
    )
    background.add_task(fire_trigger, "ticket_updated", _trigger_context(ticket))
    from app.services.webhook_dispatcher import dispatch_event
    background.add_task(dispatch_event, "ticket.updated", _trigger_context(ticket))
    if changes.get("assigned_to") and changes["assigned_to"] != agent.id:
        background.add_task(_notify_assignee, changes["assigned_to"], {
            "ticket_id": ticket.id, "title": ticket.title,
            "by": agent.name,
            "priority": ticket.priority.value if hasattr(ticket.priority, "value") else str(ticket.priority),
        })
    return ticket


@router.delete("/{ticket_id}", status_code=204)
async def delete_ticket(
    ticket_id: int,
    db: Session = Depends(get_db),
    agent: Agent = Depends(get_current_agent),
):
    await _get_accessible_ticket(db, agent, ticket_id)
    if not TicketService(db).delete_ticket(ticket_id):
        raise HTTPException(404, "Ticket not found")
    log_activity(
        db, "ticket_deleted", entity_type="ticket", entity_id=ticket_id,
        agent_id=agent.id, description=f"Ticket #{ticket_id} deleted",
    )


# ── Ticket labels ─────────────────────────────────────────────────────────────

@router.get("/{ticket_id}/labels")
async def get_ticket_labels(
    ticket_id: int,
    db: Session = Depends(get_db),
    agent: Agent = Depends(get_current_agent),
):
    await _get_accessible_ticket(db, agent, ticket_id)
    from app.models.label import Label
    from app.models.ticket import TicketLabel
    rows = (
        db.query(Label)
        .join(TicketLabel, TicketLabel.label_id == Label.id)
        .filter(TicketLabel.ticket_id == ticket_id)
        .all()
    )
    return [{"id": l.id, "name": l.name, "color": l.color} for l in rows]


@router.post("/{ticket_id}/labels/{label_id}", status_code=201)
async def add_ticket_label(
    ticket_id: int,
    label_id: int,
    db: Session = Depends(get_db),
    agent: Agent = Depends(get_current_agent),
):
    from app.models.ticket import TicketLabel
    await _get_accessible_ticket(db, agent, ticket_id)
    exists = db.query(TicketLabel).filter(
        TicketLabel.ticket_id == ticket_id, TicketLabel.label_id == label_id
    ).first()
    if not exists:
        db.add(TicketLabel(ticket_id=ticket_id, label_id=label_id))
        db.commit()
    return {"ok": True}


@router.delete("/{ticket_id}/labels/{label_id}", status_code=204)
async def remove_ticket_label(
    ticket_id: int,
    label_id: int,
    db: Session = Depends(get_db),
    agent: Agent = Depends(get_current_agent),
):
    from app.models.ticket import TicketLabel
    await _get_accessible_ticket(db, agent, ticket_id)
    row = db.query(TicketLabel).filter(
        TicketLabel.ticket_id == ticket_id, TicketLabel.label_id == label_id
    ).first()
    if row:
        db.delete(row)
        db.commit()
