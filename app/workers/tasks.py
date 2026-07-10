import logging
from datetime import datetime

logger = logging.getLogger(__name__)


async def sync_phone_statuses() -> None:
    """Refresh WAHA session status for all active phones."""
    from app.db.session import SessionLocal
    from app.models.phone import Phone
    from app.services.waha_service import WAHAService

    db = SessionLocal()
    try:
        phones = db.query(Phone).filter(Phone.is_active == True).all()
        for phone in phones:
            try:
                waha = WAHAService(session_name=phone.session_name)
                status = await waha.get_session_status()
                phone.waha_status = status
            except Exception as exc:
                logger.warning("Status check failed for phone %s: %s", phone.id, exc)
        db.commit()
    finally:
        db.close()


async def check_sla_breaches() -> None:
    """Flag tickets past their due date, alert the team, and write audit entries."""
    from app.core.ws_manager import ws_manager
    from app.db.session import SessionLocal
    from app.models.ticket import Ticket, TicketStatus
    from app.services.activity_service import log_activity

    db = SessionLocal()
    try:
        now = datetime.utcnow()
        breached = (
            db.query(Ticket)
            .filter(
                Ticket.due_date <= now,
                Ticket.status.in_([TicketStatus.OPEN, TicketStatus.IN_PROGRESS]),
                Ticket.sla_breached == False,
            )
            .all()
        )
        for ticket in breached:
            ticket.sla_breached = True
            logger.info("SLA breached for ticket %s", ticket.id)
            log_activity(
                db, "sla_breached", entity_type="ticket", entity_id=ticket.id,
                description=f"SLA breached for ticket '{ticket.title}'",
                commit=False,
            )
            await ws_manager.emit_ticket_event("sla_breach", ticket.id, {
                "chat_id": ticket.chat_id,
                "title": ticket.title,
                "assigned_to": ticket.assigned_to,
                "due_date": ticket.due_date.isoformat() if ticket.due_date else None,
            })
        db.commit()
    finally:
        db.close()


async def check_no_reply_timeouts() -> None:
    """Fire 'no_reply_timeout' automation rules for chats waiting on a reply."""
    from datetime import timedelta

    from app.db.session import SessionLocal
    from app.models.activity_log import ActivityLog
    from app.models.chat import Chat
    from app.services.activity_service import log_activity
    from app.services.automation_service import AutomationService

    db = SessionLocal()
    try:
        svc = AutomationService(db)
        rules = svc.get_active_rules("no_reply_timeout")
        if not rules:
            return
        now = datetime.utcnow()
        for rule in rules:
            timeout_min = int((rule.criteria or {}).get("timeout_minutes", 60))
            cutoff = now - timedelta(minutes=timeout_min)
            chats = (
                db.query(Chat)
                .filter(
                    Chat.unread_count > 0,
                    Chat.is_archived == False,
                    Chat.last_message_at.isnot(None),
                    Chat.last_message_at <= cutoff,
                )
                .all()
            )
            for chat in chats:
                already_fired = (
                    db.query(ActivityLog)
                    .filter(
                        ActivityLog.action == "no_reply_timeout_fired",
                        ActivityLog.entity_type == "chat",
                        ActivityLog.entity_id == chat.id,
                        ActivityLog.created_at >= chat.last_message_at,
                    )
                    .first()
                )
                if already_fired:
                    continue
                context = {
                    "chat_id": chat.id,
                    "chat_wid": chat.chat_wid,
                    "chat_name": chat.name,
                    "message": chat.last_message or "",
                    "is_group": chat.is_group,
                    "timeout_minutes": timeout_min,
                }
                if not svc._matches_criteria(rule, context):
                    continue
                taken = await svc._execute_actions(rule, context)
                rule.runs_count = (rule.runs_count or 0) + 1
                db.commit()
                log_activity(
                    db, "no_reply_timeout_fired", entity_type="chat", entity_id=chat.id,
                    description=f"Rule '{rule.name}' fired after {timeout_min}m without reply",
                    metadata={"rule_id": rule.id, "actions": taken},
                )
    finally:
        db.close()


async def run_scheduled_bulk_jobs() -> None:
    """Execute bulk message jobs that are due."""
    from app.db.session import SessionLocal
    from app.models.bulk_message_job import BulkMessageJob
    from app.services.bulk_service import BulkService

    db = SessionLocal()
    try:
        now = datetime.utcnow()
        from sqlalchemy import or_
        jobs = (
            db.query(BulkMessageJob)
            .filter(
                BulkMessageJob.status == "pending",
                or_(
                    BulkMessageJob.scheduled_at.is_(None),
                    BulkMessageJob.scheduled_at <= now,
                ),
            )
            .all()
        )
        for job in jobs:
            logger.info("Running scheduled bulk job %s", job.id)
            await BulkService(db).execute_job(job.id)
    finally:
        db.close()
