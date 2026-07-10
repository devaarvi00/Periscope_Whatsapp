import logging
from datetime import datetime, timedelta

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.models.agent import Agent
from app.models.chat import Chat
from app.models.message import Message
from app.models.ticket import Ticket, TicketStatus

logger = logging.getLogger(__name__)


class AnalyticsService:
    def __init__(self, db: Session) -> None:
        self.db = db

    def get_dashboard_metrics(self) -> dict:
        total_chats = self.db.query(func.count(Chat.id)).scalar() or 0
        unread_chats = self.db.query(func.count(Chat.id)).filter(Chat.unread_count > 0).scalar() or 0
        flagged_chats = self.db.query(func.count(Chat.id)).filter(Chat.is_flagged == True).scalar() or 0
        open_tickets = self.db.query(func.count(Ticket.id)).filter(Ticket.status == TicketStatus.OPEN).scalar() or 0
        in_progress = self.db.query(func.count(Ticket.id)).filter(Ticket.status == TicketStatus.IN_PROGRESS).scalar() or 0
        active_agents = self.db.query(func.count(Agent.id)).filter(Agent.is_active == True).scalar() or 0
        return {
            "total_chats": total_chats,
            "unread_chats": unread_chats,
            "flagged_chats": flagged_chats,
            "open_tickets": open_tickets,
            "in_progress_tickets": in_progress,
            "online_agents": active_agents,
        }

    def get_message_metrics(self, days: int = 7) -> dict:
        since = datetime.utcnow() - timedelta(days=days)
        outgoing = self.db.query(func.count(Message.id)).filter(
            Message.from_me == True, Message.timestamp >= since
        ).scalar() or 0
        incoming = self.db.query(func.count(Message.id)).filter(
            Message.from_me == False, Message.timestamp >= since
        ).scalar() or 0
        flagged = self.db.query(func.count(Message.id)).filter(
            Message.is_flagged == True, Message.timestamp >= since
        ).scalar() or 0
        active_chats = self.db.query(func.count(func.distinct(Message.chat_id))).filter(
            Message.timestamp >= since
        ).scalar() or 0
        return {
            "active_chats": active_chats,
            "outgoing_messages": outgoing,
            "incoming_messages": incoming,
            "flagged_messages": flagged,
            "median_first_response_minutes": 0.0,
        }

    def get_ticket_metrics(self) -> dict:
        open_count = self.db.query(func.count(Ticket.id)).filter(Ticket.status == TicketStatus.OPEN).scalar() or 0
        in_progress = self.db.query(func.count(Ticket.id)).filter(Ticket.status == TicketStatus.IN_PROGRESS).scalar() or 0
        resolved = self.db.query(func.count(Ticket.id)).filter(Ticket.status == TicketStatus.RESOLVED).scalar() or 0
        unassigned = self.db.query(func.count(Ticket.id)).filter(
            Ticket.assigned_to.is_(None), Ticket.status.in_([TicketStatus.OPEN, TicketStatus.IN_PROGRESS])
        ).scalar() or 0
        sla_breached = self.db.query(func.count(Ticket.id)).filter(Ticket.sla_breached == True).scalar() or 0
        return {
            "open": open_count,
            "in_progress": in_progress,
            "resolved": resolved,
            "unassigned": unassigned,
            "avg_resolution_hours": 0.0,
            "sla_breached": sla_breached,
        }

    def get_agent_performance(self, days: int = 7) -> list[dict]:
        since = datetime.utcnow() - timedelta(days=days)
        agents = self.db.query(Agent).filter(Agent.is_active == True).all()
        result = []
        for agent in agents:
            msgs_sent = self.db.query(func.count(Message.id)).filter(
                Message.sent_by_agent_id == agent.id, Message.timestamp >= since
            ).scalar() or 0
            open_tickets = self.db.query(func.count(Ticket.id)).filter(
                Ticket.assigned_to == agent.id,
                Ticket.status.in_([TicketStatus.OPEN, TicketStatus.IN_PROGRESS])
            ).scalar() or 0
            chats_assigned = self.db.query(func.count(Chat.id)).filter(
                Chat.assigned_to == agent.id
            ).scalar() or 0
            result.append({
                "agent_id": agent.id,
                "agent_name": agent.name,
                "messages_sent": msgs_sent,
                "open_tickets": open_tickets,
                "chats_assigned": chats_assigned,
                "response_time_minutes": 0.0,
            })
        return result
