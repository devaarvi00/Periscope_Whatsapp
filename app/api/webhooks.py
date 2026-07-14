import logging
from datetime import datetime
from typing import Any

from fastapi import APIRouter, BackgroundTasks, Header, HTTPException, Request
from sqlalchemy import delete

from app.core.config import settings
from app.db.session import SessionLocal
from app.models.phone import Phone
from app.services.mongo_chat_service import MongoInboxService

router = APIRouter(prefix="/webhooks", tags=["webhooks"])
logger = logging.getLogger(__name__)

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


async def _process_message_event(payload: dict[str, Any]) -> None:
    db = SessionLocal()
    try:
        session = payload.get("session", settings.waha_session_name)
        phone = db.query(Phone).filter(Phone.session_name == session).first()
        if not phone:
            logger.warning("Webhook: unknown session '%s'", session)
            return

        msg_data = payload.get("payload") or payload
        if not msg_data:
            return

        msg_id = msg_data.get("id")
        if isinstance(msg_id, dict):
            msg_wid = msg_id.get("_serialized") or msg_id.get("id", "")
        else:
            msg_wid = str(msg_id or "")
        if not msg_wid:
            return

        from_me = bool(msg_data.get("fromMe", False))
        chat_data = msg_data.get("chatId")
        if not chat_data:
            chat_data = msg_data.get("to") if from_me else msg_data.get("from")

        if isinstance(chat_data, dict):
            chat_wid = chat_data.get("_serialized") or chat_data.get("id", "")
        else:
            chat_wid = str(chat_data or "")
        if not chat_wid:
            return

        inbox = MongoInboxService()

        # Dedup before any work — fast path
        if await inbox.message_exists(msg_wid):
            return

        notify_name = msg_data.get("notifyName") or msg_data.get("_data", {}).get("notifyName") or ""
        chat = await inbox.get_chat_by_wid(chat_wid, phone.id)
        chat_is_new = chat is None

        if not chat:
            is_group = chat_wid.endswith("@g.us")
            chat_name = ""
            if is_group:
                try:
                    from app.services.waha_service import WAHAService
                    info = await WAHAService.from_phone(phone).get_group_info(chat_wid)
                    chat_name = info.get("subject") or info.get("name") or ""
                except Exception:
                    pass
                chat_name = chat_name or f"Group {chat_wid.split('@')[0][-6:]}"
            else:
                chat_name = notify_name or chat_wid.split("@")[0]

            from app.models.ai_settings import get_ai_settings
            _cfg = get_ai_settings(db)
            chat = await inbox.upsert_chat({
                "chat_wid": chat_wid,
                "phone_id": phone.id,
                "name": str(chat_name),
                "is_group": is_group,
                "ai_active": bool(_cfg.enabled and _cfg.auto_activate_new_chats),
                "ai_state": "ACTIVE" if (_cfg.enabled and _cfg.auto_activate_new_chats) else "INACTIVE",
            })
        elif not (chat.get("is_group")) and notify_name:
            current = chat.get("name") or ""
            if current == chat_wid or current == chat_wid.split("@")[0] or "@" in current:
                await inbox.update_chat(chat["id"], name=notify_name)
                chat["name"] = notify_name

        body = msg_data.get("body") or msg_data.get("caption") or ""
        ts_raw = msg_data.get("timestamp")
        if isinstance(ts_raw, (int, float)):
            ts = datetime.utcfromtimestamp(ts_raw)
        else:
            ts = datetime.utcnow()

        msg_type = str(msg_data.get("type") or "text").lower()
        waha_has_media = bool(msg_data.get("hasMedia") or msg_data.get("has_media"))
        has_media = msg_type in _MEDIA_TYPES or waha_has_media
        if not body and has_media:
            body = _MEDIA_LABELS.get(msg_type, "📎 Media")

        sender_name = msg_data.get("notifyName") or msg_data.get("pushName") or ""
        from_raw = msg_data.get("from") or msg_data.get("author") or ""
        if isinstance(from_raw, dict):
            sender_number = str(from_raw.get("_serialized") or from_raw.get("id", "")).split("@")[0]
        else:
            sender_number = str(from_raw).split("@")[0]

        await inbox.upsert_message({
            "chat_id": chat["id"],
            "chat_wid": chat_wid,
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

        if not from_me:
            new_unread = (chat.get("unread_count") or 0) + 1
            await inbox.update_chat(chat["id"], unread_count=new_unread)
            chat["unread_count"] = new_unread

        from app.core.ws_manager import ws_manager
        await ws_manager.emit_new_message(
            chat_id=chat["id"],
            chat_wid=chat_wid,
            body=body,
            from_me=from_me,
            sender_name=sender_name or "",
            sender_number=sender_number or "",
            timestamp=int(ts.timestamp()),
            message_type=msg_type,
            has_media=has_media,
            chat_name=chat.get("name") or "",
            unread_count=chat.get("unread_count") or 0,
        )

        from app.services.webhook_dispatcher import dispatch_event
        if chat_is_new:
            await dispatch_event("chat.created", {
                "chat_id": chat["id"], "chat_wid": chat_wid,
                "name": chat.get("name"), "is_group": chat.get("is_group"),
            })
        await dispatch_event("message.sent" if from_me else "message.received", {
            "chat_id": chat["id"], "chat_wid": chat_wid, "body": body,
            "sender_name": sender_name, "sender_number": sender_number,
            "type": msg_type, "timestamp": ts.isoformat(),
        })

        from app.services.automation_service import AutomationService
        automation = AutomationService(db)
        rule_context = {
            "chat_id": chat["id"],
            "chat_wid": chat_wid,
            "chat_name": chat.get("name"),
            "message": body,
            "from_me": from_me,
            "is_group": chat.get("is_group"),
            "sender_name": sender_name,
            "sender_number": sender_number,
        }
        if chat_is_new:
            await automation.run_rules("chat_created", rule_context)
        await automation.run_rules("message_received", rule_context)
        if body:
            await automation.run_rules("message_keyword", rule_context)

        # AI auto-flag
        from app.models.ai_settings import get_ai_settings as _get_ai_cfg
        _ai_cfg = _get_ai_cfg(db)
        _flag_on = settings.ai_auto_flag_enabled or _ai_cfg.flag_enabled
        _flag_criteria = _ai_cfg.flag_criteria or settings.ai_auto_flag_criteria
        if not from_me and body and _flag_on:
            try:
                from app.services.gemini_service import GeminiService
                if await GeminiService().flag_message(body, _flag_criteria):
                    await inbox.flag_message(msg_wid, True)
                    await inbox.update_chat(chat["id"], is_flagged=True)
                    from app.core.ws_manager import ws_manager as _ws
                    await _ws.emit_chat_updated(chat["id"], {"is_flagged": True})
            except Exception as exc:
                logger.warning("AI auto-flag failed: %s", exc)

        # AI agent
        if not from_me and chat.get("ai_active") and chat.get("ai_state") != "SNOOZED":
            from app.services.ai_agent_service import AIAgentService
            from app.services.waha_service import WAHAService
            ai = AIAgentService(db)
            recent = [{"body": body, "from_me": from_me, "sender_name": sender_name}]
            reply = await ai.handle_incoming_message(chat, body, recent)
            if reply:
                waha = WAHAService.from_phone(phone)
                ai_result = await waha.send_text(chat_wid, reply)
                ai_ts = datetime.utcnow()
                ai_wid = ai_result.message_id if ai_result.message_id else f"ai_{msg_wid}"
                try:
                    await inbox.upsert_message({
                        "chat_id": chat["id"],
                        "chat_wid": chat_wid,
                        "phone_id": phone.id,
                        "message_wid": ai_wid,
                        "from_me": True,
                        "sender_name": "AI Agent",
                        "sender_number": phone.phone_number,
                        "body": reply,
                        "message_type": "text",
                        "timestamp": ai_ts,
                    })
                except Exception:
                    pass
                from app.core.ws_manager import ws_manager as _ws
                await _ws.emit_new_message(
                    chat_id=chat["id"],
                    chat_wid=chat_wid,
                    body=reply,
                    from_me=True,
                    sender_name="AI Agent",
                    sender_number=phone.phone_number,
                    timestamp=int(ai_ts.timestamp()),
                    message_type="text",
                    chat_name=chat.get("name") or "",
                    unread_count=0,
                )

    except Exception as exc:
        logger.exception("Webhook processing error: %s", exc)
    finally:
        db.close()


async def _process_reaction_event(payload: dict[str, Any]) -> None:
    """Create a ticket when a message is reacted to with a ticket emoji."""
    db = SessionLocal()
    try:
        session = payload.get("session", settings.waha_session_name)
        data = payload.get("payload") or {}
        reaction = data.get("reaction") or {}
        emoji = str(reaction.get("text") or "").strip()
        if not emoji or emoji not in settings.ticket_emoji_reactions:
            return

        msg_id = reaction.get("messageId") or data.get("messageId")
        if isinstance(msg_id, dict):
            msg_wid = msg_id.get("_serialized") or msg_id.get("id", "")
        else:
            msg_wid = str(msg_id or "")
        if not msg_wid:
            return

        inbox = MongoInboxService()
        message = await inbox.get_message_by_wid(msg_wid)
        if not message:
            return

        from app.models.ticket import Ticket
        existing = db.query(Ticket).filter(Ticket.message_wid == msg_wid).first()
        if existing:
            return

        chat = await inbox.get_chat_by_id(message["chat_id"])
        title = (message.get("body") or "").strip()[:120] or f"Ticket from {chat.get('name') if chat else 'chat'}"
        ticket = Ticket(
            chat_id=message["chat_id"],
            message_wid=msg_wid,
            title=f"{emoji} {title}",
            description=message.get("body") or "",
        )
        db.add(ticket)
        db.commit()
        db.refresh(ticket)
        logger.info("Emoji reaction %s created ticket %s (session %s)", emoji, ticket.id, session)

        from app.services.activity_service import log_activity
        log_activity(
            db, "ticket_created_via_emoji", entity_type="ticket", entity_id=ticket.id,
            description=f"Ticket created from {emoji} reaction on message {msg_wid}",
        )

        from app.core.ws_manager import ws_manager
        await ws_manager.emit_ticket_event("ticket_created", ticket.id, {"chat_id": message["chat_id"]})

        from app.services.automation_service import AutomationService
        await AutomationService(db).run_rules("ticket_created", {
            "chat_id": message["chat_id"],
            "ticket_id": ticket.id,
            "title": ticket.title,
            "priority": "medium",
            "status": "open",
            "message": message.get("body") or "",
            "source": "emoji_reaction",
        })
    except Exception as exc:
        logger.exception("Reaction webhook error: %s", exc)
    finally:
        db.close()


async def _process_session_status(payload: dict[str, Any]) -> None:
    """Update phone WAHA status in DB and notify the frontend."""
    db = SessionLocal()
    try:
        session_name = payload.get("session", "")
        status_payload = payload.get("payload") or {}
        raw_status = str(status_payload.get("status", "")).upper()

        STATUS_MAP = {
            "WORKING": "WORKING", "CONNECTED": "WORKING", "AUTHENTICATED": "WORKING",
            "STOPPED": "STOPPED", "FAILED": "FAILED", "STARTING": "STARTING",
            "SCAN_QR_CODE": "SCAN_QR_CODE", "DISCONNECTED": "STOPPED",
        }
        db_status = STATUS_MAP.get(raw_status)
        if not db_status:
            return

        phone = db.query(Phone).filter(Phone.session_name == session_name).first()
        if not phone:
            return

        phone.waha_status = db_status
        db.commit()
        logger.info("Session status: session=%s raw=%s db=%s phone_id=%d",
                    session_name, raw_status, db_status, phone.id)

        from app.core.ws_manager import ws_manager
        await ws_manager.broadcast("phone_status_changed", {
            "phone_id": phone.id, "status": db_status,
        })
        if db_status in ("STOPPED", "FAILED"):
            await ws_manager.broadcast("data_cleared", {"phone_id": phone.id, "reason": db_status})

        # Auto-sync chats when session becomes WORKING
        if db_status == "WORKING":
            import asyncio
            asyncio.create_task(_auto_sync_chats(phone.id))

    except Exception as exc:
        logger.exception("session.status error: %s", exc)
    finally:
        db.close()


async def _auto_sync_chats(phone_id: int) -> None:
    """Background task: pull all chats from WAHA and upsert into MongoDB."""
    import asyncio
    await asyncio.sleep(2)  # Let WAHA settle before querying
    db = SessionLocal()
    try:
        phone = db.query(Phone).filter(Phone.id == phone_id).first()
        if not phone:
            return
        from app.services.waha_service import WAHAService
        waha = WAHAService.from_phone(phone)
        chats = await waha.get_chats(limit=500)
        inbox = MongoInboxService()
        for c in chats:
            cid = c.get("id") or c.get("chatId") or c.get("_serialized") or ""
            if isinstance(cid, dict):
                cid = cid.get("_serialized") or cid.get("id", "")
            if not cid:
                continue
            is_group = str(cid).endswith("@g.us")
            name = c.get("name") or c.get("subject") or ""
            if not name or "@" in name:
                name = str(cid).split("@")[0]
            data: dict = {"chat_wid": str(cid), "phone_id": phone_id, "name": name, "is_group": is_group}
            last_msg = c.get("lastMessage") or {}
            if last_msg:
                lm_body = last_msg.get("body") or last_msg.get("caption") or ""
                lm_type = str(last_msg.get("type") or "text").lower()
                if not lm_body:
                    lm_body = _MEDIA_LABELS.get(lm_type, "📎 Media")
                ts_raw = last_msg.get("timestamp")
                if lm_body:
                    data["last_message"] = lm_body[:200]
                if isinstance(ts_raw, (int, float)):
                    data["last_message_at"] = datetime.utcfromtimestamp(ts_raw)
            await inbox.upsert_chat(data)
        logger.info("Auto-synced %d chats for phone_id=%d", len(chats), phone_id)
    except Exception as exc:
        logger.warning("Auto-sync chats failed for phone_id=%d: %s", phone_id, exc)
    finally:
        db.close()


@router.post("/waha")
async def waha_webhook(
    request: Request,
    background: BackgroundTasks,
    x_webhook_secret: str | None = Header(None),
):
    secret = settings.waha_webhook_secret.strip()
    if secret and x_webhook_secret != secret:
        raise HTTPException(status_code=403, detail="Invalid webhook secret")

    try:
        import json
        body = await request.json()
        logger.debug("WAHA WEBHOOK BODY: %s", json.dumps(body))
    except Exception as exc:
        logger.exception("Failed to parse webhook JSON: %s", exc)
        raise HTTPException(status_code=400, detail="Invalid JSON")

    event = body.get("event", "")
    logger.debug("WAHA event: %s", event)

    if event in ("message", "message.any", "message_create"):
        background.add_task(_process_message_event, body)
    elif event == "message.reaction":
        background.add_task(_process_reaction_event, body)
    elif event == "session.status":
        background.add_task(_process_session_status, body)

    return {"status": "ok"}
