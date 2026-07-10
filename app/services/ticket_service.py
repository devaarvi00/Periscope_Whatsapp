import logging
from datetime import datetime
from typing import Any

from sqlalchemy import desc
from sqlalchemy.orm import Session

from app.models.ticket import Ticket, TicketStatus

logger = logging.getLogger(__name__)


class TicketService:
    def __init__(self, db: Session) -> None:
        self.db = db

    def create_ticket(self, **kwargs: Any) -> Ticket:
        ticket = Ticket(**kwargs)
        self.db.add(ticket)
        self.db.commit()
        self.db.refresh(ticket)
        return ticket

    def get_ticket(self, ticket_id: int) -> Ticket | None:
        return self.db.query(Ticket).filter(Ticket.id == ticket_id).first()

    def list_tickets(
        self,
        chat_id: int | None = None,
        status: str | None = None,
        assigned_to: int | None = None,
        priority: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[Ticket]:
        q = self.db.query(Ticket)
        if chat_id:
            q = q.filter(Ticket.chat_id == chat_id)
        if status:
            q = q.filter(Ticket.status == status)
        if assigned_to is not None:
            q = q.filter(Ticket.assigned_to == assigned_to)
        if priority:
            q = q.filter(Ticket.priority == priority)
        return q.order_by(desc(Ticket.created_at)).offset(offset).limit(limit).all()

    def update_ticket(self, ticket_id: int, **kwargs: Any) -> Ticket | None:
        ticket = self.get_ticket(ticket_id)
        if not ticket:
            return None
        for k, v in kwargs.items():
            if hasattr(ticket, k) and v is not None:
                setattr(ticket, k, v)
        if kwargs.get("status") in (TicketStatus.RESOLVED, TicketStatus.CLOSED):
            ticket.resolved_at = datetime.utcnow()
        self.db.commit()
        self.db.refresh(ticket)
        return ticket

    def delete_ticket(self, ticket_id: int) -> bool:
        ticket = self.get_ticket(ticket_id)
        if not ticket:
            return False
        self.db.delete(ticket)
        self.db.commit()
        return True

    def count_by_status(self) -> dict[str, int]:
        rows = self.db.query(Ticket.status, Ticket.id).all()
        result: dict[str, int] = {}
        for status, _ in rows:
            result[status] = result.get(status, 0) + 1
        return result
