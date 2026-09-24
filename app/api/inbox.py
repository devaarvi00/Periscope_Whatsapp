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
from app.services.access import (
    assert_phone_access, filter_accessible_chat_ids, get_accessible_chat,
)
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
    status: str | None = None,
    limit: int = 200,
    offset: int = 0,
    db: Session = Depends(get_db),
    agent: Agent = Depends(get_current_agent),
):
    from app.core.permissions import allowed_phone_ids
    if phone_id is not None:
        assert_phone_access(db, agent, phone_id)
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
        status=status,
        limit=max(1, min(limit, 500)),
        offset=max(0, offset),
    )
    # last_message_from_me (for the "awaiting reply" filter) is maintained on
    # the chat doc; only chats written before that field existed need a lookup.
    result = []
    for doc in docs:
        serialized = _serialize_chat(doc)
        if serialized.get("last_message_from_me") is None:
            msgs = await inbox.get_messages(chat_id=doc["id"], limit=1)
            serialized["last_message_from_me"] = msgs[0].get("from_me") if msgs else None
        result.append(serialized)
    return result


@router.get("/chats/{chat_id}", response_model=dict)
async def get_chat(
    chat_id: int,
    db: Session = Depends(get_db),
    agent: Agent = Depends(get_current_agent),
):
    doc = await get_accessible_chat(db, agent, chat_id)
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
    prev = await get_accessible_chat(db, agent, chat_id)
    prev_assigned = prev.get("assigned_to")

    # exclude_unset: only fields present in the body. `"assigned_to": null`
    # therefore unassigns; other explicit nulls are ignored.
    updates = req.model_dump(exclude_unset=True)
    updates = {k: v for k, v in updates.items() if v is not None or k == "assigned_to"}
    if "ai_active" in updates:
        updates["ai_state"] = "ACTIVE" if updates["ai_active"] else "INACTIVE"

    await inbox.update_chat(chat_id, **updates)

    if "status" in updates and updates["status"] != (prev.get("status") or "open"):
        log_activity(
            db, "chat_resolved" if updates["status"] == "resolved" else "chat_reopened",
            entity_type="chat", entity_id=chat_id, agent_id=agent.id,
            description=f"Chat '{prev.get('name')}' marked {updates['status']}",
        )

    if "ai_flagging" in updates and updates["ai_flagging"] != (prev.get("ai_flagging") is not False):
        log_activity(
            db, "chat_ai_flagging", entity_type="chat", entity_id=chat_id, agent_id=agent.id,
            description=f"AI flagging {'allowed' if updates['ai_flagging'] else 'turned off'} for '{prev.get('name')}'",
        )

    if "assigned_to" in updates and updates["assigned_to"] is None and prev_assigned is not None:
        log_activity(
            db, "chat_unassigned", entity_type="chat", entity_id=chat_id,
            agent_id=agent.id,
            description=f"Chat '{prev.get('name')}' unassigned",
        )
    elif "assigned_to" in updates and updates["assigned_to"] != prev_assigned:
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
        await _notify_chat_assigned(prev, updates["assigned_to"], agent)
    return {"ok": True}


def _chat_display_name(chat: dict) -> str:
    """Same rules as the frontend's displayName(): never leak raw WIDs."""
    name = chat.get("name") or chat.get("chat_wid") or ""
    if "@" not in name:
        return name
    ident, _, domain = name.partition("@")
    if domain == "g.us":
        return f"Group {ident[-6:]}"
    if domain == "lid":
        return "WhatsApp user"
    return f"+{ident}" if ident.isdigit() and len(ident) >= 6 else ident


async def _notify_chat_assigned(chat: dict, assignee_id: int | None, actor: Agent) -> None:
    """Tell the new assignee (all their tabs) — never the agent who did it."""
    if assignee_id is None or assignee_id == actor.id:
        return
    from app.core.ws_manager import ws_manager
    await ws_manager.send_to_agent(assignee_id, "chat_assigned", {
        "chat_id": chat["id"],
        "chat_name": _chat_display_name(chat),
        "by": actor.name,
    })


