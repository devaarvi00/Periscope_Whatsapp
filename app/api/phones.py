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

router = APIRouter(prefix="/phones", tags=["phones"])


@router.get("", response_model=list[PhoneOut])
def list_phones(db: Session = Depends(get_db)):
    return db.query(Phone).filter(Phone.is_active == True).all()


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
        db.execute(delete(ChatLabel).where(ChatLabel.chat_id.in_(chat_ids)))
        db.execute(delete(Message).where(Message.phone_id == phone_id))
        db.execute(delete(Chat).where(Chat.phone_id == phone_id))
        db.commit()

    # Broadcast so all open browser tabs update immediately
    from app.core.ws_manager import ws_manager
    await ws_manager.broadcast("data_cleared", {"phone_id": phone_id})

    return {"ok": True, "chats_deleted": len(chat_ids)}


@router.delete("/{phone_id}", status_code=204)
def delete_phone(phone_id: int, db: Session = Depends(get_db)):
    phone = db.query(Phone).filter(Phone.id == phone_id).first()
    if not phone:
        raise HTTPException(404, "Phone not found")
    phone.is_active = False
    db.commit()
