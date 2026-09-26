"""Public developer API — authenticated with an API key (X-API-Key header).

Mirrors Hyperscope's "Custom APIs for WhatsApp groups, chats and numbers":
send messages programmatically, list chats/messages, create tickets.
"""
import hashlib
import logging
import time
from datetime import datetime

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from fastapi.routing import APIRoute
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.models.api_key import ApiKey
from app.models.phone import Phone
from app.services.mongo_chat_service import MongoInboxService
from app.services.waha_service import WAHAService

logger = logging.getLogger(__name__)


class LoggedRoute(APIRoute):
    """Writes one Logs → API logs row per request on this router: method,
    route path, status code, duration and the key's name. Never the key,
    headers, query string or body."""

    def get_route_handler(self):
        handler = super().get_route_handler()
        route_path = self.path_format

        async def logged(request: Request):
            started = time.perf_counter()
            status_code = 500
            try:
                response = await handler(request)
                status_code = response.status_code
                return response
            except HTTPException as exc:
                status_code = exc.status_code
                raise
            except Exception as exc:  # RequestValidationError & co.
                status_code = getattr(exc, "status_code", None) or (422 if type(exc).__name__ == "RequestValidationError" else 500)
                raise
            finally:
                await _log_request(request, route_path, status_code,
                                   int((time.perf_counter() - started) * 1000))

        return logged


async def _log_request(request: Request, route_path: str, status_code: int, ms: int) -> None:
    from starlette.concurrency import run_in_threadpool

    from app.services import operation_log as oplog
    key_id = getattr(request.state, "api_key_id", None)
    key_name = getattr(request.state, "api_key_name", None)
    ok = status_code < 400
    details = {
        "method": request.method,
        "path": route_path,
        "status_code": status_code,
        "duration_ms": ms,
    }
    if key_id is not None:
        details.update(api_key_id=key_id, api_key_name=key_name)
    try:
        await run_in_threadpool(
            oplog.record, "api", f"{request.method} {route_path}",
            success=1 if ok else 0, failed=0 if ok else 1,
            status_code=status_code, duration_ms=ms,
            performed_by=f"API key: {key_name}" if key_name else "API key (invalid)",
            details=details,
        )
    except Exception:  # pragma: no cover — record() already swallows
        pass


router = APIRouter(prefix="/public/v1", tags=["public-api"], route_class=LoggedRoute)


def require_api_key(
    request: Request,
    x_api_key: str | None = Header(None),
    db: Session = Depends(get_db),
) -> ApiKey:
    if not x_api_key:
        raise HTTPException(401, "Missing X-API-Key header")
    key_hash = hashlib.sha256(x_api_key.encode()).hexdigest()
    key = db.query(ApiKey).filter(
        ApiKey.key_hash == key_hash, ApiKey.is_active == True
    ).first()
    if not key:
        raise HTTPException(401, "Invalid API key")
    key.last_used_at = datetime.utcnow()
    db.commit()
    request.state.api_key_id = key.id
    request.state.api_key_name = key.name
    return key


class PublicSendRequest(BaseModel):
    chat_id: str | None = None      # WID like 9198xxxx@c.us or a group @g.us
    phone_number: str | None = None  # plain number; converted to @c.us
    message: str