@router.get("/chats/{chat_id}/activity")
async def chat_activity(
    chat_id: int,
    limit: int = 50,
    db: Session = Depends(get_db),
    agent: Agent = Depends(get_current_agent),
):
    """Activity-log entries for one chat (assignments, resolves, …), newest first."""
    from app.models.activity_log import ActivityLog
    await get_accessible_chat(db, agent, chat_id)
    rows = (
        db.query(ActivityLog)
        .filter(ActivityLog.entity_type == "chat", ActivityLog.entity_id == chat_id)
        .order_by(ActivityLog.id.desc())
        .limit(max(1, min(limit, 200)))
        .all()
    )
    names = {a.id: a.name for a in db.query(Agent).all()}
    return [{
        "id": r.id,
        "action": r.action,
        "description": r.description or "",
        "agent_name": names.get(r.agent_id, "") if r.agent_id else "",
        "created_at": r.created_at.isoformat() if r.created_at else None,
    } for r in rows]


@router.get("/chats/{chat_id}/team")
async def chat_team(
    chat_id: int,
    db: Session = Depends(get_db),
    agent: Agent = Depends(get_current_agent),
):
    """Active agents who can open this chat (admins, unrestricted agents and
    agents granted its number), with live online state."""
    from app.core.ws_manager import ws_manager
    from app.models.agent_phone import AgentPhone
    chat = await get_accessible_chat(db, agent, chat_id)
    phone_id = chat.get("phone_id")
    grants: dict[int, set[int]] = {}
    for aid, pid in db.query(AgentPhone.agent_id, AgentPhone.phone_id).all():
        grants.setdefault(aid, set()).add(pid)
    online = ws_manager.online_agent_ids()
    out = []
    for a in db.query(Agent).filter(Agent.is_active.is_(True)).order_by(Agent.name).all():
        role = getattr(a.role, "value", a.role)
        if role != "admin" and a.id in grants and phone_id not in grants[a.id]:
            continue
        out.append({"id": a.id, "name": a.name, "role": role, "online": a.id in online})
    return out


@router.get("/chats/{chat_id}/picture")
async def chat_picture(
    chat_id: int,
    refresh: bool = False,
    db: Session = Depends(get_db),
    agent: Agent = Depends(get_current_agent),
):
    """Profile / group picture URL, looked up from WAHA at most once a day."""
    from datetime import timedelta
    chat = await get_accessible_chat(db, agent, chat_id)
    checked = chat.get("picture_checked_at")
    if not refresh and isinstance(checked, datetime) and datetime.utcnow() - checked < timedelta(hours=24):
        return {"url": chat.get("picture_url") or None}
    phone = db.query(Phone).filter(Phone.id == chat["phone_id"]).first()
    if not phone or phone.waha_status != "WORKING":
        return {"url": chat.get("picture_url") or None}
    url = await WAHAService.from_phone(phone).get_chat_picture(chat["chat_wid"])
    # Only WhatsApp's own CDN over https ever reaches an <img src>
    from urllib.parse import urlsplit
    try:
        parts = urlsplit(url or "")
        ok = parts.scheme == "https" and (parts.hostname or "").endswith(".whatsapp.net")
    except ValueError:
        ok = False
    url = url if ok else None
    await MongoInboxService().update_chat(chat_id, picture_url=url or "", picture_checked_at=datetime.utcnow())
    return {"url": url}


@router.post("/chats/{chat_id}/read")
async def mark_read(
    chat_id: int,
    db: Session = Depends(get_db),
    agent: Agent = Depends(get_current_agent),
):
    await get_accessible_chat(db, agent, chat_id)
    await MongoInboxService().mark_chat_read(chat_id)
    return {"ok": True}


