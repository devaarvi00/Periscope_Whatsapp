import logging
from datetime import datetime
from typing import Any

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from sqlalchemy.orm import Session

from app.api.auth import get_current_agent
from app.db.session import get_db
from app.models.agent import Agent
from app.models.chat import ChatLabel
from app.models.phone import Phone
from app.schemas.inbox import (
    ChatOut,
    ChatUpdateRequest,
    MessageOut,
    SendMessageRequest,
)
from app.services.activity_service import log_activity
from app.services.automation_service import fire_trigger
from app.services.inbox_service import InboxService
from app.services.waha_service import WAHAService

router = APIRouter(prefix="/inbox", tags=["inbox"])
logger = logging.getLogger(__name__)


@router.get("/chats", response_model=list[dict])
def list_chats(
    phone_id: int | None = None,
    is_archived: bool = False,
    is_flagged: bool | None = None,
    label_id: int | None = None,
    search: str | None = None,
    assigned_to: int | None = None,
    is_group: bool | None = None,
    limit: int = 50,
    offset: int = 0,
    db: Session = Depends(get_db),
    agent: Agent = Depends(get_current_agent),
):
    from app.core.permissions import allowed_phone_ids
    svc = InboxService(db)
    chats = svc.list_chats(
        phone_id=phone_id,
        is_archived=is_archived,
        is_flagged=is_flagged,
        label_id=label_id,
        search=search,
        assigned_to=assigned_to,
        is_group=is_group,
        limit=limit,
        offset=offset,
        phone_ids=allowed_phone_ids(db, agent),
    )
    result = []
    for c in chats:
        labels = svc.get_chat_label_ids(c.id)
        d = {
            "id": c.id,
            "chat_wid": c.chat_wid,
            "phone_id": c.phone_id,
            "name": c.name,
            "is_group": c.is_group,
            "last_message": c.last_message,
            "last_message_at": c.last_message_at.isoformat() if c.last_message_at else None,
            "unread_count": c.unread_count,
            "is_flagged": c.is_flagged,
            "is_archived": c.is_archived,
            "is_pinned": c.is_pinned,
            "ai_active": c.ai_active,
            "ai_state": c.ai_state,
            "assigned_to": c.assigned_to,
            "labels": labels,
        }
        result.append(d)
    return result


@router.get("/chats/{chat_id}", response_model=dict)
def get_chat(chat_id: int, db: Session = Depends(get_db)):
    svc = InboxService(db)
    chat = svc.get_chat(chat_id)
    if not chat:
        raise HTTPException(404, "Chat not found")
    labels = svc.get_chat_label_ids(chat_id)
    return {
        "id": chat.id, "chat_wid": chat.chat_wid, "name": chat.name,
        "is_group": chat.is_group, "phone_id": chat.phone_id,
        "unread_count": chat.unread_count, "is_flagged": chat.is_flagged,
        "is_archived": chat.is_archived, "is_pinned": chat.is_pinned,
        "ai_active": chat.ai_active, "ai_state": chat.ai_state,
        "assigned_to": chat.assigned_to, "labels": labels,
    }


@router.patch("/chats/{chat_id}")
def update_chat(
    chat_id: int,
    req: ChatUpdateRequest,
    background: BackgroundTasks,
    db: Session = Depends(get_db),
    agent: Agent = Depends(get_current_agent),
):
    svc = InboxService(db)
    updates = req.model_dump(exclude_none=True)
    prev = svc.get_chat(chat_id)
    prev_assigned = prev.assigned_to if prev else None
    chat = svc.update_chat(chat_id, **updates)
    if not chat:
        raise HTTPException(404, "Chat not found")
    if "assigned_to" in updates and updates["assigned_to"] != prev_assigned:
        log_activity(
            db, "chat_assigned", entity_type="chat", entity_id=chat.id,
            agent_id=agent.id,
            description=f"Chat '{chat.name}' assigned to agent #{updates['assigned_to']}",
        )
        background.add_task(fire_trigger, "chat_assigned", {
            "chat_id": chat.id,
            "chat_wid": chat.chat_wid,
            "chat_name": chat.name,
            "assigned_to": updates["assigned_to"],
            "is_group": chat.is_group,
        })
    return {"ok": True}


