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


async def _ensure_index(collection, keys: list, **kwargs) -> None:
    """Create an index, handling the case where an existing index has the same
    key pattern but different options (e.g. missing unique=True after a migration).
    MongoDB raises OperationFailure code 86 (IndexKeySpecsConflict) in that case —
    we drop the old index by name and recreate with the correct options."""
    from pymongo.errors import OperationFailure
    try:
        await collection.create_index(keys, **kwargs)
    except OperationFailure as exc:
        if exc.code != 86:
            raise
        # Derive the auto-generated index name: "field1_dir1_field2_dir2_..."
        index_name = kwargs.get("name") or "_".join(f"{k}_{v}" for k, v in keys)
        logger.warning(
            "Index '%s' on %s has conflicting options — dropping and recreating",
            index_name, collection.name,
        )
        try:
            await collection.drop_index(index_name)
        except Exception as drop_exc:
            logger.warning("Could not drop index '%s': %s", index_name, drop_exc)
        await collection.create_index(keys, **kwargs)


async def init_mongo_indexes() -> None:
    """Create all required indexes — idempotent, safe to call on every startup."""
    db = get_mongo_db()

    # chats
    await _ensure_index(db.chats, [("phone_id", 1), ("chat_wid", 1)], unique=True)
    await _ensure_index(db.chats, [("id", 1)], unique=True)
    await _ensure_index(db.chats, [("phone_id", 1), ("last_message_at", -1)])
    await _ensure_index(db.chats, [("phone_id", 1), ("is_archived", 1), ("last_message_at", -1)])
    await _ensure_index(db.chats, [("phone_id", 1), ("is_flagged", 1)])
    await _ensure_index(db.chats, [("phone_id", 1), ("assigned_to", 1)])
    await _ensure_index(db.chats, [("phone_id", 1), ("label_ids", 1)])
    await _ensure_index(db.chats, [("phone_id", 1), ("name", 1)])

    # messages
    await _ensure_index(db.messages, [("message_wid", 1)], unique=True)
    await _ensure_index(db.messages, [("id", 1)], unique=True)
    await _ensure_index(db.messages, [("chat_wid", 1), ("phone_id", 1), ("timestamp", -1)])
    await _ensure_index(db.messages, [("phone_id", 1), ("chat_wid", 1), ("timestamp", -1)])

    logger.info("MongoDB indexes ensured")
