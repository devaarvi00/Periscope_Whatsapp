import logging
from datetime import datetime, timedelta

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.models.agent import Agent
from app.models.ticket import Ticket, TicketStatus

logger = logging.getLogger(__name__)


class AnalyticsService:
    def __init__(self, db: Session) -> None:
        self.db = db

    async def _response_stats(self, since: datetime) -> tuple[float, dict[int, float]]:
        """Compute first-response times from MongoDB messages."""
        from app.services.mongo_chat_service import MongoInboxService
        inbox = MongoInboxService()
        msgs = await (
            inbox.db.messages.find(
                {"timestamp": {"$gte": since}},
                {"chat_id": 1, "from_me": 1, "timestamp": 1, "sent_by_agent_id": 1},
            )
            .sort([("chat_id", 1), ("timestamp", 1)])
            .limit(100_000)
            .to_list(100_000)
        )
        deltas: list[float] = []
        per_agent: dict[int, list[float]] = {}
        current_chat = None
        pending_inbound: datetime | None = None
        for m in msgs:
            chat_id = m.get("chat_id")
            from_me = m.get("from_me", False)
            ts = m.get("timestamp")
            agent_id = m.get("sent_by_agent_id")
            if not ts:
                continue
            if chat_id != current_chat:
                current_chat = chat_id
                pending_inbound = None
            if not from_me:
                if pending_inbound is None:
                    pending_inbound = ts
            elif pending_inbound is not None:
                minutes = max(0.0, (ts - pending_inbound).total_seconds() / 60)
                deltas.append(minutes)
                if agent_id:
                    per_agent.setdefault(agent_id, []).append(minutes)
                pending_inbound = None

        if deltas:
            deltas.sort()
            n = len(deltas)
            median = deltas[n // 2] if n % 2 else (deltas[n // 2 - 1] + deltas[n // 2]) / 2
        else:
            median = 0.0
        agent_avgs = {
            aid: round(sum(vals) / len(vals), 1) for aid, vals in per_agent.items()
        }
        return round(median, 1), agent_avgs

    async def get_dashboard_metrics(self) -> dict:
        from app.services.mongo_chat_service import MongoInboxService
        inbox = MongoInboxService()
        total_chats = await inbox.db.chats.count_documents({})
        unread_chats = await inbox.db.chats.count_documents({"unread_count": {"$gt": 0}})
        flagged_chats = await inbox.db.chats.count_documents({"is_flagged": True})
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

    async def get_message_metrics(self, days: int = 7) -> dict:
        from app.services.mongo_chat_service import MongoInboxService
        inbox = MongoInboxService()
        since = datetime.utcnow() - timedelta(days=days)
        outgoing = await inbox.db.messages.count_documents({"from_me": True, "timestamp": {"$gte": since}})
        incoming = await inbox.db.messages.count_documents({"from_me": False, "timestamp": {"$gte": since}})
        flagged = await inbox.db.messages.count_documents({"is_flagged": True, "timestamp": {"$gte": since}})
        pipeline = [
            {"$match": {"timestamp": {"$gte": since}}},
            {"$group": {"_id": "$chat_id"}},
            {"$count": "n"},
        ]
        result = await inbox.db.messages.aggregate(pipeline).to_list(1)
        active_chats = result[0]["n"] if result else 0
        median_response, _ = await self._response_stats(since)
        return {
            "active_chats": active_chats,
            "outgoing_messages": outgoing,
            "incoming_messages": incoming,
            "flagged_messages": flagged,
            "median_first_response_minutes": median_response,
        }

    def get_ticket_metrics(self) -> dict:
        open_count = self.db.query(func.count(Ticket.id)).filter(Ticket.status == TicketStatus.OPEN).scalar() or 0
        in_progress = self.db.query(func.count(Ticket.id)).filter(Ticket.status == TicketStatus.IN_PROGRESS).scalar() or 0
        resolved = self.db.query(func.count(Ticket.id)).filter(Ticket.status == TicketStatus.RESOLVED).scalar() or 0
        unassigned = self.db.query(func.count(Ticket.id)).filter(
            Ticket.assigned_to.is_(None), Ticket.status.in_([TicketStatus.OPEN, TicketStatus.IN_PROGRESS])
        ).scalar() or 0
        sla_breached = self.db.query(func.count(Ticket.id)).filter(Ticket.sla_breached == True).scalar() or 0

        resolved_tickets = (
            self.db.query(Ticket.created_at, Ticket.resolved_at)
            .filter(Ticket.resolved_at.isnot(None))
            .order_by(Ticket.resolved_at.desc())
            .limit(500)
            .all()
        )
        if resolved_tickets:
            hours = [
                max(0.0, (r - c).total_seconds() / 3600)
                for c, r in resolved_tickets if c and r
            ]
            avg_resolution = round(sum(hours) / len(hours), 1) if hours else 0.0
        else:
            avg_resolution = 0.0

        return {
            "open": open_count,
            "in_progress": in_progress,
            "resolved": resolved,
            "unassigned": unassigned,
            "avg_resolution_hours": avg_resolution,
            "sla_breached": sla_breached,
        }

    async def get_agent_performance(self, days: int = 7) -> list[dict]:
        from app.services.mongo_chat_service import MongoInboxService
        inbox = MongoInboxService()
        since = datetime.utcnow() - timedelta(days=days)
        agents = self.db.query(Agent).filter(Agent.is_active == True).all()
        _, agent_response_times = await self._response_stats(since)
        result = []
        for agent in agents:
            msgs_sent = await inbox.db.messages.count_documents(
                {"sent_by_agent_id": agent.id, "timestamp": {"$gte": since}}
            )
            open_tickets = self.db.query(func.count(Ticket.id)).filter(
                Ticket.assigned_to == agent.id,
                Ticket.status.in_([TicketStatus.OPEN, TicketStatus.IN_PROGRESS])
            ).scalar() or 0
            chats_assigned = await inbox.db.chats.count_documents({"assigned_to": agent.id})
            result.append({
                "agent_id": agent.id,
                "agent_name": agent.name,
                "messages_sent": msgs_sent,
                "open_tickets": open_tickets,
                "chats_assigned": chats_assigned,
                "response_time_minutes": agent_response_times.get(agent.id, 0.0),
            })
        return result
