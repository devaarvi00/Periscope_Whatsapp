"""Repair empty-body messages already stored in MongoDB.

Fixes messages that were stored before the media-label fallback was in place.
Safe to re-run — only updates docs where body is currently "".

    python fix_empty_messages.py
"""
import asyncio
import logging

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

_MEDIA_LABELS = {
    "image": "📷 Photo", "photo": "📷 Photo",
    "video": "🎬 Video",
    "audio": "🎤 Voice message", "voice": "🎤 Voice message", "ptt": "🎤 Voice message",
    "document": "📄 Document", "pdf": "📄 Document",
    "sticker": "🖼 Sticker", "gif": "🎞 GIF",
    "location": "📍 Location",
    "contact": "👤 Contact", "vcard": "👤 Contact",
}
_SYSTEM_LABELS = {
    "revoke": "🗑 Message deleted",
    "call_log": "📞 Call",
    "e2e_notification": "🔒 Encrypted notification",
    "order": "🛒 Order",
    "product": "📦 Product",
    "list": "📋 List message",
    "list_response": "📋 List response",
    "buttons_response": "📋 Button response",
    "template_button_reply": "📋 Template reply",
    "interactive": "📋 Interactive message",
    "poll_creation": "📊 Poll",
    "poll_update": "📊 Poll response",
}


async def fix() -> None:
    import sys, os
    sys.path.insert(0, os.path.dirname(__file__))

    from app.db.mongo import get_mongo_db
    db = get_mongo_db()

    # Find all messages with empty body
    cursor = db.messages.find({"$or": [{"body": ""}, {"body": None}]}, {"id": 1, "message_type": 1, "has_media": 1})
    docs = await cursor.to_list(length=100_000)
    logger.info("Found %d messages with empty body", len(docs))

    updated = 0
    skipped = 0
    for doc in docs:
        msg_type = (doc.get("message_type") or "text").lower()
        has_media = bool(doc.get("has_media"))

        if has_media or msg_type in _MEDIA_LABELS:
            new_body = _MEDIA_LABELS.get(msg_type, "📎 Media")
        elif msg_type in _SYSTEM_LABELS:
            new_body = _SYSTEM_LABELS[msg_type]
        else:
            skipped += 1
            continue

        await db.messages.update_one({"id": doc["id"]}, {"$set": {"body": new_body}})
        updated += 1

    logger.info("Updated %d messages | Skipped %d (unknown type, left empty)", updated, skipped)


if __name__ == "__main__":
    asyncio.run(fix())
