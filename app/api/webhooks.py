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

        chat_data = msg_data.get("chatId") or msg_data.get("from") or ""
        if isinstance(chat_data, dict):
            chat_wid = chat_data.get("_serialized") or chat_data.get("id", "")
        else:
            chat_wid = str(chat_data)

        if not chat_wid:
            return

        inbox = InboxService(db)
        chat = inbox.get_chat_by_wid(chat_wid)
        if not chat:
            is_group = chat_wid.endswith("@g.us")
            chat_name = (
                (msg_data.get("notifyName") or msg_data.get("_data", {}).get("notifyName"))
                if not is_group else chat_wid
            ) or chat_wid
            chat = inbox.upsert_chat({
                "chat_wid": chat_wid,
                "phone_id": phone.id,
                "name": str(chat_name),
                "is_group": is_group,
            })

        from_me = bool(msg_data.get("fromMe", False))
        body = msg_data.get("body") or msg_data.get("caption") or ""
        ts_raw = msg_data.get("timestamp")
        if isinstance(ts_raw, (int, float)):
            ts = datetime.utcfromtimestamp(ts_raw)
        else:
            ts = datetime.utcnow()

        msg_type = msg_data.get("type", "text")
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
            "has_media": msg_type not in ("text", "chat", ""),
            "timestamp": ts,
        })

        if not from_me:
            chat.unread_count = (chat.unread_count or 0) + 1
            db.commit()

        # Broadcast real-time update
        from app.core.ws_manager import ws_manager
        await ws_manager.emit_new_message(
            chat_id=chat.id,
            chat_wid=chat_wid,
            body=body,
            from_me=from_me,
            sender_name=sender_name or "",
            sender_number=sender_number or "",
            timestamp=ts,
            message_type=msg_type,
            has_media=msg_type not in ("text", "chat", ""),
        )

        # Run automation rules
        from app.services.automation_service import AutomationService
        automation = AutomationService(db)
        await automation.run_rules("message_received", {
            "chat_id": chat.id,
            "chat_wid": chat_wid,
            "message": body,
            "from_me": from_me,
            "is_group": chat.is_group,
        })

        # AI agent handling
        if not from_me and chat.ai_active and chat.ai_state != "SNOOZED":
            from app.services.ai_agent_service import AIAgentService
            from app.services.waha_service import WAHAService
            ai = AIAgentService(db)
            recent = [{"body": body, "from_me": from_me, "sender_name": sender_name}]
            reply = await ai.handle_incoming_message(chat, body, recent)
            if reply:
                waha = WAHAService(session_name=session)
                await waha.send_text(chat_wid, reply)
                inbox.upsert_message({
                    "chat_id": chat.id,
                    "phone_id": phone.id,
                    "message_wid": f"ai_{msg_wid}",
                    "from_me": True,
                    "sender_name": "AI Agent",
                    "sender_number": phone.phone_number,
                    "body": reply,
                    "message_type": "text",
                    "timestamp": datetime.utcnow(),
                })

    except Exception as exc:
        logger.exception("Webhook processing error: %s", exc)
    finally:
        db.close()


async def _process_session_status(payload: dict[str, Any]) -> None:
    """Clear all chat/message data when WAHA session logs out."""
    db = SessionLocal()
    try:
        session_name = payload.get("session", "")
        status_payload = payload.get("payload") or {}
        status = str(status_payload.get("status", "")).upper()

        # Only wipe on explicit logout/disconnect states
        if status not in ("STOPPED", "FAILED"):
            return

        phone = db.query(Phone).filter(Phone.session_name == session_name).first()
        if not phone:
            return

        chat_ids = [r[0] for r in db.query(Chat.id).filter(Chat.phone_id == phone.id).all()]
        if chat_ids:
            db.execute(delete(ChatLabel).where(ChatLabel.chat_id.in_(chat_ids)))
            db.execute(delete(Message).where(Message.phone_id == phone.id))
            db.execute(delete(Chat).where(Chat.phone_id == phone.id))
            db.commit()
            logger.info("Cleared %d chats for phone %d (session %s → %s)",
                        len(chat_ids), phone.id, session_name, status)

        from app.core.ws_manager import ws_manager
        await ws_manager.broadcast("data_cleared", {"phone_id": phone.id, "reason": status})

    except Exception as exc:
        logger.exception("session.status clear error: %s", exc)
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
        body = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON")

    event = body.get("event", "")
    logger.debug("WAHA event: %s", event)

    if event in ("message", "message.any", "message_create"):
        background.add_task(_process_message_event, body)
    elif event == "session.status":
        background.add_task(_process_session_status, body)

    return {"status": "ok"}