@router.post("/messages/send")
async def public_send_message(
    req: PublicSendRequest,
    db: Session = Depends(get_db),
    api_key: ApiKey = Depends(require_api_key),
):
    target = req.chat_id or (f"{req.phone_number}@c.us" if req.phone_number else None)
    if not target:
        raise HTTPException(400, "chat_id or phone_number is required")
    if not req.message.strip():
        raise HTTPException(400, "message is empty")

    inbox = MongoInboxService()
    # Try to find the chat in MongoDB by WID
    docs = await inbox.db.chats.find({"chat_wid": target}).to_list(1)
    chat = docs[0] if docs else None

    phone = None
    if chat:
        phone = db.query(Phone).filter(Phone.id == chat["phone_id"]).first()
    if not phone:
        phone = db.query(Phone).filter(Phone.is_active == True).first()
    if not phone:
        raise HTTPException(503, "No active WhatsApp number connected")

    waha = WAHAService.from_phone(phone)
    try:
        result = await waha.send_text(target, req.message)
    except Exception as exc:
        logger.exception("Public API send failed: %s", exc)
        raise HTTPException(502, "Failed to send message via WhatsApp")

    if chat:
        try:
            await inbox.upsert_message({
                "chat_id": chat["id"],
                "chat_wid": chat["chat_wid"],
                "phone_id": phone.id,
                "message_wid": result.message_id or f"api_{datetime.utcnow().timestamp()}",
                "from_me": True,
                "sender_name": f"API ({api_key.name})",
                "sender_number": phone.phone_number,
                "body": req.message,
                "message_type": "text",
                "timestamp": datetime.utcnow(),
            })
        except Exception:
            pass

    from app.services.webhook_dispatcher import dispatch_event
    await dispatch_event("message.sent", {
        "chat_wid": target, "body": req.message, "via": "public_api",
    })
    return {"ok": True, "message_id": result.message_id, "chat_id": target}


@router.get("/chats")
async def public_list_chats(
    is_group: bool | None = None,
    limit: int = 50,
    offset: int = 0,
    _key: ApiKey = Depends(require_api_key),
):
    inbox = MongoInboxService()
    chats = await inbox.list_chats(is_group=is_group, limit=min(limit, 200), offset=offset)
    return [
        {
            "id": c["id"], "chat_id": c["chat_wid"], "name": c.get("name") or "",
            "is_group": bool(c.get("is_group")), "unread_count": c.get("unread_count") or 0,
            "last_message": c.get("last_message") or "",
            "last_message_at": c["last_message_at"].isoformat() if isinstance(c.get("last_message_at"), datetime) else c.get("last_message_at"),
        }
        for c in chats
    ]


@router.get("/chats/{chat_wid}/messages")
async def public_get_messages(
    chat_wid: str,
    limit: int = 50,
    _key: ApiKey = Depends(require_api_key),
):
    inbox = MongoInboxService()
    docs = await inbox.db.chats.find({"chat_wid": chat_wid}).to_list(1)
    if not docs:
        raise HTTPException(404, "Chat not found")
    chat = docs[0]
    msgs = await inbox.get_messages(chat_id=chat["id"], limit=min(limit, 200))
    return [
        {
            "id": m.get("message_wid") or m["id"], "from_me": bool(m.get("from_me")),
            "sender_name": m.get("sender_name") or "", "sender_number": m.get("sender_number") or "",
            "body": m.get("body") or "", "type": m.get("message_type") or "text",
            "timestamp": m["timestamp"].isoformat() if isinstance(m.get("timestamp"), datetime) else (m.get("timestamp") or ""),
        }
        for m in msgs
    ]


class PublicTicketRequest(BaseModel):
    chat_id: str
    title: str
    description: str = ""
    priority: str = "medium"


@router.post("/tickets", status_code=201)
async def public_create_ticket(
    req: PublicTicketRequest,
    db: Session = Depends(get_db),
    api_key: ApiKey = Depends(require_api_key),
):
    inbox = MongoInboxService()
    docs = await inbox.db.chats.find({"chat_wid": req.chat_id}).to_list(1)
    if not docs:
        raise HTTPException(404, "Chat not found")
    chat = docs[0]
    from app.services.ticket_service import TicketService
    from app.models.ticket import TicketPriority
    try:
        priority = TicketPriority(req.priority.lower())
    except ValueError:
        priority = TicketPriority.MEDIUM
    ticket = TicketService(db).create_ticket(
        chat_id=chat["id"], title=req.title[:500],
        description=req.description, priority=priority,
    )
    return {"id": ticket.id, "title": ticket.title, "status": "open"}


@router.get("/numbers")
def public_list_numbers(
    db: Session = Depends(get_db),
    _key: ApiKey = Depends(require_api_key),
):
    phones = db.query(Phone).filter(Phone.is_active == True).all()
    return [
        {"id": p.id, "name": p.name, "phone_number": p.phone_number,
         "status": p.waha_status}
        for p in phones
    ]
