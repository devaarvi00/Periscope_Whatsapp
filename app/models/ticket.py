import enum
from datetime import datetime

from sqlalchemy import DateTime, Enum as SAEnum, ForeignKey, Integer, JSON, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin


class TicketStatus(str, enum.Enum):
    OPEN = "open"
    IN_PROGRESS = "in_progress"
    RESOLVED = "resolved"
    CLOSED = "closed"


class TicketPriority(str, enum.Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    URGENT = "urgent"


class Ticket(Base, TimestampMixin):
    __tablename__ = "tickets"

    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    # chat_id references MongoDB chat.id (integer) — no FK constraint since chats live in MongoDB
    chat_id: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    # message_wid is the WAHA message ID string (was message_id FK to MySQL messages)
    message_wid: Mapped[str | None] = mapped_column(String(200), nullable=True, index=True)
    title: Mapped[str] = mapped_column(String(500), nullable=False)
    description: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[TicketStatus] = mapped_column(SAEnum(TicketStatus), default=TicketStatus.OPEN)
    priority: Mapped[TicketPriority] = mapped_column(SAEnum(TicketPriority), default=TicketPriority.MEDIUM)
    assigned_to: Mapped[int | None] = mapped_column(ForeignKey("agents.id"), nullable=True)
    created_by: Mapped[int | None] = mapped_column(ForeignKey("agents.id"), nullable=True)
    due_date: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    sla_breached: Mapped[bool] = mapped_column(default=False)
    # Set once the assignee has been told the ticket is overdue; cleared when
    # the due date moves back into the future so it can fire again.
    overdue_notified_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    custom_properties: Mapped[dict | None] = mapped_column("custom_props", JSON, nullable=True, default=None)


class TicketLabel(Base):
    __tablename__ = "ticket_labels"

    ticket_id: Mapped[int] = mapped_column(ForeignKey("tickets.id"), primary_key=True)
    label_id: Mapped[int] = mapped_column(ForeignKey("labels.id"), primary_key=True)


class TicketMessage(Base):
    """Extra messages attached to a ticket (e.g. replies quoting a ticketed
    message). The ticket's originating message stays in Ticket.message_wid."""
    __tablename__ = "ticket_messages"

    id: Mapped[int] = mapped_column(primary_key=True)
    ticket_id: Mapped[int] = mapped_column(ForeignKey("tickets.id", ondelete="CASCADE"), index=True)
    chat_id: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    message_wid: Mapped[str] = mapped_column(String(200), nullable=False, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
