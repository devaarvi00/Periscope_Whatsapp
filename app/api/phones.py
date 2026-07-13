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
    existing = db.query(Phone).filter(Phone.phone_number == req.phone_number).first()
    if existing:
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
    status = await waha.get_session_status()
    phone.waha_status = status
    db.commit()
    return {"phone_id": phone_id, "status": status}


@router.get("/{phone_id}/qr")
async def get_qr(phone_id: int, db: Session = Depends(get_db)):
    phone = db.query(Phone).filter(Phone.id == phone_id).first()
    if not phone:
        raise HTTPException(404, "Phone not found")
    waha = WAHAService(session_name=phone.session_name)
    qr = await waha.get_qr()
    return {"qr": qr}


@router.post("/{phone_id}/start")
async def start_session(phone_id: int, db: Session = Depends(get_db)):
    phone = db.query(Phone).filter(Phone.id == phone_id).first()
    if not phone:
        raise HTTPException(404, "Phone not found")
    waha = WAHAService(session_name=phone.session_name)
    ok = await waha.start_session()
    return {"ok": ok}


@router.post("/{phone_id}/logout")
async def logout_session(phone_id: int, db: Session = Depends(get_db)):
    """Logout from WhatsApp, clear WAHA auth so next start forces QR scan."""
    phone = db.query(Phone).filter(Phone.id == phone_id).first()
    if not phone:
        raise HTTPException(404, "Phone not found")
    waha = WAHAService(session_name=phone.session_name)
    ok = await waha.logout_session()
    phone.waha_status = "STOPPED"
    db.commit()
    return {"ok": ok}


@router.post("/{phone_id}/stop")
async def stop_session(phone_id: int, db: Session = Depends(get_db)):
    phone = db.query(Phone).filter(Phone.id == phone_id).first()
    if not phone:
        raise HTTPException(404, "Phone not found")
    waha = WAHAService(session_name=phone.session_name)
    ok = await waha.stop_session()
    return {"ok": ok}


@router.post("/{phone_id}/restart")
async def restart_session(phone_id: int, db: Session = Depends(get_db)):
    phone = db.query(Phone).filter(Phone.id == phone_id).first()
    if not phone:
        raise HTTPException(404, "Phone not found")
    waha = WAHAService(session_name=phone.session_name)
    ok = await waha.restart_session()
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
    phone = db.query(Phone).filter(Phone.session_name == session_name, Phone.is_active == True).first()
    if not phone:
        phone = Phone(name="My WhatsApp", phone_number="pending", session_name=session_name,
                      waha_status="STOPPED", is_default=True)
        db.add(phone)
        db.commit()
        db.refresh(phone)

    waha = WAHAService(session_name=session_name)
    status = await waha.get_session_status()
    if status not in ("WORKING", "SCAN_QR_CODE"):
        await waha.start_session()

    qr = await waha.get_qr()
    return {"phone_id": phone.id, "qr": qr, "status": status}


@router.post("/{phone_id}/sync-number")
async def sync_phone_number(phone_id: int, db: Session = Depends(get_db)):
    """After QR scan: fetch real phone number from WAHA and update the record."""
    phone = db.query(Phone).filter(Phone.id == phone_id).first()
    if not phone:
        raise HTTPException(404, "Phone not found")
    waha = WAHAService(session_name=phone.session_name)
    me = await waha.get_me()
    number = me.get("id", "").split("@")[0] if me.get("id") else ""
    if number:
        phone.phone_number = number
    status = await waha.get_session_status()
    phone.waha_status = status
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
