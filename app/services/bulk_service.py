import asyncio
import logging
from datetime import datetime

from sqlalchemy.orm import Session

from app.models.bulk_message_job import BulkMessageJob
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

    async def execute_job(self, job_id: int) -> dict:
        job = self.get_job(job_id)
        if not job:
            return {"error": "Job not found"}

        job.status = "running"
        self.db.commit()

        from app.models.phone import Phone
        phone = self.db.query(Phone).filter(Phone.id == job.phone_id).first()
        if not phone:
            job.status = "failed"
            self.db.commit()
            return {"error": "Phone not found"}

        waha = WAHAService(session_name=phone.session_name)
        sent = 0
        failed = 0

        from app.models.chat import Chat
        for chat_id in (job.recipient_chat_ids or []):
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
                    continue
                await waha.send_text(chat.chat_wid, job.message)
                sent += 1
                await asyncio.sleep(0.5)
            except Exception as exc:
                logger.warning("Bulk send failed for chat %s: %s", chat_id, exc)
                failed += 1

        job.sent_count = sent
        job.failed_count = failed
        job.credits_used = sent
        job.status = "done"
        self.db.commit()
        return {"sent": sent, "failed": failed}
