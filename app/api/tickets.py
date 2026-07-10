from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.schemas.ticket import TicketCreate, TicketOut, TicketUpdate
from app.services.ticket_service import TicketService

router = APIRouter(prefix="/tickets", tags=["tickets"])


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
def create_ticket(req: TicketCreate, db: Session = Depends(get_db)):
    return TicketService(db).create_ticket(**req.model_dump())


@router.get("/{ticket_id}", response_model=TicketOut)
def get_ticket(ticket_id: int, db: Session = Depends(get_db)):
    ticket = TicketService(db).get_ticket(ticket_id)
    if not ticket:
        raise HTTPException(404, "Ticket not found")
    return ticket


@router.patch("/{ticket_id}", response_model=TicketOut)
def update_ticket(ticket_id: int, req: TicketUpdate, db: Session = Depends(get_db)):
    ticket = TicketService(db).update_ticket(ticket_id, **req.model_dump(exclude_none=True))
    if not ticket:
        raise HTTPException(404, "Ticket not found")
    return ticket


@router.delete("/{ticket_id}", status_code=204)
def delete_ticket(ticket_id: int, db: Session = Depends(get_db)):
    if not TicketService(db).delete_ticket(ticket_id):
        raise HTTPException(404, "Ticket not found")
