import asyncio
import logging
from datetime import datetime

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.core.config import settings
from app.models.bulk_message_job import BulkMessageJob
from app.models.chat import Chat
from app.models.contact import Contact
from app.services.waha_service import WAHAService

logger = logging.getLogger(__name__)


class BulkService:
    def __init__(self, db: Session) -> None:
        self.db = db

    def create_job(self, **kwargs) -> BulkMessageJob:
        job = BulkMessageJob(**kwargs)
        self.db.add(job)
        self.db.commit()
        self.db.refresh(job)
        return job

    def get_job(self, job_id: int) -> BulkMessageJob | None:
        return self.db.query(BulkMessageJob).filter(BulkMessageJob.id == job_id).first()

    def list_jobs(self, limit: int = 20) -> list[BulkMessageJob]:
        return self.db.query(BulkMessageJob).order_by(
            BulkMessageJob.created_at.desc()
        ).limit(limit).all()

    def credits_used_this_month(self) -> int:
        month_start = datetime.utcnow().replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        used = (
            self.db.query(func.coalesce(func.sum(BulkMessageJob.credits_used), 0))
            .filter(BulkMessageJob.created_at >= month_start)
            .scalar()
        )
        return int(used or 0)

    def credits_remaining(self) -> int:
        return max(0, settings.bulk_message_credits_per_month - self.credits_used_this_month())

    def _render_variables(self, template: str, chat: Chat) -> str:
        """Personalize message per recipient: {{name}}, {{phone}}, {{company}}."""
        number = chat.chat_wid.split("@")[0]
        name = chat.name or number
        company = ""
        contact = None
        if chat.contact_id:
            contact = self.db.query(Contact).filter(Contact.id == chat.contact_id).first()
        if not contact:
            contact = self.db.query(Contact).filter(Contact.phone_number == number).first()
        if contact:
            name = contact.name or name
            company = contact.company or ""
        text = template
        for key, val in (("name", name), ("phone", number), ("company", company)):
            text = text.replace("{{" + key + "}}", val).replace("{{ " + key + " }}", val)
        return text

    async def execute_job(self, job_id: int) -> dict:
        job = self.get_job(job_id)
        if not job:
            return {"error": "Job not found"}

        recipients = job.recipient_chat_ids or []
        remaining = self.credits_remaining()
        if len(recipients) > remaining:
            job.status = "failed"
            job.error_message = (
                f"Insufficient credits: job needs {len(recipients)}, "
                f"{remaining} left this month"
            )
            self.db.commit()
            return {"error": job.error_message}

        job.status = "running"
        self.db.commit()

        from app.models.phone import Phone
        phone = self.db.query(Phone).filter(Phone.id == job.phone_id).first()
        if not phone:
            job.status = "failed"
            job.error_message = "Phone not found"
            self.db.commit()
            return {"error": "Phone not found"}

        from app.models.bulk_message_job import BulkMessageLog

        waha = WAHAService(session_name=phone.session_name)
        sent = 0
        failed = 0
        run_no = (job.runs_count or 0) + 1
        delay = max(0.5, float(job.delay_seconds or 1))

        for chat_id in recipients:
            # Re-check status so a Stop request takes effect mid-run
            self.db.refresh(job)
            if job.status == "cancelled":
                break
            log_row = BulkMessageLog(job_id=job.id, run_number=run_no)
            try:
                # recipient_chat_ids stores WIDs (e.g. "918320356326@c.us") or int IDs
                chat_id_str = str(chat_id)
                if "@" in chat_id_str:
                    chat = self.db.query(Chat).filter(Chat.chat_wid == chat_id_str).first()
                else:
                    chat = self.db.query(Chat).filter(Chat.id == int(chat_id_str)).first()
                if not chat:
                    logger.warning("Bulk: chat %s not found, skipping", chat_id)
                    failed += 1
                    log_row.status = "skipped"
                    log_row.error = f"Chat {chat_id} not found"
                    self.db.add(log_row)
                    self.db.commit()
                    continue

                log_row.chat_id = chat.id
                log_row.chat_name = chat.name or chat.chat_wid
                text = self._render_variables(job.message, chat)
                if job.message_type == "image" and job.media_url:
                    await waha.send_image(chat.chat_wid, job.media_url, caption=text)
                elif job.message_type == "file" and job.media_url:
                    await waha.send_file(chat.chat_wid, job.media_url, caption=text)
                elif job.message_type == "poll" and job.poll_options:
                    await waha.send_poll(chat.chat_wid, text, [str(o) for o in job.poll_options])
                else:
                    await waha.send_text(chat.chat_wid, text)
                sent += 1
                log_row.status = "sent"
                self.db.add(log_row)
                self.db.commit()
                await asyncio.sleep(delay)
            except Exception as exc:
                logger.warning("Bulk send failed for chat %s: %s", chat_id, exc)
                failed += 1
                log_row.status = "failed"
                log_row.error = str(exc)[:500]
                self.db.add(log_row)
                self.db.commit()

        job.sent_count = (job.sent_count or 0) + sent
        job.failed_count = (job.failed_count or 0) + failed
        job.credits_used = (job.credits_used or 0) + sent
        job.runs_count = run_no

        # Repeating broadcasts: line up the next run instead of finishing
        if job.status != "cancelled" and (job.repeat or "none") != "none":
            nxt = self._next_run(job)
            if nxt:
                job.scheduled_at = nxt
                job.status = "pending"
            else:
                job.status = "done"
        elif job.status != "cancelled":
            job.status = "done"
        self.db.commit()

        from app.services.activity_service import log_activity
        log_activity(
            self.db, "bulk_job_completed", entity_type="bulk_job", entity_id=job.id,
            description=f"Bulk job '{job.name}' run #{run_no}: {sent} sent, {failed} failed"
                        + (f", next run {job.scheduled_at}" if job.status == "pending" else ""),
            metadata={"sent": sent, "failed": failed, "credits_used": sent, "run": run_no},
        )
        return {"sent": sent, "failed": failed}

    def _next_run(self, job) -> datetime | None:
        """Next occurrence for a repeating broadcast (reuses scheduler math)."""
        from app.workers.tasks import _next_occurrence

        class _Shim:
            pass

        shim = _Shim()
        shim.send_at = job.scheduled_at or datetime.utcnow()
        shim.repeat = job.repeat
        shim.interval = job.interval or 1
        shim.days_of_week = job.days_of_week
        shim.day_of_month = job.day_of_month
        shim.end_date = job.end_date
        return _next_occurrence(shim, datetime.utcnow())
