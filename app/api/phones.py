from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from sqlalchemy import delete

from app.core.config import settings
from app.db.session import get_db
from app.models.phone import Phone
from app.schemas.inbox import PhoneCreate, PhoneOut
from app.services.waha_service import WAHAService
from app.services.mongo_chat_service import MongoInboxService

from app.api.auth import get_current_agent as _current_agent

router = APIRouter(prefix="/phones", tags=["phones"])


@router.get("", response_model=list[PhoneOut])
def list_phones(db: Session = Depends(get_db), agent=Depends(_current_agent)):
    from app.core.permissions import allowed_phone_ids
    q = db.query(Phone).filter(Phone.is_active == True)
    allowed = allowed_phone_ids(db, agent)
    if allowed is not None:
        q = q.filter(Phone.id.in_(allowed or [0]))
    return q.all()


@router.post("", response_model=PhoneOut, status_code=201)
async def add_phone(req: PhoneCreate, db: Session = Depends(get_db)):
    import re as _re

    # Auto-generate unique session name: hyperscope_1, hyperscope_2, …
    prefix = settings.waha_session_prefix
    existing_nums: list[int] = []
    for (sname,) in db.query(Phone.session_name).filter(
        Phone.session_name.like(f"{prefix}_%")
    ).all():
        m = _re.match(rf"^{_re.escape(prefix)}_(\d+)$", sname)
        if m:
            existing_nums.append(int(m.group(1)))
    next_num = max(existing_nums, default=0) + 1
    session_name = f"{prefix}_{next_num}"

    phone = Phone(
        name=req.name,
        phone_number=f"pending_{session_name}",
        session_name=session_name,
        waha_status="STOPPED",
        is_active=True,
        is_default=req.is_default,
    )
    db.add(phone)
    db.commit()
    db.refresh(phone)

    waha = WAHAService.from_phone(phone)
    try:
        await waha.ensure_session_exists(settings.waha_webhook_url, settings.waha_webhook_secret)
        await waha.start_session()
        await waha.configure_webhook(settings.waha_webhook_url, settings.waha_webhook_secret)
        phone.waha_status = "SCAN_QR_CODE"
        db.commit()
    except Exception as exc:
        from app.api.webhooks import logger
        logger.warning("Could not start WAHA session %s after creation: %s", session_name, exc)

    return phone


@router.get("/{phone_id}/status")
async def get_status(phone_id: int, db: Session = Depends(get_db)):
    phone = db.query(Phone).filter(Phone.id == phone_id).first()
    if not phone:
        raise HTTPException(404, "Phone not found")
    waha = WAHAService.from_phone(phone)
    try:
        status = await waha.get_session_status()
    except Exception as exc:
        from app.api.webhooks import logger
        logger.warning("Failed to query WAHA status for phone %s: %s", phone.session_name, exc)
        status = "OFFLINE"
    phone.waha_status = status

    # Auto-resolve stale "pending_*" placeholder when WAHA is connected
    if status == "WORKING" and str(phone.phone_number or "").startswith("pending"):
        try:
            me = await waha.get_me()
            number = me.get("id", "").split("@")[0] if me.get("id") else ""
            if number:
                conflict = db.query(Phone).filter(
                    Phone.phone_number == number, Phone.id != phone.id
                ).first()
                if conflict:
                    from app.models.agent_phone import AgentPhone as _AP
                    # Re-parent MongoDB chats/messages from conflict → this phone
                    inbox = MongoInboxService()
                    await inbox.db.chats.update_many(
                        {"phone_id": conflict.id}, {"$set": {"phone_id": phone.id}}
                    )
                    await inbox.db.messages.update_many(
                        {"phone_id": conflict.id}, {"$set": {"phone_id": phone.id}}
                    )
                    db.execute(delete(_AP).where(_AP.phone_id == conflict.id))
                    db.flush()
                    db.delete(conflict)
                    db.flush()
                phone.phone_number = number
        except Exception:
            pass

    db.commit()
    return {"phone_id": phone_id, "status": status, "phone_number": phone.phone_number}


@router.get("/{phone_id}/qr")
async def get_qr(phone_id: int, db: Session = Depends(get_db)):
    phone = db.query(Phone).filter(Phone.id == phone_id).first()
    if not phone:
        raise HTTPException(404, "Phone not found")
    waha = WAHAService.from_phone(phone)
    try:
        qr = await waha.get_qr()
    except Exception as exc:
        from app.api.webhooks import logger
        logger.warning("Failed to query WAHA QR code for phone %s: %s", phone.session_name, exc)
        qr = None
    return {"qr": qr}


