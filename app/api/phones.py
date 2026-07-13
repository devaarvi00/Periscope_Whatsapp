from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from sqlalchemy import delete

from app.core.config import settings
from app.db.session import get_db
from app.models.phone import Phone
from app.models.chat import Chat, ChatLabel
from app.models.message import Message
from app.schemas.inbox import PhoneCreate, PhoneOut
from app.services.waha_service import WAHAService

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
def add_phone(req: PhoneCreate, db: Session = Depends(get_db)):
    existing_session = db.query(Phone).filter(Phone.session_name == req.session_name).first()
    if existing_session:
        raise HTTPException(400, "Session name already registered")
    if req.phone_number and req.phone_number != "pending":
        existing_num = db.query(Phone).filter(Phone.phone_number == req.phone_number).first()
        if existing_num:
            raise HTTPException(400, "Phone number already registered")
    phone = Phone(**req.model_dump())
    db.add(phone)
    db.commit()
    db.refresh(phone)
    return phone


@router.get("/{phone_id}/status")
async def get_status(phone_id: int, db: Session = Depends(get_db)):
    phone = db.query(Phone).filter(Phone.id == phone_id).first()
    if not phone:
        raise HTTPException(404, "Phone not found")
    waha = WAHAService(session_name=phone.session_name)
    try:
        status = await waha.get_session_status()
    except Exception as exc:
        from app.api.webhooks import logger
        logger.warning("Failed to query WAHA status for phone %s: %s", phone.session_name, exc)
        status = "OFFLINE"
    phone.waha_status = status
    db.commit()
    return {"phone_id": phone_id, "status": status}


@router.get("/{phone_id}/qr")
async def get_qr(phone_id: int, db: Session = Depends(get_db)):
    phone = db.query(Phone).filter(Phone.id == phone_id).first()
    if not phone:
        raise HTTPException(404, "Phone not found")
    waha = WAHAService(session_name=phone.session_name)
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
    waha = WAHAService(session_name=phone.session_name)
    try:
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
    """Logout from WhatsApp, clear WAHA auth so next start forces QR scan."""
    phone = db.query(Phone).filter(Phone.id == phone_id).first()
    if not phone:
        raise HTTPException(404, "Phone not found")
    waha = WAHAService(session_name=phone.session_name)
    try:
        ok = await waha.logout_session()
    except Exception as exc:
        from app.api.webhooks import logger
        logger.warning("Failed to logout WAHA session %s: %s", phone.session_name, exc)
        ok = False
    phone.waha_status = "STOPPED"
    db.commit()
    # Notify all connected browser tabs immediately — don't wait for WAHA webhook
    from app.core.ws_manager import ws_manager
    await ws_manager.broadcast("phone_status_changed", {"phone_id": phone.id, "status": "STOPPED"})
    await ws_manager.broadcast("data_cleared", {"phone_id": phone.id, "reason": "logout"})
    return {"ok": ok}


