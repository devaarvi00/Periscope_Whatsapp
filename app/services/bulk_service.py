import asyncio
import logging
from datetime import datetime

from sqlalchemy.orm import Session

from app.core.config import settings
from app.models.bulk_message_job import BulkMessageJob
from app.models.contact import Contact
from app.services.mongo_chat_service import MongoInboxService
from app.services.waha_service import WAHAService

logger = logging.getLogger(__name__)

# A running job with no job/log activity for this long is considered dead.
STALE_RUNNING_MINUTES = 30


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
        return 0

    def credits_remaining(self) -> int:
        return 999999

    def _render_variables(self, template: str, chat: dict) -> str:
        """Personalize message per recipient: {{name}}, {{phone}}, {{company}}."""
        number = chat.get("chat_wid", "").split("@")[0]
        name = chat.get("name") or number
        company = ""
        contact = self.db.query(Contact).filter(Contact.phone_number == number).first()
        if contact:
            name = contact.name or name
            company = contact.company or ""
        text = template
        for key, val in (("name", name), ("phone", number), ("company", company)):
            text = text.replace("{{" + key + "}}", val).replace("{{ " + key + " }}", val)
        return text

    def claim_job(self, job_id: int) -> bool:
        """Atomically move a job pending → running. Only one caller wins, so
        the scheduler tick and a manual "Send now" can't both send it."""
        rows = (
            self.db.query(BulkMessageJob)
            .filter(BulkMessageJob.id == job_id, BulkMessageJob.status == "pending")
            .update(
                {BulkMessageJob.status: "running", BulkMessageJob.updated_at: datetime.utcnow()},
                synchronize_session=False,
            )
        )
        self.db.commit()
        return rows == 1

    def fail_stale_running_jobs(self, stale_after_minutes: int = STALE_RUNNING_MINUTES) -> int:
        """Mark jobs stuck in 'running' (process crashed / restarted mid-run)
        as failed. A job is stale when neither the job row nor any of its
        delivery logs changed for `stale_after_minutes`. They are failed rather
        than re-queued so recipients never get a duplicate send."""
        from datetime import timedelta

        from sqlalchemy import func

        from app.models.bulk_message_job import BulkMessageLog

        cutoff = datetime.utcnow() - timedelta(minutes=stale_after_minutes)
        running = self.db.query(BulkMessageJob).filter(BulkMessageJob.status == "running").all()
        failed = 0
        for job in running:
            last_log = (
                self.db.query(func.max(BulkMessageLog.created_at))
                .filter(BulkMessageLog.job_id == job.id)
                .scalar()
            )
            stamps = [d for d in (job.updated_at, last_log, job.created_at) if d]
            if stamps and max(stamps) > cutoff:
                continue
            rows = (
                self.db.query(BulkMessageJob)
                .filter(BulkMessageJob.id == job.id, BulkMessageJob.status == "running")
                .update(
                    {BulkMessageJob.status: "failed",
                     BulkMessageJob.error_message: "Interrupted: no progress for "
                                                   f"{stale_after_minutes} minutes (server restart?)"},
                    synchronize_session=False,
                )
            )
            failed += rows
            if rows:
                logger.warning("Bulk job %s was stuck in 'running' — marked failed", job.id)
        self.db.commit()
        return failed

    async def execute_job(self, job_id: int) -> dict:
        if not self.claim_job(job_id):
            job = self.get_job(job_id)
            if not job:
                return {"error": "Job not found"}
            logger.info("Bulk job %s not claimed (status=%s) — skipping", job_id, job.status)
            return {"error": f"Job is {job.status}, not pending"}

        job = self.get_job(job_id)
        self.db.refresh(job)
        recipients = job.recipient_chat_ids or []

        from app.services import operation_log as oplog
        log_uid = oplog.record(
            "scheduled", f"Bulk message: {job.name}",
            pending=len(recipients), status="pending",
            performed_by_id=job.created_by, performed_by="Scheduler",
            details={"bulk_job_id": job.id, "run": (job.runs_count or 0) + 1,
                     "message_type": job.message_type, "recipients": len(recipients),
                     "repeat": job.repeat, "message_preview": oplog.preview(job.message)},
        )

        from app.models.phone import Phone
        phone = self.db.query(Phone).filter(Phone.id == job.phone_id).first()
        if not phone:
            job.status = "failed"
            job.error_message = "Phone not found"
            self.db.commit()
            oplog.update(log_uid, failed=len(recipients), pending=0, details={"error": "Phone not found"})
            return {"error": "Phone not found"}

        from app.models.bulk_message_job import BulkMessageLog

        waha = WAHAService.from_phone(phone)
        inbox = MongoInboxService()
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
                chat_id_str = str(chat_id)
                if "@" in chat_id_str:
                    # WID provided — find by chat_wid in MongoDB
                    docs = await inbox.db.chats.find(
                        {"chat_wid": chat_id_str, "phone_id": job.phone_id}
                    ).to_list(1)
                    if not docs:
                        docs = await inbox.db.chats.find({"chat_wid": chat_id_str}).to_list(1)
                    chat = docs[0] if docs else None
                else:
                    chat = await inbox.get_chat_by_id(int(chat_id_str))

                if not chat:
                    logger.warning("Bulk: chat %s not found, skipping", chat_id)
                    failed += 1
                    log_row.status = "skipped"
                    log_row.error = f"Chat {chat_id} not found"
                    self.db.add(log_row)
                    self.db.commit()
                    continue

                log_row.chat_id = chat["id"]
                log_row.chat_name = chat.get("name") or chat.get("chat_wid") or ""
                text = self._render_variables(job.message, chat)
                if settings.environment == "development" and phone.waha_status != "WORKING":
                    # Dev-only simulation when no WhatsApp session is connected.
                    logger.warning("WAHA session %s status is %s (not WORKING) in development environment. Mocking bulk send to %s.", phone.session_name, phone.waha_status, chat.get("chat_wid"))
                    await asyncio.sleep(0.01)
                else:
                    # Real send: any failure is counted as failed (below),
                    # in every environment.
                    if job.message_type == "image" and job.media_url:
                        await waha.send_image(chat["chat_wid"], job.media_url, caption=text)
                    elif job.message_type == "file" and job.media_url:
                        await waha.send_file(chat["chat_wid"], job.media_url, caption=text)
                    elif job.message_type == "poll" and job.poll_options:
                        await waha.send_poll(chat["chat_wid"], text, [str(o) for o in job.poll_options])
                    else:
                        await waha.send_text(chat["chat_wid"], text)
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

        oplog.update(
            log_uid, success=sent, failed=failed, pending=0,
            details={"cancelled": job.status == "cancelled",
                     "not_sent": max(0, len(recipients) - sent - failed),
                     "next_run": job.scheduled_at if job.status == "pending" else None},
        )

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
