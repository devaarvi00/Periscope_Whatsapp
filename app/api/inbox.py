import logging
from datetime import datetime
from typing import Any

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.api.auth import get_current_agent
from app.db.session import get_db
from app.models.agent import Agent
from app.models.phone import Phone
from app.schemas.inbox import ChatUpdateRequest, SendMessageRequest
from app.services.activity_service import log_activity
from app.services.automation_service import fire_trigger
from app.services.mongo_chat_service import MongoInboxService, _serialize_chat, _serialize_message
from app.core.config import settings
from app.services.waha_service import WAHAService

router = APIRouter(prefix="/inbox", tags=["inbox"])
logger = logging.getLogger(__name__)


@router.get("/chats", response_model=list[dict])
async def list_chats(
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
    inbox = MongoInboxService()
    docs = await inbox.list_chats(
        phone_id=phone_id,
        phone_ids=allowed_phone_ids(db, agent),
        is_archived=is_archived,
        is_flagged=is_flagged,
        label_id=label_id,
        search=search,
        assigned_to=assigned_to,
        is_group=is_group,
        limit=limit,
        offset=offset,
    )
    # Fetch last message from_me for each chat (for "awaiting reply" filter)
    result = []
    for doc in docs:
        serialized = _serialize_chat(doc)
        # Get last message sender for awaiting-reply filter
        msgs = await inbox.get_messages(chat_id=doc["id"], limit=1)
        serialized["last_message_from_me"] = msgs[0].get("from_me") if msgs else None
        result.append(serialized)
    return result


@router.get("/chats/{chat_id}", response_model=dict)
async def get_chat(chat_id: int):
    inbox = MongoInboxService()
    doc = await inbox.get_chat_by_id(chat_id)
    if not doc:
        raise HTTPException(404, "Chat not found")
    return _serialize_chat(doc)


@router.patch("/chats/{chat_id}")
async def update_chat(
    chat_id: int,
    req: ChatUpdateRequest,
    background: BackgroundTasks,
    db: Session = Depends(get_db),
    agent: Agent = Depends(get_current_agent),
):
    inbox = MongoInboxService()
    prev = await inbox.get_chat_by_id(chat_id)
    if not prev:
        raise HTTPException(404, "Chat not found")
    prev_assigned = prev.get("assigned_to")

    updates = req.model_dump(exclude_none=True)
    if "ai_active" in updates:
        updates["ai_state"] = "ACTIVE" if updates["ai_active"] else "INACTIVE"

    await inbox.update_chat(chat_id, **updates)

    if "assigned_to" in updates and updates["assigned_to"] != prev_assigned:
        log_activity(
            db, "chat_assigned", entity_type="chat", entity_id=chat_id,
            agent_id=agent.id,
            description=f"Chat '{prev.get('name')}' assigned to agent #{updates['assigned_to']}",
        )
        background.add_task(fire_trigger, "chat_assigned", {
            "chat_id": chat_id,
            "chat_wid": prev.get("chat_wid"),
            "chat_name": prev.get("name"),
            "assigned_to": updates["assigned_to"],
            "is_group": prev.get("is_group"),
        })
    return {"ok": True}


@router.post("/chats/{chat_id}/read")
async def mark_read(chat_id: int):
    await MongoInboxService().mark_chat_read(chat_id)
    return {"ok": True}


@router.get("/chats/{chat_id}/messages", response_model=list[dict])
async def get_messages(
    chat_id: int,
    limit: int = 50,
    before_id: int | None = None,
    before_ts: str | None = None,
    db: Session = Depends(get_db),
):
    inbox = MongoInboxService()
    chat = await inbox.get_chat_by_id(chat_id)
    if not chat:
        raise HTTPException(404, "Chat not found")

    msgs = await inbox.get_messages(
        chat_id=chat_id, limit=limit, before_id=before_id, before_ts=before_ts
    )

    # Lazy-load from WAHA if DB has no messages yet for this chat
    if not msgs and not before_id and not before_ts:
        phone = db.query(Phone).filter(Phone.id == chat["phone_id"]).first()
        if phone and phone.waha_status == "WORKING":
            try:
                waha = WAHAService.from_phone(phone)
                waha_msgs = await waha.get_messages(chat["chat_wid"], limit=50)
                await _store_waha_messages(inbox, waha_msgs, chat, phone)
                msgs = await inbox.get_messages(chat_id=chat_id, limit=limit)
            except Exception as exc:
                logger.warning("Lazy-load messages failed for chat %d: %s", chat_id, exc)

    return [_serialize_message(m) for m in msgs]


async def _store_waha_messages(
    inbox: MongoInboxService,
    waha_msgs: list[dict],
    chat: dict,
    phone: Any,
) -> None:
    from app.api.webhooks import _MEDIA_TYPES as _media_types, _MEDIA_LABELS as _media_labels, _SYSTEM_LABELS as _system_labels
    from datetime import timezone
    for m in waha_msgs:
        raw_id = m.get("id") or {}
        msg_wid = raw_id.get("_serialized") or raw_id.get("id", "") if isinstance(raw_id, dict) else str(raw_id or "")
        if not msg_wid:
            continue
        from_me = bool(m.get("fromMe") or m.get("from_me", False))
        body = m.get("body") or m.get("caption") or ""
        ts_raw = m.get("timestamp")
        ts = datetime.fromtimestamp(ts_raw, tz=timezone.utc).replace(tzinfo=None) if isinstance(ts_raw, (int, float)) else datetime.utcnow()
        msg_type = str(m.get("type") or "text").lower()
        has_media = msg_type in _media_types or bool(m.get("hasMedia") or m.get("has_media"))
        if not body:
            if has_media:
                body = _media_labels.get(msg_type, "📎 Media")
            elif msg_type in _system_labels:
                body = _system_labels[msg_type]
        sender_name = m.get("notifyName") or m.get("pushName") or ""
        from_raw = m.get("from") or m.get("author") or ""
        sender_number = str(from_raw.get("_serialized") or from_raw.get("id", "") if isinstance(from_raw, dict) else from_raw).split("@")[0]
        try:
            await inbox.upsert_message({
                "chat_id": chat["id"],
                "chat_wid": chat["chat_wid"],
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
        except Exception:
            pass


@router.post("/send")
async def send_message(
    req: SendMessageRequest,
    db: Session = Depends(get_db),
    agent: Agent = Depends(get_current_agent),
):
    inbox = MongoInboxService()
    chat = await inbox.get_chat_by_id(req.chat_id)
    if not chat:
        raise HTTPException(404, "Chat not found")

    phone = db.query(Phone).filter(Phone.id == (req.phone_id or chat["phone_id"])).first()
    if not phone:
        raise HTTPException(404, "Phone not found")

    from app.services.waha_service import SendResult
    import time
    if settings.environment == "development" and phone.waha_status != "WORKING":
        result = SendResult(message_id=f"mock_{int(time.time())}_{chat['id']}", raw={"status": "mock_sent"})
    else:
        waha = WAHAService.from_phone(phone)
        try:
            if req.message_type == "image" and req.media_url:
                result = await waha.send_image(chat["chat_wid"], req.media_url, caption=req.body)
            elif req.message_type == "file" and req.media_url:
                result = await waha.send_file(chat["chat_wid"], req.media_url, caption=req.body)
            else:
                result = await waha.send_text(chat["chat_wid"], req.body)
        except Exception as exc:
            if settings.environment == "development":
                result = SendResult(message_id=f"mock_{int(time.time())}_{chat['id']}", raw={"status": "mock_sent"})
            else:
                raise HTTPException(500, f"Send failed: {exc}")

    msg = await inbox.upsert_message({
        "chat_id": chat["id"],
        "chat_wid": chat["chat_wid"],
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

    # Human replied — snooze AI
    if chat.get("ai_active") and chat.get("ai_state") != "SNOOZED":
        await inbox.update_chat(chat["id"], ai_state="SNOOZED", ai_snoozed_at=datetime.utcnow())

    from app.core.ws_manager import ws_manager
    sent_ts = msg.get("timestamp") or datetime.utcnow()
    ts_int = int(sent_ts.timestamp()) if isinstance(sent_ts, datetime) else int(sent_ts)
    await ws_manager.emit_new_message(
        chat_id=chat["id"],
        chat_wid=chat["chat_wid"],
        body=req.body,
        from_me=True,
        sender_name=agent.name or "Agent",
        sender_number=phone.phone_number or "",
        timestamp=ts_int,
        message_type=req.message_type if req.media_url else "text",
        has_media=bool(req.media_url),
        chat_name=chat.get("name") or "",
        unread_count=0,
    )

    return {"ok": True, "message_id": msg.get("id")}


@router.post("/chats/{chat_id}/sync-messages")
async def sync_chat_messages(chat_id: int, limit: int = 50, db: Session = Depends(get_db)):
    """Fetch recent messages from WAHA for a chat and save to MongoDB."""
    inbox = MongoInboxService()
    chat = await inbox.get_chat_by_id(chat_id)
    if not chat:
        raise HTTPException(404, "Chat not found")
    phone = db.query(Phone).filter(Phone.id == chat["phone_id"]).first()
    if not phone:
        raise HTTPException(404, "Phone not found")

    waha = WAHAService.from_phone(phone)
    try:
        messages = await waha.get_messages(chat["chat_wid"], limit=min(max(limit, 1), 500))
    except Exception:
        messages = []

    await _store_waha_messages(inbox, messages, chat, phone)
    return {"synced": len(messages)}


class BulkChatUpdateRequest(BaseModel):
    chat_ids: list[int]
    updates: dict | None = None
    mark_read: bool | None = None
    add_label_id: int | None = None
    remove_label_id: int | None = None


@router.post("/bulk-update")
async def bulk_update_chats(
    req: BulkChatUpdateRequest,
    db: Session = Depends(get_db),
    agent: Agent = Depends(get_current_agent),
):
    if not req.chat_ids:
        raise HTTPException(400, "No chats selected")
    ids = req.chat_ids[:500]
    inbox = MongoInboxService()

    allowed = {"is_archived", "is_pinned", "ai_active", "is_flagged"}
    updates = {k: v for k, v in (req.updates or {}).items() if k in allowed}
    if "ai_active" in updates:
        updates["ai_state"] = "ACTIVE" if updates["ai_active"] else "INACTIVE"
    if updates:
        await inbox.bulk_update_chats(ids, **updates)

    if req.mark_read is True:
        await inbox.bulk_update_chats(ids, unread_count=0)
    elif req.mark_read is False:
        # Only mark unread if currently at 0
        for cid in ids:
            doc = await inbox.get_chat_by_id(cid)
            if doc and (doc.get("unread_count") or 0) == 0:
                await inbox.update_chat(cid, unread_count=1)

    if req.add_label_id:
        for cid in ids:
            await inbox.add_label_to_chat(cid, req.add_label_id)
    if req.remove_label_id:
        for cid in ids:
            await inbox.remove_label_from_chat(cid, req.remove_label_id)

    log_activity(
        db, "chats_bulk_updated", entity_type="chat", agent_id=agent.id,
        description=f"Bulk update on {len(ids)} chats",
        metadata={"chat_ids": ids},
    )
    return {"updated": len(ids)}


@router.post("/chats/{chat_id}/labels/{label_id}")
async def add_label(
    chat_id: int,
    label_id: int,
    background: BackgroundTasks,
):
    await MongoInboxService().add_label_to_chat(chat_id, label_id)
    background.add_task(fire_trigger, "label_added", {
        "chat_id": chat_id, "label_id": label_id, "source": "manual",
    })
    return {"ok": True}


@router.delete("/chats/{chat_id}/labels/{label_id}")
async def remove_label(chat_id: int, label_id: int):
    await MongoInboxService().remove_label_from_chat(chat_id, label_id)
    return {"ok": True}


@router.post("/sync/{phone_id}")
async def sync_chats(phone_id: int, db: Session = Depends(get_db)):
    """Pull all chats from WAHA and upsert into MongoDB for this phone."""
    from datetime import timezone as _tz

    phone = db.query(Phone).filter(Phone.id == phone_id).first()
    if not phone:
        raise HTTPException(404, "Phone not found")

    waha = WAHAService.from_phone(phone)
    chats = await waha.get_chats(limit=500)
    inbox = MongoInboxService()
    synced = 0

    _media_labels = {
        "image": "📷 Photo", "photo": "📷 Photo", "video": "🎬 Video",
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

        await inbox.upsert_chat(chat_data)
        synced += 1

    # Fix group chats with WID-looking names
    stale_cursor = inbox.db.chats.find(
        {"phone_id": phone_id, "is_group": True, "name": {"$regex": "@g.us", "$options": "i"}},
        limit=100,
    )
    async for stale_chat in stale_cursor:
        try:
            info = await waha.get_group_info(stale_chat["chat_wid"])
            subject = info.get("subject") or info.get("name") or ""
            if subject:
                await inbox.update_chat(stale_chat["id"], name=subject)
        except Exception:
            continue

    return {"synced": synced}