@router.post("/{phone_id}/stop")
async def stop_session(phone_id: int, db: Session = Depends(get_db)):
    phone = db.query(Phone).filter(Phone.id == phone_id).first()
    if not phone:
        raise HTTPException(404, "Phone not found")
    waha = WAHAService(session_name=phone.session_name)
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
    waha = WAHAService(session_name=phone.session_name)
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
    """Delete all synced WhatsApp chats and messages for this phone."""
    phone = db.query(Phone).filter(Phone.id == phone_id).first()
    if not phone:
        raise HTTPException(404, "Phone not found")

    # Get all chat IDs for this phone
    chat_ids = [r[0] for r in db.query(Chat.id).filter(Chat.phone_id == phone_id).all()]

    if chat_ids:
        from sqlalchemy import update

        from app.models.note import Note
        from app.models.ticket import Ticket, TicketLabel
        from app.models.bulk_message_job import BulkMessageLog
        from app.models.scheduled_message import ScheduledMessage
        from app.models.task import Task

        ticket_ids = [r[0] for r in db.query(Ticket.id).filter(Ticket.chat_id.in_(chat_ids)).all()]
        if ticket_ids:
            db.execute(delete(TicketLabel).where(TicketLabel.ticket_id.in_(ticket_ids)))
        db.execute(delete(Note).where(Note.chat_id.in_(chat_ids)))
        db.execute(delete(Ticket).where(Ticket.chat_id.in_(chat_ids)))
        db.execute(delete(BulkMessageLog).where(BulkMessageLog.chat_id.in_(chat_ids)))
        db.execute(delete(ScheduledMessage).where(ScheduledMessage.chat_id.in_(chat_ids)))
        db.execute(delete(Task).where(Task.chat_id.in_(chat_ids)))
        # Unlink task->message references for this phone's messages so deletion can't hit FK errors
        msg_ids = db.query(Message.id).filter(Message.phone_id == phone_id).subquery()
        db.execute(update(Task).where(Task.message_id.in_(msg_ids.select())).values(message_id=None))
        db.execute(delete(ChatLabel).where(ChatLabel.chat_id.in_(chat_ids)))
        db.execute(delete(Message).where(Message.phone_id == phone_id))
        db.execute(delete(Chat).where(Chat.phone_id == phone_id))
        db.commit()

    # Broadcast so all open browser tabs update immediately
    from app.core.ws_manager import ws_manager
    await ws_manager.broadcast("data_cleared", {"phone_id": phone_id})

    return {"ok": True, "chats_deleted": len(chat_ids)}


@router.post("/connect")
async def auto_connect(db: Session = Depends(get_db)):
    """Find or create the default phone, start WAHA session, return phone + QR."""
    session_name = settings.waha_session_name
    phone = db.query(Phone).filter(Phone.session_name == session_name).first()
    if not phone:
        phone = Phone(name="My WhatsApp", phone_number="pending", session_name=session_name,
                      waha_status="STOPPED", is_default=True, is_active=True)
        db.add(phone)
        db.commit()
        db.refresh(phone)
    elif not phone.is_active:
        phone.is_active = True
        db.commit()
        db.refresh(phone)

    waha = WAHAService(session_name=session_name)
    try:
        status = await waha.get_session_status()
        if status not in ("WORKING", "SCAN_QR_CODE"):
            await waha.start_session()
            await waha.configure_webhook(settings.waha_webhook_url, settings.waha_webhook_secret)
        else:
            # Session is already started/working, make sure webhook is configured
            await waha.configure_webhook(settings.waha_webhook_url, settings.waha_webhook_secret)
    except Exception as exc:
        from app.api.webhooks import logger
        logger.warning("Failed to auto-connect session: %s", exc)
        status = "OFFLINE"

    try:
        qr = await waha.get_qr()
    except Exception as exc:
        from app.api.webhooks import logger
        logger.warning("Failed to retrieve QR code for session %s: %s", session_name, exc)
        qr = None
    return {"phone_id": phone.id, "qr": qr, "status": status}


@router.post("/{phone_id}/sync-number")
async def sync_phone_number(phone_id: int, db: Session = Depends(get_db)):
    """After QR scan: fetch real phone number from WAHA and update the record."""
    phone = db.query(Phone).filter(Phone.id == phone_id).first()
    if not phone:
        raise HTTPException(404, "Phone not found")
    waha = WAHAService(session_name=phone.session_name)
    try:
        me = await waha.get_me()
        number = me.get("id", "").split("@")[0] if me.get("id") else ""
        if number:
            phone.phone_number = number
        status = await waha.get_session_status()
        phone.waha_status = status
    except Exception as exc:
        from app.api.webhooks import logger
        logger.warning("Failed to sync phone number for phone %s: %s", phone.session_name, exc)
        status = "OFFLINE"
    db.commit()
    db.refresh(phone)
    return {"phone_id": phone_id, "phone_number": phone.phone_number, "status": phone.waha_status}


@router.delete("/{phone_id}", status_code=204)
def delete_phone(phone_id: int, db: Session = Depends(get_db)):
    phone = db.query(Phone).filter(Phone.id == phone_id).first()
    if not phone:
        raise HTTPException(404, "Phone not found")
    phone.is_active = False
    db.commit()
