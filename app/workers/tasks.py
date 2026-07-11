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


def _next_occurrence(item, after: datetime):
    """Compute the next send time for a recurring schedule, or None if finished.

    Supports: daily (optionally restricted to days_of_week, every N days),
    weekly (every N weeks), monthly (every N months, optionally pinned to
    day_of_month). Respects end_date.
    """
    from datetime import timedelta

    interval = max(1, item.interval or 1)
    nxt = item.send_at

    if item.repeat == "daily":
        allowed = set(item.days_of_week or [])
        step = timedelta(days=interval)
        nxt = nxt + step
        if allowed:
            # advance day-by-day to the next allowed weekday (Mon=0)
            for _ in range(0, 8):
                if nxt.weekday() in allowed and nxt > after:
                    break
                nxt = nxt + timedelta(days=1)
        else:
            while nxt <= after:
                nxt = nxt + step
    elif item.repeat == "weekly":
        step = timedelta(weeks=interval)
        nxt = nxt + step
        while nxt <= after:
            nxt = nxt + step
    elif item.repeat == "monthly":
        import calendar
        month_index = nxt.month - 1 + interval
        year = nxt.year + month_index // 12
        month = month_index % 12 + 1
        day = item.day_of_month or nxt.day
        day = min(day, calendar.monthrange(year, month)[1])
        nxt = nxt.replace(year=year, month=month, day=day)
    else:
        return None

    if item.end_date and nxt > item.end_date:
        return None
    return nxt


async def run_scheduled_messages() -> None:
    """Send due per-chat scheduled messages; reschedule recurring ones."""
    from app.db.session import SessionLocal
    from app.models.chat import Chat
    from app.models.phone import Phone
    from app.models.scheduled_message import ScheduledMessage
    from app.services.inbox_service import InboxService
    from app.services.waha_service import WAHAService

    db = SessionLocal()
    try:
        now = datetime.utcnow()
        due = (
            db.query(ScheduledMessage)
            .filter(ScheduledMessage.status == "pending", ScheduledMessage.send_at <= now)
            .limit(50)
            .all()
        )
        for item in due:
            chat = db.query(Chat).filter(Chat.id == item.chat_id).first()
            phone = db.query(Phone).filter(Phone.id == chat.phone_id).first() if chat else None
            if not chat or not phone:
                item.status = "failed"
                item.last_error = "Chat or phone missing"
                db.commit()
                continue

            # Daily schedules restricted to specific weekdays: skip disallowed
            # days by rolling forward without sending.
            if item.repeat == "daily" and item.days_of_week and now.weekday() not in item.days_of_week:
                nxt = _next_occurrence(item, now)
                if nxt is None:
                    item.status = "sent"
                else:
                    item.send_at = nxt
                db.commit()
                continue

            try:
                waha = WAHAService(session_name=phone.session_name)
                result = await waha.send_text(chat.chat_wid, item.body)
                InboxService(db).upsert_message({
                    "chat_id": chat.id,
                    "phone_id": phone.id,
                    "message_wid": result.message_id or f"sched_{item.id}_{now.timestamp()}",
                    "from_me": True,
                    "sender_name": "Scheduled",
                    "sender_number": phone.phone_number,
                    "body": item.body,
                    "message_type": "text",
                    "timestamp": now,
                })
                item.sent_count = (item.sent_count or 0) + 1
                nxt = _next_occurrence(item, now)
                if nxt is None:
                    item.status = "sent"
                else:
                    item.send_at = nxt
                item.last_error = None
            except Exception as exc:
                item.status = "failed"
                item.last_error = str(exc)[:500]
                logger.warning("Scheduled message %s failed: %s", item.id, exc)
            db.commit()
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
