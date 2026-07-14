"""MongoDB connection and collection helpers.

Chats and messages are stored here, keyed by phone_id so each WhatsApp
number has fully isolated data.  Auto-increment integer IDs are maintained
via a `counters` collection so the REST API surface stays unchanged
(clients still see integer IDs, not ObjectIds).
"""
from __future__ import annotations

import logging
from motor.motor_asyncio import AsyncIOMotorClient, AsyncIOMotorDatabase
from app.core.config import settings

logger = logging.getLogger(__name__)

_client: AsyncIOMotorClient | None = None


def _get_client() -> AsyncIOMotorClient:
    global _client
    if _client is None:
        _client = AsyncIOMotorClient(settings.mongodb_url)
    return _client


def get_mongo_db() -> AsyncIOMotorDatabase:
    return _get_client()[settings.mongodb_db_name]


async def next_id(name: str) -> int:
    """Return the next auto-increment integer for the given sequence name."""
    db = get_mongo_db()
    doc = await db.counters.find_one_and_update(
        {"_id": name},
        {"$inc": {"seq": 1}},
        upsert=True,
        return_document=True,
    )
    return doc["seq"]


async def init_mongo_indexes() -> None:
    """Create all required indexes — idempotent, safe to call on every startup."""
    db = get_mongo_db()

    # chats
    await db.chats.create_index([("phone_id", 1), ("chat_wid", 1)], unique=True)
    await db.chats.create_index([("id", 1)], unique=True)
    await db.chats.create_index([("phone_id", 1), ("last_message_at", -1)])
    await db.chats.create_index([("phone_id", 1), ("is_archived", 1), ("last_message_at", -1)])
    await db.chats.create_index([("phone_id", 1), ("is_flagged", 1)])
    await db.chats.create_index([("phone_id", 1), ("assigned_to", 1)])
    await db.chats.create_index([("phone_id", 1), ("label_ids", 1)])
    await db.chats.create_index([("phone_id", 1), ("name", 1)])

    # messages
    await db.messages.create_index([("message_wid", 1)], unique=True)
    await db.messages.create_index([("id", 1)], unique=True)
    await db.messages.create_index([("chat_wid", 1), ("phone_id", 1), ("timestamp", -1)])
    await db.messages.create_index([("phone_id", 1), ("chat_wid", 1), ("timestamp", -1)])

    logger.info("MongoDB indexes ensured")
