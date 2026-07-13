import logging
from datetime import datetime
from typing import Any

from fastapi import APIRouter, BackgroundTasks, Header, HTTPException, Request
from sqlalchemy import delete

from app.core.config import settings
from app.db.session import SessionLocal
from app.models.chat import Chat, ChatLabel
from app.models.message import Message
from app.models.phone import Phone
from app.services.inbox_service import InboxService

router = APIRouter(prefix="/webhooks", tags=["webhooks"])
logger = logging.getLogger(__name__)


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

        inbox = InboxService(db)

        # Dedup guard: if this message_wid is already in DB, a previous event (or
        # another concurrent background task) already processed it — skip all side effects.
        msg_already_exists = db.query(Message.id).filter(Message.message_wid == msg_wid).scalar() is not None

        chat = inbox.get_chat_by_wid(chat_wid)
        chat_is_new = chat is None
        notify_name = msg_data.get("notifyName") or msg_data.get("_data", {}).get("notifyName") or ""
        if not chat:
            is_group = chat_wid.endswith("@g.us")
            if is_group:
                # Resolve the real group subject from WAHA instead of storing the WID
                chat_name = ""
                try:
                    from app.services.waha_service import WAHAService
                    info = await WAHAService(session_name=session).get_group_info(chat_wid)
                    chat_name = info.get("subject") or info.get("name") or ""
                except Exception:
                    pass
                chat_name = chat_name or f"Group {chat_wid.split('@')[0][-6:]}"
            else:
                chat_name = notify_name or chat_wid.split("@")[0]
            from app.models.ai_settings import get_ai_settings
            _cfg = get_ai_settings(db)
            chat = inbox.upsert_chat({
                "chat_wid": chat_wid,
                "phone_id": phone.id,
                "name": str(chat_name),
                "is_group": is_group,
                # Org setting: auto-activate the AI agent on new chats
                "ai_active": bool(_cfg.enabled and _cfg.auto_activate_new_chats),
                "ai_state": "ACTIVE" if (_cfg.enabled and _cfg.auto_activate_new_chats) else "INACTIVE",
            })
        elif not chat.is_group and notify_name:
            # Upgrade WID-looking names to the sender's push name once we learn it
            current = chat.name or ""
            if current == chat_wid or current == chat_wid.split("@")[0] or "@" in current:
                chat.name = str(notify_name)
                db.commit()

        body = msg_data.get("body") or msg_data.get("caption") or ""
        ts_raw = msg_data.get("timestamp")
        if isinstance(ts_raw, (int, float)):
            ts = datetime.utcfromtimestamp(ts_raw)
        else:
            ts = datetime.utcnow()

        _WEBHOOK_MEDIA_TYPES = {"image", "photo", "video", "audio", "ptt", "voice",
                                 "document", "pdf", "sticker", "gif", "location", "contact", "vcard"}
        _WEBHOOK_MEDIA_LABELS = {
            "image": "📷 Photo", "photo": "📷 Photo",
            "video": "🎬 Video",
            "audio": "🎤 Voice message", "voice": "🎤 Voice message", "ptt": "🎤 Voice message",
            "document": "📄 Document", "pdf": "📄 Document",
            "sticker": "🖼 Sticker", "gif": "🎞 GIF",
            "location": "📍 Location",
            "contact": "👤 Contact", "vcard": "👤 Contact",
        }
        msg_type = str(msg_data.get("type") or "text").lower()
        waha_has_media = bool(msg_data.get("hasMedia") or msg_data.get("has_media"))
        has_media = msg_type in _WEBHOOK_MEDIA_TYPES or waha_has_media
        # Ensure media messages always have a displayable body
        if not body and has_media:
            body = _WEBHOOK_MEDIA_LABELS.get(msg_type, "📎 Media")

        sender_name = msg_data.get("notifyName") or msg_data.get("pushName") or ""
        sender_number = ""
        from_raw = msg_data.get("from") or msg_data.get("author") or ""
        if isinstance(from_raw, dict):
            sender_number = str(from_raw.get("_serialized") or from_raw.get("id", "")).split("@")[0]
        else:
            sender_number = str(from_raw).split("@")[0]

        inbox.upsert_message({
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

        # Duplicate event (message.any fired twice, or race with another task) — stop here.
        if msg_already_exists:
            return

        if not from_me:
            chat.unread_count = (chat.unread_count or 0) + 1
            db.commit()

        # Broadcast real-time update — use int epoch so JSON serialises correctly
        from app.core.ws_manager import ws_manager
        await ws_manager.emit_new_message(
            chat_id=chat.id,
            chat_wid=chat_wid,
            body=body,
            from_me=from_me,
            sender_name=sender_name or "",
            sender_number=sender_number or "",
            timestamp=int(ts.timestamp()),
            message_type=msg_type,
            has_media=has_media,
            chat_name=chat.name or "",
            unread_count=(chat.unread_count or 0),
        )

        # Notify outbound webhook subscribers
        from app.services.webhook_dispatcher import dispatch_event
        if chat_is_new:
            await dispatch_event("chat.created", {
                "chat_id": chat.id, "chat_wid": chat_wid,
                "name": chat.name, "is_group": chat.is_group,
            })
        await dispatch_event("message.sent" if from_me else "message.received", {
            "chat_id": chat.id, "chat_wid": chat_wid, "body": body,
            "sender_name": sender_name, "sender_number": sender_number,
            "type": msg_type, "timestamp": ts.isoformat(),
        })

        # Run automation rules
        from app.services.automation_service import AutomationService
        automation = AutomationService(db)
        rule_context = {
            "chat_id": chat.id,
            "chat_wid": chat_wid,
            "chat_name": chat.name,
            "message": body,
            "from_me": from_me,
            "is_group": chat.is_group,
            "sender_name": sender_name,
            "sender_number": sender_number,
        }
        if chat_is_new:
            await automation.run_rules("chat_created", rule_context)
        await automation.run_rules("message_received", rule_context)
        if body:
            await automation.run_rules("message_keyword", rule_context)

        # AI auto-flag: mark important inbound messages per the configured criteria
        from app.models.ai_settings import get_ai_settings as _get_ai_cfg
        _ai_cfg = _get_ai_cfg(db)
        _flag_on = settings.ai_auto_flag_enabled or _ai_cfg.flag_enabled
        _flag_criteria = _ai_cfg.flag_criteria or settings.ai_auto_flag_criteria
        if not from_me and body and _flag_on:
            try:
                from app.services.gemini_service import GeminiService
                if await GeminiService().flag_message(body, _flag_criteria):
                    msg_row = db.query(Message).filter(Message.message_wid == msg_wid).first()
                    if msg_row:
                        msg_row.is_flagged = True
                    chat.is_flagged = True
                    db.commit()
                    from app.core.ws_manager import ws_manager as _ws
                    await _ws.emit_chat_updated(chat.id, {"is_flagged": True})
            except Exception as exc:
                logger.warning("AI auto-flag failed: %s", exc)

        # AI agent handling
        if not from_me and chat.ai_active and chat.ai_state != "SNOOZED":
            from app.services.ai_agent_service import AIAgentService
            from app.services.waha_service import WAHAService
            ai = AIAgentService(db)
            recent = [{"body": body, "from_me": from_me, "sender_name": sender_name}]
            reply = await ai.handle_incoming_message(chat, body, recent)
            if reply:
                waha = WAHAService(session_name=session)
                ai_result = await waha.send_text(chat_wid, reply)
                ai_ts = datetime.utcnow()
                # Use the real WAHA message_wid so the incoming message.any webhook
                # deduplicates against this row instead of creating a second copy.
                ai_wid = ai_result.message_id if ai_result.message_id else f"ai_{msg_wid}"
                try:
                    inbox.upsert_message({
                        "chat_id": chat.id,
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
                    pass  # Webhook may insert it first — not an error
                from app.core.ws_manager import ws_manager as _ws
                await _ws.emit_new_message(
                    chat_id=chat.id,
                    chat_wid=chat_wid,
                    body=reply,
                    from_me=True,
                    sender_name="AI Agent",
                    sender_number=phone.phone_number,
                    timestamp=int(ai_ts.timestamp()),
                    message_type="text",
                    chat_name=chat.name or "",
                    unread_count=0,
                )

    except Exception as exc:
        logger.exception("Webhook processing error: %s", exc)
    finally:
        db.close()


async def _process_reaction_event(payload: dict[str, Any]) -> None:
    """Create a ticket when a message is reacted to with a ticket emoji (Hyperscope-style)."""
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

        message = db.query(Message).filter(Message.message_wid == msg_wid).first()
        if not message:
            return

        from app.models.ticket import Ticket
        existing = db.query(Ticket).filter(Ticket.message_id == message.id).first()
        if existing:
            return

        chat = db.query(Chat).filter(Chat.id == message.chat_id).first()
        title = (message.body or "").strip()[:120] or f"Ticket from {chat.name if chat else 'chat'}"
        ticket = Ticket(
            chat_id=message.chat_id,
            message_id=message.id,
            title=f"{emoji} {title}",
            description=message.body or "",
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
        await ws_manager.emit_ticket_event("ticket_created", ticket.id, {"chat_id": message.chat_id})

        from app.services.automation_service import AutomationService
        await AutomationService(db).run_rules("ticket_created", {
            "chat_id": message.chat_id,
            "ticket_id": ticket.id,
            "title": ticket.title,
            "priority": "medium",
            "status": "open",
            "message": message.body or "",
            "source": "emoji_reaction",
        })
    except Exception as exc:
        logger.exception("Reaction webhook error: %s", exc)
    finally:
        db.close()


async def _process_session_status(payload: dict[str, Any]) -> None:
    """Update phone WAHA status in DB and notify the frontend when a session state changes."""
    db = SessionLocal()
    try:
        session_name = payload.get("session", "")
        status_payload = payload.get("payload") or {}
        raw_status = str(status_payload.get("status", "")).upper()

        # Map WAHA statuses to our DB values
        STATUS_MAP = {
            "WORKING": "WORKING",
            "CONNECTED": "WORKING",
            "AUTHENTICATED": "WORKING",
            "STOPPED": "STOPPED",
            "FAILED": "FAILED",
            "STARTING": "STARTING",
            "SCAN_QR_CODE": "SCAN_QR_CODE",
            "DISCONNECTED": "STOPPED",
        }
        db_status = STATUS_MAP.get(raw_status)
        if not db_status:
            return

        phone = db.query(Phone).filter(Phone.session_name == session_name).first()
        if not phone:
            return

        # Always keep chat/message data — hiding it is handled in the frontend by status check.
        # Permanently deleting on every WAHA restart or network hiccup would be destructive.
        phone.waha_status = db_status
        db.commit()
        logger.info("Session status: session=%s raw=%s db=%s phone_id=%d",
                    session_name, raw_status, db_status, phone.id)

        from app.core.ws_manager import ws_manager
        # Notify frontend so it can update the UI immediately
        await ws_manager.broadcast("phone_status_changed", {
            "phone_id": phone.id, "status": db_status,
        })
        # When disconnected, broadcast data_cleared so frontend hides the chat list
        if db_status in ("STOPPED", "FAILED"):
            await ws_manager.broadcast("data_cleared", {"phone_id": phone.id, "reason": db_status})

    except Exception as exc:
        logger.exception("session.status error: %s", exc)
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
        # Full payloads only in debug mode — they contain customer message content
        logger.debug("WAHA WEBHOOK BODY: %s", json.dumps(body))
    except Exception as exc:
        logger.exception("Failed to parse or log webhook JSON: %s", exc)
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