@router.get("/chats/{chat_id}/messages", response_model=list[dict])
async def get_messages(
    chat_id: int,
    limit: int = 50,
    before_id: int | None = None,
    before_ts: str | None = None,
    db: Session = Depends(get_db),
    agent: Agent = Depends(get_current_agent),
):
    inbox = MongoInboxService()
    chat = await get_accessible_chat(db, agent, chat_id)
    limit = max(1, min(limit, 200))

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
    from app.core.message_labels import _MEDIA_TYPES as _media_types, _MEDIA_LABELS as _media_labels, _SYSTEM_LABELS as _system_labels
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
    chat = await get_accessible_chat(db, agent, req.chat_id)

    # Always send from the chat's own number when it exists; a client-supplied
    # phone_id is only a fallback and must be one the agent may use.
    phone = db.query(Phone).filter(Phone.id == chat["phone_id"]).first()
    if not phone and req.phone_id is not None:
        assert_phone_access(db, agent, req.phone_id)
        phone = db.query(Phone).filter(Phone.id == req.phone_id).first()
    if not phone:
        raise HTTPException(404, "Phone not found")

    if req.media_url:
        from app.services.url_safety import UnsafeURLError, assert_public_url
        try:
            await assert_public_url(req.media_url)
        except UnsafeURLError as exc:
            raise HTTPException(400, f"Invalid media_url: {exc}")

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
                logger.exception("Send failed for chat %s: %s", chat["id"], exc)
                raise HTTPException(502, "Failed to send message via WhatsApp")

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
async def sync_chat_messages(
    chat_id: int,
    limit: int = 50,
    db: Session = Depends(get_db),
    agent: Agent = Depends(get_current_agent),
):
    """Fetch recent messages from WAHA for a chat and save to MongoDB."""
    inbox = MongoInboxService()
    chat = await get_accessible_chat(db, agent, chat_id)
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
    ids = await filter_accessible_chat_ids(db, agent, req.chat_ids[:500])
    if not ids:
        raise HTTPException(404, "No accessible chats selected")
    inbox = MongoInboxService()

    allowed = {"is_archived", "is_pinned", "ai_active", "is_flagged", "status", "assigned_to"}
    updates = {k: v for k, v in (req.updates or {}).items() if k in allowed}
    if "status" in updates and updates["status"] not in ("open", "resolved"):
        raise HTTPException(400, "status must be 'open' or 'resolved'")
    if "assigned_to" in updates and updates["assigned_to"] is not None:
        try:
            updates["assigned_to"] = int(updates["assigned_to"])
        except (TypeError, ValueError):
            raise HTTPException(400, "assigned_to must be an agent id or null")
    if "ai_active" in updates:
        updates["ai_state"] = "ACTIVE" if updates["ai_active"] else "INACTIVE"
    # Snapshot previous assignees so only real changes notify
    prev_chats: list[dict] = []
    if "assigned_to" in updates:
        prev_chats = await inbox.db.chats.find(
            {"id": {"$in": ids}}, {"id": 1, "name": 1, "chat_wid": 1, "assigned_to": 1}
        ).to_list(length=len(ids))
    if updates:
        await inbox.bulk_update_chats(ids, **updates)
    if "assigned_to" in updates:
        new_assignee = updates["assigned_to"]
        for c in prev_chats:
            if c.get("assigned_to") != new_assignee:
                await _notify_chat_assigned(c, new_assignee, agent)

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
    db: Session = Depends(get_db),
    agent: Agent = Depends(get_current_agent),
):
    await get_accessible_chat(db, agent, chat_id)
    await MongoInboxService().add_label_to_chat(chat_id, label_id)
    background.add_task(fire_trigger, "label_added", {
        "chat_id": chat_id, "label_id": label_id, "source": "manual",
    })
    return {"ok": True}


@router.delete("/chats/{chat_id}/labels/{label_id}")
async def remove_label(
    chat_id: int,
    label_id: int,
    db: Session = Depends(get_db),
    agent: Agent = Depends(get_current_agent),
):
    await get_accessible_chat(db, agent, chat_id)
    await MongoInboxService().remove_label_from_chat(chat_id, label_id)
    return {"ok": True}


@router.post("/sync/{phone_id}")
async def sync_chats(
    phone_id: int,
    db: Session = Depends(get_db),
    agent: Agent = Depends(get_current_agent),
):
    """Pull all chats from WAHA and upsert into MongoDB for this phone."""
    from datetime import timezone as _tz

    phone = db.query(Phone).filter(Phone.id == phone_id).first()
    if not phone:
        raise HTTPException(404, "Phone not found")
    assert_phone_access(db, agent, phone.id)

    waha = WAHAService.from_phone(phone)
    chats = await waha.get_chats(limit=500)
    inbox = MongoInboxService()
    synced = 0

    from app.core.message_labels import _MEDIA_LABELS as _media_labels

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