@router.post("/chats/{chat_id}/read")
def mark_read(chat_id: int, db: Session = Depends(get_db)):
    InboxService(db).mark_chat_read(chat_id)
    return {"ok": True}


@router.get("/chats/{chat_id}/messages", response_model=list[dict])
def get_messages(
    chat_id: int,
    limit: int = 50,
    before_id: int | None = None,
    db: Session = Depends(get_db),
):
    svc = InboxService(db)
    msgs = svc.get_messages(chat_id, limit=limit, before_id=before_id)
    return [
        {
            "id": m.id, "chat_id": m.chat_id, "message_wid": m.message_wid,
            "from_me": m.from_me, "sender_name": m.sender_name,
            "sender_number": m.sender_number, "sent_by_agent_id": m.sent_by_agent_id,
            "body": m.body, "message_type": m.message_type,
            "has_media": m.has_media, "media_url": m.media_url,
            "is_read": m.is_read, "is_flagged": m.is_flagged,
            "timestamp": m.timestamp.isoformat(),
        }
        for m in reversed(msgs)
    ]


@router.post("/send")
async def send_message(
    req: SendMessageRequest,
    db: Session = Depends(get_db),
    agent: Agent = Depends(get_current_agent),
):
    svc = InboxService(db)
    chat = svc.get_chat(req.chat_id)
    if not chat:
        raise HTTPException(404, "Chat not found")

    phone = db.query(Phone).filter(Phone.id == (req.phone_id or chat.phone_id)).first()
    if not phone:
        raise HTTPException(404, "Phone not found")

    waha = WAHAService(session_name=phone.session_name)
    try:
        if req.message_type == "image" and req.media_url:
            result = await waha.send_image(chat.chat_wid, req.media_url, caption=req.body)
        elif req.message_type == "file" and req.media_url:
            result = await waha.send_file(chat.chat_wid, req.media_url, caption=req.body)
        else:
            result = await waha.send_text(chat.chat_wid, req.body)
    except Exception as exc:
        raise HTTPException(500, f"Send failed: {exc}")

    try:
        from sqlalchemy.exc import IntegrityError
        msg = svc.upsert_message({
            "chat_id": chat.id,
            "phone_id": phone.id,
            "message_wid": result.message_id or f"sent_{datetime.utcnow().timestamp()}",
            "from_me": True,
            "sender_name": agent.name or "Agent",
            "sender_number": phone.phone_number,
            "sent_by_agent_id": agent.id,
            "body": req.body,
            "message_type": req.message_type if req.media_url else "text",
            "has_media": bool(req.media_url),
            "media_url": req.media_url,
            "timestamp": datetime.utcnow(),
        })
    except IntegrityError:
        db.rollback()
        # Race: webhook already saved this message — find it and return
        from app.models.message import Message as MsgModel
        msg = db.query(MsgModel).filter(
            MsgModel.message_wid == result.message_id
        ).first()
        if not msg:
            return {"ok": True, "message_id": None}
    except Exception as exc:
        logger.exception("upsert_message failed after successful WAHA send")
        raise HTTPException(500, f"DB save failed: {exc}")

    return {"ok": True, "message_id": msg.id}


