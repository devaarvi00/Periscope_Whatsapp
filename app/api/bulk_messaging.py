from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.schemas.ai_agent import BulkJobCreate, BulkJobOut
from app.services.bulk_service import BulkService

router = APIRouter(prefix="/bulk", tags=["bulk-messaging"])


@router.get("/jobs", response_model=list[BulkJobOut])
def list_jobs(db: Session = Depends(get_db)):
    return BulkService(db).list_jobs()


@router.post("/jobs", response_model=BulkJobOut, status_code=201)
def create_job(req: BulkJobCreate, db: Session = Depends(get_db)):
    from datetime import datetime
    scheduled_at = None
    if req.scheduled_at:
        try:
            scheduled_at = datetime.fromisoformat(req.scheduled_at)
        except ValueError:
            raise HTTPException(400, "Invalid scheduled_at format (use ISO 8601)")
    return BulkService(db).create_job(
        name=req.name,
        message=req.message,
        phone_id=req.phone_id,
        recipient_chat_ids=req.recipient_chat_ids,
        scheduled_at=scheduled_at,
    )


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
