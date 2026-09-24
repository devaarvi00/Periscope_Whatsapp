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


def _index_name(keys: list, kwargs: dict) -> str:
    """Name MongoDB auto-generates for an index: "field1_dir1_field2_dir2"."""
    return kwargs.get("name") or "_".join(f"{k}_{v}" for k, v in keys)


async def _ensure_index(collection, keys: list, **kwargs) -> None:
    """Create an index without ever crashing startup.

    - IndexOptionsConflict (85) / IndexKeySpecsConflict (86): an index with the
      same name/keys but different options exists (e.g. missing unique=True
      after a migration) — drop it and recreate with the correct options.
    - DuplicateKey (11000) while building a unique index: existing data
      violates it — log and continue without the index rather than crash.
    """
    from pymongo.errors import DuplicateKeyError, OperationFailure
    index_name = _index_name(keys, kwargs)
    try:
        await collection.create_index(keys, **kwargs)
        return
    except DuplicateKeyError as exc:
        logger.error("Cannot build unique index '%s' on %s — duplicate data: %s",
                     index_name, collection.name, exc)
        return
    except OperationFailure as exc:
        if exc.code == 11000:
            logger.error("Cannot build unique index '%s' on %s — duplicate data: %s",
                         index_name, collection.name, exc)
            return
        if exc.code not in (85, 86):
            logger.error("Could not create index '%s' on %s: %s", index_name, collection.name, exc)
            return
    logger.warning(
        "Index '%s' on %s has conflicting options — dropping and recreating",
        index_name, collection.name,
    )
    try:
        await collection.drop_index(index_name)
    except Exception as drop_exc:
        logger.warning("Could not drop index '%s': %s", index_name, drop_exc)
    try:
        await collection.create_index(keys, **kwargs)
    except Exception as exc:
        logger.error("Could not recreate index '%s' on %s: %s", index_name, collection.name, exc)


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

    # messages — WAHA message IDs are only unique per session/phone, so dedup
    # is on (phone_id, message_wid). The old global *unique* index on
    # message_wid alone conflicts with the non-unique spec below, so
    # _ensure_index drops and recreates it without the unique constraint.
    await _ensure_index(db.messages, [("phone_id", 1), ("message_wid", 1)], unique=True)
    await _ensure_index(db.messages, [("message_wid", 1)])
    await _ensure_index(db.messages, [("id", 1)], unique=True)
    await _ensure_index(db.messages, [("chat_id", 1), ("timestamp", -1), ("id", -1)])
    await _ensure_index(db.messages, [("chat_wid", 1), ("phone_id", 1), ("timestamp", -1)])
    await _ensure_index(db.messages, [("phone_id", 1), ("chat_wid", 1), ("timestamp", -1)])

    # media library: newest media per number
    await _ensure_index(db.messages, [("phone_id", 1), ("message_type", 1), ("timestamp", -1), ("id", -1)])

    # group_events — participant joins/adds/leaves/removes from WAHA webhooks
    await _ensure_index(db.group_events, [("chat_id", 1), ("timestamp", 1)])
    await _ensure_index(db.group_events, [("phone_id", 1), ("timestamp", 1)])
    await _ensure_index(db.group_events, [("type", 1), ("timestamp", 1)])
    await _ensure_index(
        db.group_events, [("source_event_id", 1)], unique=True,
        name="source_event_id_unique",
        partialFilterExpression={"source_event_id": {"$type": "string"}},
    )

    # message_reactions — one live reaction per (message, reactor)
    await _ensure_index(db.message_reactions, [("phone_id", 1), ("message_wid", 1), ("reactor", 1)], unique=True)
    await _ensure_index(db.message_reactions, [("chat_id", 1), ("timestamp", 1)])
    await _ensure_index(db.message_reactions, [("phone_id", 1), ("timestamp", 1)])

    logger.info("MongoDB indexes ensured")