@router.post("/{phone_id}/start")
async def start_session(phone_id: int, db: Session = Depends(get_db)):
    phone = db.query(Phone).filter(Phone.id == phone_id).first()
    if not phone:
        raise HTTPException(404, "Phone not found")
    waha = WAHAService.from_phone(phone)
    try:
        await waha.ensure_session_exists(settings.waha_webhook_url, settings.waha_webhook_secret)
        ok = await waha.start_session()
        if ok:
            await waha.configure_webhook(settings.waha_webhook_url, settings.waha_webhook_secret)
    except Exception as exc:
        from app.api.webhooks import logger
        logger.warning("Failed to start WAHA session %s: %s", phone.session_name, exc)
        ok = False
    return {"ok": ok}


@router.post("/{phone_id}/logout")
async def logout_session(phone_id: int, db: Session = Depends(get_db)):
    phone = db.query(Phone).filter(Phone.id == phone_id).first()
    if not phone:
        raise HTTPException(404, "Phone not found")
    waha = WAHAService.from_phone(phone)
    try:
        ok = await waha.logout_session()
    except Exception as exc:
        from app.api.webhooks import logger
        logger.warning("Failed to logout WAHA session %s: %s", phone.session_name, exc)
        ok = False
    phone.waha_status = "STOPPED"
    db.commit()
    from app.core.ws_manager import ws_manager
    await ws_manager.broadcast("phone_status_changed", {"phone_id": phone.id, "status": "STOPPED"})
    await ws_manager.broadcast("data_cleared", {"phone_id": phone.id, "reason": "logout"})
    return {"ok": ok}


@router.post("/{phone_id}/stop")
async def stop_session(phone_id: int, db: Session = Depends(get_db)):
    phone = db.query(Phone).filter(Phone.id == phone_id).first()
    if not phone:
        raise HTTPException(404, "Phone not found")
    waha = WAHAService.from_phone(phone)
    try:
        ok = await waha.stop_session()
    except Exception as exc:
        from app.api.webhooks import logger
        logger.warning("Failed to stop WAHA session %s: %s", phone.session_name, exc)
        ok = False
    return {"ok": ok}


@router.post("/{phone_id}/restart")
async def restart_session(phone_id: int, db: Session = Depends(get_db)):
    phone = db.query(Phone).filter(Phone.id == phone_id).first()
    if not phone:
        raise HTTPException(404, "Phone not found")
    waha = WAHAService.from_phone(phone)
    try:
        ok = await waha.restart_session()
        if ok:
            await waha.configure_webhook(settings.waha_webhook_url, settings.waha_webhook_secret)
    except Exception as exc:
        from app.api.webhooks import logger
        logger.warning("Failed to restart WAHA session %s: %s", phone.session_name, exc)
        ok = False
    return {"ok": ok}


@router.post("/{phone_id}/clear-data")
async def clear_phone_data(phone_id: int, db: Session = Depends(get_db)):
    """Delete all synced WhatsApp chats and messages for this phone from MongoDB."""
    phone = db.query(Phone).filter(Phone.id == phone_id).first()
    if not phone:
        raise HTTPException(404, "Phone not found")

    inbox = MongoInboxService()
    await inbox.delete_phone_data(phone_id)

    from app.core.ws_manager import ws_manager
    await ws_manager.broadcast("data_cleared", {"phone_id": phone_id})
    return {"ok": True}


@router.post("/connect")
async def auto_connect(db: Session = Depends(get_db)):
    session_name = settings.waha_session_name
    phone = db.query(Phone).filter(Phone.session_name == session_name).first()
    if not phone:
        phone = Phone(name="My WhatsApp", phone_number=f"pending_{session_name}", session_name=session_name,
                      waha_status="STOPPED", is_default=True, is_active=True)
        db.add(phone)
        db.commit()
        db.refresh(phone)
    elif not phone.is_active:
        phone.is_active = True
        db.commit()
        db.refresh(phone)

    waha = WAHAService.from_phone(phone)
    try:
        await waha.ensure_session_exists(settings.waha_webhook_url, settings.waha_webhook_secret)
        status = await waha.get_session_status()
        if status not in ("WORKING", "SCAN_QR_CODE"):
            await waha.start_session()
            await waha.configure_webhook(settings.waha_webhook_url, settings.waha_webhook_secret)
        else:
            await waha.configure_webhook(settings.waha_webhook_url, settings.waha_webhook_secret)
    except Exception as exc:
        from app.api.webhooks import logger
        logger.warning("Failed to auto-connect session: %s", exc)
        status = "OFFLINE"

    try:
        qr = await waha.get_qr()
    except Exception:
        qr = None
    return {"phone_id": phone.id, "qr": qr, "status": status}


