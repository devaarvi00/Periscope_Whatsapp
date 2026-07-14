"""One-time migration: copy existing MySQL chats and messages into MongoDB.

Run ONCE after deploying the MongoDB migration code:
    python migrate_to_mongo.py

Safe to re-run — upsert logic prevents duplicates.
"""
import asyncio
import logging

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)


async def migrate() -> None:
    from app.db.session import SessionLocal
    from app.db.mongo import init_mongo_indexes
    from app.services.mongo_chat_service import MongoInboxService

    await init_mongo_indexes()

    db = SessionLocal()
    inbox = MongoInboxService()

    try:
        # Import old MySQL models directly (they still exist during migration)
        from app.models.chat import Chat, ChatLabel
        from app.models.message import Message

        total_chats = db.query(Chat).count()
        logger.info("Migrating %d chats...", total_chats)

        offset = 0
        batch = 200
        migrated_chats = 0

        while True:
            chats = db.query(Chat).offset(offset).limit(batch).all()
            if not chats:
                break
            for c in chats:
                # Get label ids from MySQL chat_labels join table
                label_ids = [row.label_id for row in db.query(ChatLabel).filter(ChatLabel.chat_id == c.id).all()]

                # Build a MongoDB document using the existing integer id
                doc = {
                    "id": c.id,
                    "phone_id": c.phone_id,
                    "chat_wid": c.chat_wid,
                    "name": c.name or "",
                    "is_group": bool(c.is_group),
                    "last_message": c.last_message or "",
                    "last_message_at": c.last_message_at,
                    "unread_count": c.unread_count or 0,
                    "is_flagged": bool(c.is_flagged),
                    "is_archived": bool(c.is_archived),
                    "is_pinned": bool(getattr(c, "is_pinned", False)),
                    "ai_active": bool(getattr(c, "ai_active", False)),
                    "ai_state": getattr(c, "ai_state", "INACTIVE") or "INACTIVE",
                    "ai_snoozed_at": getattr(c, "ai_snoozed_at", None),
                    "assigned_to": c.assigned_to,
                    "label_ids": label_ids,
                    "custom_properties": getattr(c, "custom_properties", None) or {},
                    "created_at": c.created_at,
                    "updated_at": c.updated_at if hasattr(c, "updated_at") and c.updated_at else c.created_at,
                }
                try:
                    await inbox.db.chats.update_one(
                        {"id": c.id},
                        {"$setOnInsert": doc},
                        upsert=True,
                    )
                    migrated_chats += 1
                except Exception as exc:
                    logger.warning("Failed to migrate chat %d: %s", c.id, exc)

            offset += batch
            logger.info("  Chats: %d/%d migrated", migrated_chats, total_chats)

        logger.info("Chat migration complete: %d chats", migrated_chats)

        # Ensure the counters collection has the right starting value
        max_chat_id = db.query(Chat).order_by(Chat.id.desc()).first()
        if max_chat_id:
            await inbox.db.counters.update_one(
                {"_id": "chats"},
                {"$max": {"seq": max_chat_id.id}},
                upsert=True,
            )
            logger.info("Chat counter set to %d", max_chat_id.id)

        # ── Messages ──────────────────────────────────────────────────────────

        total_msgs = db.query(Message).count()
        logger.info("Migrating %d messages...", total_msgs)

        offset = 0
        migrated_msgs = 0

        while True:
            messages = db.query(Message).offset(offset).limit(500).all()
            if not messages:
                break
            for m in messages:
                doc = {
                    "id": m.id,
                    "phone_id": m.phone_id,
                    "chat_id": m.chat_id,
                    "chat_wid": getattr(m, "chat_wid", "") or "",
                    "message_wid": m.message_wid,
                    "from_me": bool(m.from_me),
                    "sender_name": m.sender_name or "",
                    "sender_number": m.sender_number or "",
                    "sent_by_agent_id": getattr(m, "sent_by_agent_id", None),
                    "body": m.body or "",
                    "message_type": m.message_type or "text",
                    "has_media": bool(getattr(m, "has_media", False)),
                    "media_url": getattr(m, "media_url", None),
                    "is_read": bool(getattr(m, "is_read", True)),
                    "is_flagged": bool(getattr(m, "is_flagged", False)),
                    "timestamp": m.timestamp,
                    "created_at": m.created_at if hasattr(m, "created_at") and m.created_at else m.timestamp,
                }
                try:
                    await inbox.db.messages.update_one(
                        {"message_wid": m.message_wid},
                        {"$setOnInsert": doc},
                        upsert=True,
                    )
                    migrated_msgs += 1
                except Exception as exc:
                    logger.warning("Failed to migrate message %d: %s", m.id, exc)

            offset += 500
            logger.info("  Messages: %d/%d migrated", migrated_msgs, total_msgs)

        logger.info("Message migration complete: %d messages", migrated_msgs)

        # Ensure message counter is up to date
        max_msg = db.query(Message).order_by(Message.id.desc()).first()
        if max_msg:
            await inbox.db.counters.update_one(
                {"_id": "messages"},
                {"$max": {"seq": max_msg.id}},
                upsert=True,
            )
            logger.info("Message counter set to %d", max_msg.id)

    finally:
        db.close()

    logger.info("Migration complete!")


if __name__ == "__main__":
    import sys
    import os
    sys.path.insert(0, os.path.dirname(__file__))
    asyncio.run(migrate())
