from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from sqlalchemy.orm import Session

from app.api.auth import get_current_agent
from app.core.config import settings
from app.db.session import get_db
from app.models.agent import Agent
from app.schemas.ai_agent import BulkJobCreate, BulkJobOut
from app.services.activity_service import log_activity
from app.services.bulk_service import BulkService

router = APIRouter(prefix="/bulk", tags=["bulk-messaging"])


@router.get("/jobs", response_model=list[BulkJobOut])
def list_jobs(db: Session = Depends(get_db)):
    return BulkService(db).list_jobs()


@router.get("/credits")
def get_credits(db: Session = Depends(get_db)):
    svc = BulkService(db)
    return {
        "monthly_limit": settings.bulk_message_credits_per_month,
        "used_this_month": svc.credits_used_this_month(),
        "remaining": svc.credits_remaining(),
    }


@router.post("/jobs", response_model=BulkJobOut, status_code=201)
def create_job(
    req: BulkJobCreate,
    db: Session = Depends(get_db),
    agent: Agent = Depends(get_current_agent),
):
    from datetime import datetime
    scheduled_at = None
    if req.scheduled_at:
        try:
            scheduled_at = datetime.fromisoformat(req.scheduled_at)
        except ValueError:
            raise HTTPException(400, "Invalid scheduled_at format (use ISO 8601)")
    if req.message_type == "poll" and not (req.poll_options and len(req.poll_options) >= 2):
        raise HTTPException(400, "Polls need at least 2 options")
    if req.message_type in ("image", "file") and not req.media_url:
        raise HTTPException(400, f"media_url is required for {req.message_type} messages")
    job = BulkService(db).create_job(
        name=req.name,
        message=req.message,
        phone_id=req.phone_id,
        recipient_chat_ids=req.recipient_chat_ids,
        scheduled_at=scheduled_at,
        message_type=req.message_type,
        media_url=req.media_url,
        poll_options=req.poll_options,
        created_by=agent.id,
    )
    log_activity(
        db, "bulk_job_created", entity_type="bulk_job", entity_id=job.id,
        agent_id=agent.id,
        description=f"Bulk job '{job.name}' ({job.message_type}) for {len(req.recipient_chat_ids)} recipients",
    )
    return job


async def _run_bulk_job(job_id: int) -> None:
    from app.db.session import SessionLocal
    db = SessionLocal()
    try:
        await BulkService(db).execute_job(job_id)
    except Exception as exc:
        import logging
        logging.getLogger(__name__).exception("Bulk job %s error: %s", job_id, exc)
    finally:
        db.close()


@router.post("/jobs/{job_id}/send")
async def send_job(job_id: int, background: BackgroundTasks, db: Session = Depends(get_db)):
    job = BulkService(db).get_job(job_id)
    if not job:
        raise HTTPException(404, "Job not found")
    background.add_task(_run_bulk_job, job_id)
    return {"ok": True, "message": "Bulk job queued"}
