import logging
from datetime import datetime
from typing import Any

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from pydantic import BaseModel
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
from app.core.config import settings
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
    limit: int = 200,
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

    from app.services.waha_service import SendResult
    import time
    if settings.environment == "development" and phone.waha_status != "WORKING":
        logger.warning("WAHA session %s status is %s (not WORKING) in development environment. Mocking message send.", phone.session_name, phone.waha_status)
        result = SendResult(message_id=f"mock_{int(time.time())}_{chat.id}", raw={"status": "mock_sent"})
    else:
        waha = WAHAService(session_name=phone.session_name)
        try:
            if req.message_type == "image" and req.media_url:
                result = await waha.send_image(chat.chat_wid, req.media_url, caption=req.body)
            elif req.message_type == "file" and req.media_url:
                result = await waha.send_file(chat.chat_wid, req.media_url, caption=req.body)
            else:
                result = await waha.send_text(chat.chat_wid, req.body)
        except Exception as exc:
            if settings.environment == "development":
                logger.warning("WAHA send failed in development environment, falling back to mock: %s", exc)
                result = SendResult(message_id=f"mock_{int(time.time())}_{chat.id}", raw={"status": "mock_sent"})
            else:
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

    # A human replied — snooze the AI for the configured quiet period
    if chat.ai_active and chat.ai_state != "SNOOZED":
        chat.ai_state = "SNOOZED"
        chat.ai_snoozed_at = datetime.utcnow()
        db.commit()

    # Broadcast to all connected agents in real time
    from app.core.ws_manager import ws_manager
    sent_ts = msg.timestamp if msg else datetime.utcnow()
    await ws_manager.emit_new_message(
        chat_id=chat.id,
        chat_wid=chat.chat_wid,
        body=req.body,
        from_me=True,
        sender_name=agent.name or "Agent",
        sender_number=phone.phone_number or "",
        timestamp=int(sent_ts.timestamp()) if hasattr(sent_ts, 'timestamp') else int(sent_ts),
        message_type=req.message_type if req.media_url else "text",
        has_media=bool(req.media_url),
        chat_name=chat.name or "",
        unread_count=0,
    )

    return {"ok": True, "message_id": msg.id if msg else None}


@router.post("/chats/{chat_id}/sync-messages")
async def sync_chat_messages(chat_id: int, limit: int = 50, db: Session = Depends(get_db)):
    """Fetch recent messages from WAHA for a chat and save to DB.

    Pass a larger `limit` to pull deeper history (capped at 500)."""
    svc = InboxService(db)
    chat = svc.get_chat(chat_id)
    if not chat:
        raise HTTPException(404, "Chat not found")
    phone = db.query(Phone).filter(Phone.id == chat.phone_id).first()
    if not phone:
        raise HTTPException(404, "Phone not found")

    waha = WAHAService(session_name=phone.session_name)
    try:
        messages = await waha.get_messages(chat.chat_wid, limit=min(max(limit, 1), 500))
    except Exception:
        messages = []

    _MEDIA_TYPES = {"image", "photo", "video", "audio", "ptt", "voice",
                    "document", "pdf", "sticker", "gif", "location", "contact", "vcard"}
    _MEDIA_LABELS = {
        "image": "📷 Photo", "photo": "📷 Photo",
        "video": "🎬 Video",
        "audio": "🎤 Voice message", "voice": "🎤 Voice message", "ptt": "🎤 Voice message",
        "document": "📄 Document", "pdf": "📄 Document",
        "sticker": "🖼 Sticker", "gif": "🎞 GIF",
        "location": "📍 Location",
        "contact": "👤 Contact", "vcard": "👤 Contact",
    }

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

        msg_type = str(m.get("type") or "text").lower()
        # WAHA sometimes returns type:"chat" for media msgs — use hasMedia field too
        waha_has_media = bool(m.get("hasMedia") or m.get("has_media"))
        has_media = msg_type in _MEDIA_TYPES or waha_has_media

        # Store a human-readable label as body so the chat bubble is never blank
        if not body and has_media:
            body = _MEDIA_LABELS.get(msg_type, "📎 Media")

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
                "has_media": has_media,
                "timestamp": ts,
            })
            synced += 1
        except _IE:
            db.rollback()
            synced += 1
        except Exception:
            pass

    return {"synced": synced}


class BulkChatUpdateRequest(BaseModel):
    chat_ids: list[int]
    updates: dict | None = None          # is_archived / is_pinned / ai_active / is_flagged
    mark_read: bool | None = None        # True → read, False → unread
    add_label_id: int | None = None
    remove_label_id: int | None = None


