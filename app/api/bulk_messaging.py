from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from pydantic import BaseModel
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
    if req.repeat not in ("none", "daily", "weekly", "monthly"):
        raise HTTPException(400, "repeat must be none|daily|weekly|monthly")
    if req.repeat != "none" and not scheduled_at:
        raise HTTPException(400, "Repeating broadcasts need a first send time (scheduled_at)")
    end_date = None
    if req.end_date:
        try:
            end_date = datetime.fromisoformat(req.end_date)
        except ValueError:
            raise HTTPException(400, "Invalid end_date (use ISO 8601)")
    job = BulkService(db).create_job(
        name=req.name,
        message=req.message,
        phone_id=req.phone_id,
        recipient_chat_ids=req.recipient_chat_ids,
        scheduled_at=scheduled_at,
        message_type=req.message_type,
        media_url=req.media_url,
        poll_options=req.poll_options,
        delay_seconds=max(1, min(req.delay_seconds, 60)),
        repeat=req.repeat,
        interval=max(1, min(req.interval, 30)),
        days_of_week=sorted({d for d in (req.days_of_week or []) if 0 <= int(d) <= 6}) or None,
        day_of_month=req.day_of_month if req.day_of_month and 1 <= req.day_of_month <= 31 else None,
        end_date=end_date,
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


@router.post("/jobs/{job_id}/stop")
def stop_job(
    job_id: int,
    db: Session = Depends(get_db),
    agent: Agent = Depends(get_current_agent),
):
    """Stop a pending or running campaign (also ends its repeats)."""
    job = BulkService(db).get_job(job_id)
    if not job:
        raise HTTPException(404, "Job not found")
    if job.status not in ("pending", "running"):
        raise HTTPException(400, "Only pending or running jobs can be stopped")
    job.status = "cancelled"
    db.commit()
    log_activity(
        db, "bulk_job_stopped", entity_type="bulk_job", entity_id=job.id,
        agent_id=agent.id, description=f"Bulk job '{job.name}' stopped",
    )
    return {"ok": True}


@router.get("/jobs/{job_id}/logs")
def job_logs(job_id: int, db: Session = Depends(get_db)):
    """Per-recipient delivery log for a campaign."""
    from app.models.bulk_message_job import BulkMessageLog
    job = BulkService(db).get_job(job_id)
    if not job:
        raise HTTPException(404, "Job not found")
    rows = (
        db.query(BulkMessageLog)
        .filter(BulkMessageLog.job_id == job_id)
        .order_by(BulkMessageLog.run_number.desc(), BulkMessageLog.id)
        .limit(2000)
        .all()
    )
    return {
        "job": {"id": job.id, "name": job.name, "status": job.status,
                "sent": job.sent_count, "failed": job.failed_count,
                "runs": job.runs_count},
        "logs": [
            {"chat_id": r.chat_id, "chat_name": r.chat_name, "status": r.status,
             "error": r.error, "run": r.run_number,
             "at": r.created_at.isoformat() if r.created_at else None}
            for r in rows
        ],
    }


# ── Message templates ─────────────────────────────────────────────────────────

class TemplateBody(BaseModel):
    name: str
    body: str


@router.get("/templates")
def list_templates(db: Session = Depends(get_db)):
    from app.models.bulk_message_job import MessageTemplate
    rows = db.query(MessageTemplate).order_by(MessageTemplate.name).all()
    return [{"id": t.id, "name": t.name, "body": t.body} for t in rows]


@router.post("/templates", status_code=201)
def create_template(
    req: TemplateBody,
    db: Session = Depends(get_db),
    agent: Agent = Depends(get_current_agent),
):
    from app.models.bulk_message_job import MessageTemplate
    if not req.name.strip() or not req.body.strip():
        raise HTTPException(400, "Name and body are required")
    if db.query(MessageTemplate).filter(MessageTemplate.name == req.name.strip()).first():
        raise HTTPException(400, "A template with this name already exists")
    t = MessageTemplate(name=req.name.strip()[:255], body=req.body, created_by=agent.id)
    db.add(t)
    db.commit()
    db.refresh(t)
    return {"id": t.id, "name": t.name, "body": t.body}


@router.patch("/templates/{template_id}")
def update_template(
    template_id: int,
    req: TemplateBody,
    db: Session = Depends(get_db),
    _agent: Agent = Depends(get_current_agent),
):
    from app.models.bulk_message_job import MessageTemplate
    t = db.query(MessageTemplate).filter(MessageTemplate.id == template_id).first()
    if not t:
        raise HTTPException(404, "Template not found")
    t.name = req.name.strip()[:255] or t.name
    t.body = req.body or t.body
    db.commit()
    return {"id": t.id, "name": t.name, "body": t.body}


@router.delete("/templates/{template_id}", status_code=204)
def delete_template(
    template_id: int,
    db: Session = Depends(get_db),
    _agent: Agent = Depends(get_current_agent),
):
    from app.models.bulk_message_job import MessageTemplate
    t = db.query(MessageTemplate).filter(MessageTemplate.id == template_id).first()
    if not t:
        raise HTTPException(404, "Template not found")
    db.delete(t)
    db.commit()


# ── Saved chat lists ──────────────────────────────────────────────────────────

class ChatListBody(BaseModel):
    name: str
    chat_ids: list[int]


@router.get("/chat-lists")
def list_chat_lists(db: Session = Depends(get_db)):
    from app.models.bulk_message_job import SavedChatList
    rows = db.query(SavedChatList).order_by(SavedChatList.name).all()
    return [{"id": l.id, "name": l.name, "chat_ids": l.chat_ids or [],
             "count": len(l.chat_ids or [])} for l in rows]


@router.post("/chat-lists", status_code=201)
def create_chat_list(
    req: ChatListBody,
    db: Session = Depends(get_db),
    agent: Agent = Depends(get_current_agent),
):
    from app.models.bulk_message_job import SavedChatList
    if not req.name.strip():
        raise HTTPException(400, "Name is required")
    if not req.chat_ids:
        raise HTTPException(400, "Select at least one chat")
    if db.query(SavedChatList).filter(SavedChatList.name == req.name.strip()).first():
        raise HTTPException(400, "A chat list with this name already exists")
    l = SavedChatList(name=req.name.strip()[:255], chat_ids=req.chat_ids, created_by=agent.id)
    db.add(l)
    db.commit()
    db.refresh(l)
    return {"id": l.id, "name": l.name, "chat_ids": l.chat_ids, "count": len(l.chat_ids)}


@router.patch("/chat-lists/{list_id}")
def update_chat_list(
    list_id: int,
    req: ChatListBody,
    db: Session = Depends(get_db),
    _agent: Agent = Depends(get_current_agent),
):
    from app.models.bulk_message_job import SavedChatList
    l = db.query(SavedChatList).filter(SavedChatList.id == list_id).first()
    if not l:
        raise HTTPException(404, "Chat list not found")
    l.name = req.name.strip()[:255] or l.name
    if req.chat_ids:
        l.chat_ids = req.chat_ids
    db.commit()
    return {"id": l.id, "name": l.name, "chat_ids": l.chat_ids, "count": len(l.chat_ids or [])}


@router.delete("/chat-lists/{list_id}", status_code=204)
def delete_chat_list(
    list_id: int,
    db: Session = Depends(get_db),
    _agent: Agent = Depends(get_current_agent),
):
    from app.models.bulk_message_job import SavedChatList
    l = db.query(SavedChatList).filter(SavedChatList.id == list_id).first()
    if not l:
        raise HTTPException(404, "Chat list not found")
    db.delete(l)
    db.commit()