@router.post("/{phone_id}/sync-number")
async def sync_phone_number(phone_id: int, db: Session = Depends(get_db)):
    """After QR scan: fetch real phone number from WAHA and update the record."""
    from app.api.webhooks import logger

    phone = db.query(Phone).filter(Phone.id == phone_id).first()
    if not phone:
        raise HTTPException(404, "Phone not found")
    waha = WAHAService.from_phone(phone)
    try:
        me = await waha.get_me()
        number = me.get("id", "").split("@")[0] if me.get("id") else ""
        if number:
            conflict = db.query(Phone).filter(
                Phone.phone_number == number, Phone.id != phone_id,
            ).first()
            if conflict:
                logger.info(
                    "Removing stale phone record %s (session=%s) — number %s now claimed by phone %s",
                    conflict.id, conflict.session_name, number, phone_id,
                )
                from app.models.agent_phone import AgentPhone as _AP
                inbox = MongoInboxService()
                await inbox.db.chats.update_many(
                    {"phone_id": conflict.id}, {"$set": {"phone_id": phone_id}}
                )
                await inbox.db.messages.update_many(
                    {"phone_id": conflict.id}, {"$set": {"phone_id": phone_id}}
                )
                db.execute(delete(_AP).where(_AP.phone_id == conflict.id))
                db.flush()
                db.delete(conflict)
                db.flush()
            phone.phone_number = number
        status = await waha.get_session_status()
        phone.waha_status = status
    except Exception as exc:
        logger.warning("Failed to sync phone number for phone %s: %s", phone.session_name, exc)
        status = "OFFLINE"
    db.commit()
    db.refresh(phone)
    return {"phone_id": phone_id, "phone_number": phone.phone_number, "status": phone.waha_status}


@router.patch("/{phone_id}", response_model=PhoneOut)
def update_phone(phone_id: int, req: dict, db: Session = Depends(get_db)):
    phone = db.query(Phone).filter(Phone.id == phone_id).first()
    if not phone:
        raise HTTPException(404, "Phone not found")
    allowed = {"name", "waha_base_url", "waha_api_key", "is_default"}
    for k, v in req.items():
        if k in allowed and hasattr(phone, k):
            setattr(phone, k, v or None)
    db.commit()
    db.refresh(phone)
    return phone


@router.delete("/{phone_id}", status_code=204)
async def delete_phone(phone_id: int, db: Session = Depends(get_db)):
    phone = db.query(Phone).filter(Phone.id == phone_id).first()
    if not phone:
        raise HTTPException(404, "Phone not found")

    # Stop + delete the WAHA session
    try:
        waha = WAHAService.from_phone(phone)
        await waha.delete_waha_session()
    except Exception:
        pass

    # Delete MongoDB chats + messages for this phone
    inbox = MongoInboxService()
    await inbox.delete_phone_data(phone_id)

    # Clean up MySQL relational data (no chat/message FKs anymore)
    from app.models.note import Note
    from app.models.ticket import Ticket, TicketLabel
    from app.models.bulk_message_job import BulkMessageJob, BulkMessageLog
    from app.models.scheduled_message import ScheduledMessage
    from app.models.task import Task
    from app.models.agent_phone import AgentPhone

    # Tickets and notes are keyed by chat_id (MongoDB integer ID) — delete any linked to this phone's chats
    # We can't easily resolve these without querying MongoDB, so we null them out rather than cascade-delete
    db.execute(delete(ScheduledMessage).where(ScheduledMessage.phone_id == phone_id))

    job_ids = [r[0] for r in db.query(BulkMessageJob.id).filter(BulkMessageJob.phone_id == phone_id).all()]
    if job_ids:
        db.execute(delete(BulkMessageLog).where(BulkMessageLog.job_id.in_(job_ids)))
        db.execute(delete(BulkMessageJob).where(BulkMessageJob.phone_id == phone_id))

    db.execute(delete(AgentPhone).where(AgentPhone.phone_id == phone_id))
    db.delete(phone)
    db.commit()