@router.post("/bulk-update")
def bulk_update_chats(
    req: BulkChatUpdateRequest,
    db: Session = Depends(get_db),
    agent: Agent = Depends(get_current_agent),
):
    """Apply changes to many chats at once (chat-list bulk actions)."""
    if not req.chat_ids:
        raise HTTPException(400, "No chats selected")
    ids = req.chat_ids[:500]
    svc = InboxService(db)

    allowed = {"is_archived", "is_pinned", "ai_active", "is_flagged"}
    updates = {k: v for k, v in (req.updates or {}).items() if k in allowed}
    if "ai_active" in updates:
        updates["ai_state"] = "ACTIVE" if updates["ai_active"] else "INACTIVE"
    if updates:
        svc.bulk_update_chats(ids, **updates)

    if req.mark_read is True:
        from app.models.chat import Chat as _Chat
        db.query(_Chat).filter(_Chat.id.in_(ids)).update({"unread_count": 0})
        db.commit()
    elif req.mark_read is False:
        from app.models.chat import Chat as _Chat
        db.query(_Chat).filter(_Chat.id.in_(ids), _Chat.unread_count == 0).update({"unread_count": 1})
        db.commit()

    if req.add_label_id:
        for cid in ids:
            svc.add_label_to_chat(cid, req.add_label_id)
    if req.remove_label_id:
        for cid in ids:
            svc.remove_label_from_chat(cid, req.remove_label_id)

    log_activity(
        db, "chats_bulk_updated", entity_type="chat", agent_id=agent.id,
        description=f"Bulk update on {len(ids)} chats: "
                    f"{', '.join(list(updates.keys()) + (['read' if req.mark_read else 'unread'] if req.mark_read is not None else []) + (['+label'] if req.add_label_id else []) + (['-label'] if req.remove_label_id else []))}",
        metadata={"chat_ids": ids},
    )
    return {"updated": len(ids)}


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
    from datetime import timezone as _tz
    from app.models.chat import Chat as _Chat
    from app.models.message import Message as _Msg
    from sqlalchemy import desc as _desc, or_ as _or

    phone = db.query(Phone).filter(Phone.id == phone_id).first()
    if not phone:
        raise HTTPException(404, "Phone not found")
    waha = WAHAService(session_name=phone.session_name)
    chats = await waha.get_chats(limit=500)
    svc = InboxService(db)
    synced = 0

    _media_labels = {
        "image": "📷 Photo", "photo": "📷 Photo",
        "video": "🎬 Video",
        "audio": "🎤 Voice message", "voice": "🎤 Voice message", "ptt": "🎤 Voice message",
        "document": "📄 Document", "pdf": "📄 Document",
        "sticker": "🖼 Sticker", "location": "📍 Location",
        "contact": "👤 Contact", "vcard": "👤 Contact",
    }

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

        # Extract last message preview from WAHA chat object
        chat_data: dict = {"chat_wid": str(cid), "phone_id": phone_id, "name": name, "is_group": is_group}
        last_msg_obj = c.get("lastMessage") or {}
        if last_msg_obj:
            lm_body = last_msg_obj.get("body") or last_msg_obj.get("caption") or ""
            lm_type = str(last_msg_obj.get("type") or "text").lower()
            if not lm_body:
                lm_body = _media_labels.get(lm_type, "📎 Media")
            lm_ts_raw = last_msg_obj.get("timestamp")
            if lm_body:
                chat_data["last_message"] = lm_body[:200]
            if isinstance(lm_ts_raw, (int, float)):
                chat_data["last_message_at"] = datetime.fromtimestamp(lm_ts_raw, tz=_tz.utc).replace(tzinfo=None)

        svc.upsert_chat(chat_data)
        synced += 1

    # Repair previously synced chats that still have WID-looking names
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

    # Backfill last_message from DB for chats still missing it
    missing = (
        db.query(_Chat)
        .filter(_Chat.phone_id == phone_id, _or(_Chat.last_message == None, _Chat.last_message == ""))
        .all()
    )
    for chat in missing:
        latest = db.query(_Msg).filter(_Msg.chat_id == chat.id).order_by(_desc(_Msg.timestamp)).first()
        if latest:
            body = latest.body or ""
            mtype = latest.message_type or "text"
            if not body:
                body = _media_labels.get(mtype, "📎 Media")
            chat.last_message = body[:200]
            if latest.timestamp:
                chat.last_message_at = latest.timestamp
    if missing:
        db.commit()

    return {"synced": synced}
