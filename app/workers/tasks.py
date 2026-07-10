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
    """Flag tickets that have passed their due date."""
    from app.db.session import SessionLocal
    from app.models.ticket import Ticket, TicketStatus

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
