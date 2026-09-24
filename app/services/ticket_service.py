import logging
from datetime import datetime
from typing import Any

from sqlalchemy import desc
from sqlalchemy.orm import Session

from app.models.ticket import Ticket, TicketLabel, TicketMessage, TicketStatus

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
        chat_ids: list[int] | None = None,
    ) -> list[Ticket]:
        q = self.db.query(Ticket)
        if chat_ids is not None:
            q = q.filter(Ticket.chat_id.in_(chat_ids or [0]))
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
        # Callers pass only the fields the client sent (exclude_unset), so
        # None is meaningful for nullable fields: assigned_to=None unassigns,
        # due_date=None clears the deadline.
        nullable = {"assigned_to", "due_date"}
        prev_due, prev_assignee = ticket.due_date, ticket.assigned_to
        for k, v in kwargs.items():
            if not hasattr(ticket, k):
                continue
            if v is None and k not in nullable:
                continue
            setattr(ticket, k, v)
        if kwargs.get("status") in (TicketStatus.RESOLVED, TicketStatus.CLOSED):
            ticket.resolved_at = datetime.utcnow()
        # Re-arm the one-shot overdue notification when the deadline moves out
        # (or is cleared), or when a new assignee takes over.
        if ticket.due_date != prev_due and (ticket.due_date is None or ticket.due_date > datetime.utcnow()):
            ticket.overdue_notified_at = None
        if ticket.assigned_to != prev_assignee:
            ticket.overdue_notified_at = None
        self.db.commit()
        self.db.refresh(ticket)
        return ticket

    def delete_ticket(self, ticket_id: int) -> bool:
        ticket = self.get_ticket(ticket_id)
        if not ticket:
            return False
        self.db.query(TicketMessage).filter(TicketMessage.ticket_id == ticket_id).delete()
        self.db.query(TicketLabel).filter(TicketLabel.ticket_id == ticket_id).delete()
        self.db.delete(ticket)
        self.db.commit()
        return True


    # ── Settings → Tickets ─────────────────────────────────────────────── #

    def ticket_for_message(self, chat_id: int, message_wid: str) -> Ticket | None:
        """The ticket a message belongs to: as its origin, or attached later."""
        if not message_wid:
            return None
        t = self.db.query(Ticket).filter(Ticket.chat_id == chat_id, Ticket.message_wid == message_wid).first()
        if t:
            return t
        link = self.db.query(TicketMessage).filter(
            TicketMessage.chat_id == chat_id, TicketMessage.message_wid == message_wid
        ).first()
        return self.get_ticket(link.ticket_id) if link else None

    def attach_reply(self, chat_id: int, quoted_wid: str, reply_wid: str) -> int | None:
        """"Enable Automatic Ticket Attachment to Messages": a reply quoting a
        ticketed message joins the same ticket. Returns the ticket id."""
        if not reply_wid:
            return None
        ticket = self.ticket_for_message(chat_id, quoted_wid)
        if not ticket or self.ticket_for_message(chat_id, reply_wid):
            return None
        self.db.add(TicketMessage(ticket_id=ticket.id, chat_id=chat_id, message_wid=reply_wid))
        self.db.commit()
        logger.info("Attached message %s to ticket %s (quoted %s)", reply_wid, ticket.id, quoted_wid)
        return ticket.id

    def attached_messages(self, ticket_id: int) -> list[str]:
        rows = self.db.query(TicketMessage.message_wid).filter(
            TicketMessage.ticket_id == ticket_id
        ).order_by(TicketMessage.id).all()
        return [r[0] for r in rows]


def ticket_prefix(db: Session) -> str:
    from app.models.org_config import get_org_config
    return (get_org_config(db)["tickets"].get("prefix") or "").strip().upper()


def ticket_display_id(ticket_id: int, prefix: str) -> str:
    """"AAR-12" with a prefix, "#12" without (ids are never renumbered)."""
    return f"{prefix}-{ticket_id}" if prefix else f"#{ticket_id}"


def render_ticket_template(template: str, display_id: str) -> str:
    return (template or "").replace("{{ticket_id}}", display_id).strip()


async def send_ticket_created_message(ticket_id: int) -> bool:
    """"Send an automated message when a ticket is created" (default off):
    tells the customer the ticket number through the chat's own number.
    Runs as a background task with its own DB session; never raises."""
    from app.db.session import SessionLocal
    from app.models.org_config import get_org_config
    from app.models.phone import Phone
    db = SessionLocal()
    try:
        cfg = get_org_config(db)["tickets"]
        if not cfg.get("auto_message"):
            return False
        ticket = db.get(Ticket, ticket_id)
        if not ticket:
            return False
        from app.services.mongo_chat_service import MongoInboxService
        inbox = MongoInboxService()
        chat = await inbox.get_chat_by_id(ticket.chat_id)
        if not chat:
            return False
        phone = db.query(Phone).filter(Phone.id == chat["phone_id"]).first()
        if not phone or phone.waha_status != "WORKING":
            logger.info("Ticket %s auto-message skipped: number not connected", ticket_id)
            return False
        text = render_ticket_template(
            cfg.get("auto_message_template") or "", ticket_display_id(ticket.id, ticket_prefix(db))
        )
        if not text:
            return False
        from app.services.waha_service import WAHAService
        result = await WAHAService.from_phone(phone).send_text(chat["chat_wid"], text)
        now = datetime.utcnow()
        await inbox.upsert_message({
            "chat_id": chat["id"], "chat_wid": chat["chat_wid"], "phone_id": phone.id,
            "message_wid": result.message_id or f"ticket_{ticket.id}_{int(now.timestamp())}",
            "from_me": True, "sender_name": "Tickets", "sender_number": phone.phone_number,
            "body": text, "message_type": "text", "timestamp": now,
        })
        from app.core.ws_manager import ws_manager
        await ws_manager.emit_new_message(
            chat_id=chat["id"], chat_wid=chat["chat_wid"], body=text, from_me=True,
            sender_name="Tickets", sender_number=phone.phone_number or "",
            timestamp=int(now.timestamp()), message_type="text",
            chat_name=chat.get("name") or "", unread_count=0,
        )
        return True
    except Exception as exc:
        logger.warning("Ticket %s auto-message failed: %s", ticket_id, exc)
        return False
    finally:
        db.close()
