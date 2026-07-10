from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from sqlalchemy.orm import Session

from app.api.auth import get_current_agent
from app.db.session import get_db
from app.models.agent import Agent
from app.schemas.ticket import TicketCreate, TicketOut, TicketUpdate
from app.services.activity_service import log_activity
from app.services.automation_service import fire_trigger
from app.services.ticket_service import TicketService

router = APIRouter(prefix="/tickets", tags=["tickets"])


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
def list_tickets(
    chat_id: int | None = None,
    status: str | None = None,
    assigned_to: int | None = None,
    priority: str | None = None,
    limit: int = 50,
    offset: int = 0,
    db: Session = Depends(get_db),
):
    return TicketService(db).list_tickets(
        chat_id=chat_id, status=status,
        assigned_to=assigned_to, priority=priority,
        limit=limit, offset=offset,
    )


@router.post("", response_model=TicketOut, status_code=201)
def create_ticket(
    req: TicketCreate,
    background: BackgroundTasks,
    db: Session = Depends(get_db),
    agent: Agent = Depends(get_current_agent),
):
    data = req.model_dump()
    data.setdefault("created_by", agent.id)
    ticket = TicketService(db).create_ticket(**data)
    log_activity(
        db, "ticket_created", entity_type="ticket", entity_id=ticket.id,
        agent_id=agent.id, description=f"Ticket '{ticket.title}' created",
    )
    background.add_task(fire_trigger, "ticket_created", _trigger_context(ticket))
    from app.services.webhook_dispatcher import dispatch_event
    background.add_task(dispatch_event, "ticket.created", _trigger_context(ticket))
    return ticket


@router.get("/{ticket_id}", response_model=TicketOut)
def get_ticket(ticket_id: int, db: Session = Depends(get_db)):
    ticket = TicketService(db).get_ticket(ticket_id)
    if not ticket:
        raise HTTPException(404, "Ticket not found")
    return ticket


@router.patch("/{ticket_id}", response_model=TicketOut)
def update_ticket(
    ticket_id: int,
    req: TicketUpdate,
    background: BackgroundTasks,
    db: Session = Depends(get_db),
    agent: Agent = Depends(get_current_agent),
):
    changes = req.model_dump(exclude_none=True)
    ticket = TicketService(db).update_ticket(ticket_id, **changes)
    if not ticket:
        raise HTTPException(404, "Ticket not found")
    log_activity(
        db, "ticket_updated", entity_type="ticket", entity_id=ticket.id,
        agent_id=agent.id,
        description=f"Ticket '{ticket.title}' updated: {', '.join(changes.keys())}",
        metadata=changes,
    )
    background.add_task(fire_trigger, "ticket_updated", _trigger_context(ticket))
    from app.services.webhook_dispatcher import dispatch_event
    background.add_task(dispatch_event, "ticket.updated", _trigger_context(ticket))
    return ticket


@router.delete("/{ticket_id}", status_code=204)
def delete_ticket(
    ticket_id: int,
    db: Session = Depends(get_db),
    agent: Agent = Depends(get_current_agent),
):
    if not TicketService(db).delete_ticket(ticket_id):
        raise HTTPException(404, "Ticket not found")
    log_activity(
        db, "ticket_deleted", entity_type="ticket", entity_id=ticket_id,
        agent_id=agent.id, description=f"Ticket #{ticket_id} deleted",
    )


# ── Ticket labels ─────────────────────────────────────────────────────────────

@router.get("/{ticket_id}/labels")
def get_ticket_labels(ticket_id: int, db: Session = Depends(get_db)):
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
def add_ticket_label(ticket_id: int, label_id: int, db: Session = Depends(get_db)):
    from app.models.ticket import TicketLabel
    if not TicketService(db).get_ticket(ticket_id):
        raise HTTPException(404, "Ticket not found")
    exists = db.query(TicketLabel).filter(
        TicketLabel.ticket_id == ticket_id, TicketLabel.label_id == label_id
    ).first()
    if not exists:
        db.add(TicketLabel(ticket_id=ticket_id, label_id=label_id))
        db.commit()
    return {"ok": True}


@router.delete("/{ticket_id}/labels/{label_id}", status_code=204)
def remove_ticket_label(ticket_id: int, label_id: int, db: Session = Depends(get_db)):
    from app.models.ticket import TicketLabel
    row = db.query(TicketLabel).filter(
        TicketLabel.ticket_id == ticket_id, TicketLabel.label_id == label_id
    ).first()
    if row:
        db.delete(row)
        db.commit()
