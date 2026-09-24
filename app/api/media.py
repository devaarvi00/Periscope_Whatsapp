"""Media library: every image / video / document / audio message across the
chats an agent can access, plus an authenticated proxy for the files
themselves (WAHA serves them behind its API key)."""
import logging
import re

from fastapi import APIRouter, Depends, HTTPException, Response
from sqlalchemy.orm import Session

from app.api.auth import get_current_agent
from app.core.permissions import allowed_phone_ids
from app.db.session import get_db
from app.models.agent import Agent
from app.models.phone import Phone
from app.services.access import assert_phone_access, agent_can_access_phone, get_accessible_chat
from app.services.mongo_chat_service import MongoInboxService, _serialize_message
from app.services.waha_service import WAHAService

router = APIRouter(prefix="/media", tags=["media"])
logger = logging.getLogger(__name__)

# Content types safe to render inline from our origin. Anything else (HTML,
# SVG, scripts…) is served as an opaque download so it can never execute.
_INLINE_TYPES = re.compile(r"^(image/(png|jpe?g|gif|webp|bmp|heic|heif)|video/[\w.+-]+|audio/[\w.+-]+|application/pdf)$")


@router.get("")
async def list_media(
    type: str | None = None,          # image | video | document | audio
    phone_id: int | None = None,
    chat_id: int | None = None,
    search: str | None = None,        # chat name
    before: int | None = None,        # message id cursor (from next_before)
    limit: int = 60,
    db: Session = Depends(get_db),
    agent: Agent = Depends(get_current_agent),
):
    inbox = MongoInboxService()
    if type and type not in inbox.MEDIA_KINDS:
        raise HTTPException(400, "type must be one of: " + ", ".join(inbox.MEDIA_KINDS))
    if phone_id is not None:
        assert_phone_access(db, agent, phone_id)
    phone_ids = allowed_phone_ids(db, agent)
    limit = max(1, min(limit, 200))

    chat_ids: list[int] | None = None
    if chat_id is not None:
        await get_accessible_chat(db, agent, chat_id)
        chat_ids = [chat_id]
    elif search and search.strip():
        filt: dict = {"name": {"$regex": re.escape(search.strip()), "$options": "i"}}
        if phone_id is not None:
            filt["phone_id"] = phone_id
        elif phone_ids is not None:
            filt["phone_id"] = {"$in": phone_ids}
        chat_ids = [int(i) for i in await inbox.db.chats.distinct("id", filt)][:1000]
        if not chat_ids:
            return {"items": [], "next_before": None}

    docs = await inbox.list_media(
        phone_ids=phone_ids, phone_id=phone_id, chat_ids=chat_ids,
        kind=type, before_id=before, limit=limit,
    )
    ids = list({d.get("chat_id") for d in docs if d.get("chat_id") is not None})
    chats = {
        c["id"]: c for c in await inbox.db.chats.find(
            {"id": {"$in": ids}}, {"id": 1, "name": 1, "chat_wid": 1, "is_group": 1}
        ).to_list(length=len(ids) or 1)
    }
    items = []
    for d in docs:
        item = _serialize_message(d)
        c = chats.get(d.get("chat_id")) or {}
        item["chat_name"] = c.get("name") or ""
        item["chat_wid"] = c.get("chat_wid") or item.get("chat_wid") or ""
        item["chat_is_group"] = bool(c.get("is_group"))
        items.append(item)
    return {
        "items": items,
        "next_before": docs[-1]["id"] if len(docs) == limit else None,
    }


@router.get("/{message_id}/file")
async def media_file(
    message_id: int,
    db: Session = Depends(get_db),
    agent: Agent = Depends(get_current_agent),
):
    """Stream a message's media from WAHA. Media from history syncs (stored
    without a URL) is downloaded from WAHA on first request and remembered."""
    inbox = MongoInboxService()
    msg = await inbox.db.messages.find_one({"id": message_id})
    if not msg or not agent_can_access_phone(db, agent, msg.get("phone_id")):
        raise HTTPException(404, "Media not found")
    phone = db.query(Phone).filter(Phone.id == msg["phone_id"]).first()
    if not phone:
        raise HTTPException(404, "Media not found")
    waha = WAHAService.from_phone(phone)

    media_url = msg.get("media_url") or ""
    path = waha.files_path(media_url)
    if not path and not media_url and msg.get("has_media") and phone.waha_status == "WORKING":
        wa = await waha.get_message(msg.get("chat_wid") or "", msg.get("message_wid") or "")
        media = wa.get("media") if isinstance(wa.get("media"), dict) else {}
        media_url = media.get("url") or wa.get("mediaUrl") or ""
        path = waha.files_path(media_url)
        if path:
            await inbox.set_message_media(
                message_id, media_url=media_url,
                media_mimetype=str(media.get("mimetype") or "")[:100] or None,
                media_filename=str(media.get("filename") or "")[:255] or None,
            )
    if not path:
        raise HTTPException(404, "Media file not available")

    got = await waha.fetch_file(path)
    if not got:
        raise HTTPException(502, "Could not fetch media from WhatsApp")
    content, ctype = got
    ctype = (ctype or "").split(";")[0].strip().lower()
    inline = bool(_INLINE_TYPES.match(ctype))
    filename = re.sub(r'[^\w.\- ]', "_", msg.get("media_filename") or path.rsplit("/", 1)[-1])[:120]
    return Response(
        content=content,
        media_type=ctype if inline else "application/octet-stream",
        headers={
            "Content-Disposition": f'{"inline" if inline else "attachment"}; filename="{filename}"',
            "Cache-Control": "private, max-age=3600",
            "X-Content-Type-Options": "nosniff",
            "Content-Security-Policy": "sandbox; default-src 'none'",
        },
    )