@router.post("/chats/{chat_id}/sync-messages")
async def sync_chat_messages(chat_id: int, db: Session = Depends(get_db)):
    """Fetch recent messages from WAHA for a chat and save to DB."""
    svc = InboxService(db)
    chat = svc.get_chat(chat_id)
    if not chat:
        raise HTTPException(404, "Chat not found")
    phone = db.query(Phone).filter(Phone.id == chat.phone_id).first()
    if not phone:
        raise HTTPException(404, "Phone not found")

    waha = WAHAService(session_name=phone.session_name)
    try:
        messages = await waha.get_messages(chat.chat_wid, limit=50)
    except Exception:
        messages = []

    synced = 0
    for m in messages:
        raw_id = m.get("id") or {}
        if isinstance(raw_id, dict):
            msg_wid = raw_id.get("_serialized") or raw_id.get("id", "")
        else:
            msg_wid = str(raw_id or "")
        if not msg_wid:
            continue

        from_me = bool(m.get("fromMe") or m.get("from_me", False))
        body = m.get("body") or m.get("caption") or ""
        ts_raw = m.get("timestamp")
        if isinstance(ts_raw, (int, float)):
            from datetime import timezone
            ts = datetime.fromtimestamp(ts_raw, tz=timezone.utc).replace(tzinfo=None)
        else:
            ts = datetime.utcnow()

        msg_type = m.get("type", "text")
        sender_name = m.get("notifyName") or m.get("pushName") or ""
        from_raw = m.get("from") or m.get("author") or ""
        if isinstance(from_raw, dict):
            sender_number = str(from_raw.get("_serialized") or from_raw.get("id", "")).split("@")[0]
        else:
            sender_number = str(from_raw).split("@")[0]

        try:
            from sqlalchemy.exc import IntegrityError as _IE
            svc.upsert_message({
                "chat_id": chat.id,
                "phone_id": phone.id,
                "message_wid": msg_wid,
                "from_me": from_me,
                "sender_name": sender_name,
                "sender_number": sender_number,
                "body": body,
                "message_type": msg_type,
                "has_media": msg_type not in ("text", "chat", ""),
                "timestamp": ts,
            })
            synced += 1
        except _IE:
            db.rollback()
            synced += 1
        except Exception:
            pass

    return {"synced": synced}


@router.post("/chats/{chat_id}/labels/{label_id}")
def add_label(
    chat_id: int,
    label_id: int,
    background: BackgroundTasks,
    db: Session = Depends(get_db),
):
    InboxService(db).add_label_to_chat(chat_id, label_id)
    background.add_task(fire_trigger, "label_added", {
        "chat_id": chat_id,
        "label_id": label_id,
        "source": "manual",
    })
    return {"ok": True}


@router.delete("/chats/{chat_id}/labels/{label_id}")
def remove_label(chat_id: int, label_id: int, db: Session = Depends(get_db)):
    InboxService(db).remove_label_from_chat(chat_id, label_id)
    return {"ok": True}


@router.post("/sync/{phone_id}")
async def sync_chats(phone_id: int, db: Session = Depends(get_db)):
    """Fetch latest chats from WAHA and upsert into DB."""
    phone = db.query(Phone).filter(Phone.id == phone_id).first()
    if not phone:
        raise HTTPException(404, "Phone not found")
    waha = WAHAService(session_name=phone.session_name)
    chats = await waha.get_chats(limit=200)
    svc = InboxService(db)
    synced = 0
    for c in chats:
        cid = c.get("id") or c.get("chatId") or c.get("_serialized") or ""
        if isinstance(cid, dict):
            cid = cid.get("_serialized") or cid.get("id", "")
        if not cid:
            continue
        is_group = str(cid).endswith("@g.us")
        name = c.get("name") or c.get("subject") or ""
        if not name or "@" in name:
            if is_group:
                try:
                    info = await waha.get_group_info(str(cid))
                    name = info.get("subject") or info.get("name") or ""
                except Exception:
                    name = ""
                name = name or f"Group {str(cid).split('@')[0][-6:]}"
            else:
                name = str(cid).split("@")[0]
        svc.upsert_chat({"chat_wid": str(cid), "phone_id": phone_id, "name": name, "is_group": is_group})
        synced += 1

    # Repair previously synced chats that still have WID-looking names
    from app.models.chat import Chat as _Chat
    stale = (
        db.query(_Chat)
        .filter(_Chat.phone_id == phone_id, _Chat.is_group == True, _Chat.name.like("%@g.us%"))
        .limit(100)
        .all()
    )
    for chat in stale:
        try:
            info = await waha.get_group_info(chat.chat_wid)
            subject = info.get("subject") or info.get("name") or ""
            if subject:
                chat.name = subject
        except Exception:
            continue
    if stale:
        db.commit()
    return {"synced": synced}
