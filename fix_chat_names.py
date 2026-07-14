"""Repair chats stored with raw phone number as name.

Looks up the MySQL contacts table and patches any MongoDB chat where
name == the raw number and a matching Contact.name exists.

    python fix_chat_names.py
"""
import asyncio
import logging

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)


async def fix() -> None:
    import sys, os
    sys.path.insert(0, os.path.dirname(__file__))

    from app.db.session import SessionLocal
    from app.db.mongo import get_mongo_db
    from app.models.contact import Contact

    db = SessionLocal()
    mongo = get_mongo_db()

    try:
        # Load all MySQL contacts into a dict keyed by phone number
        contacts = {c.phone_number: c.name for c in db.query(Contact).all() if c.name}
        logger.info("Loaded %d contacts from MySQL", len(contacts))

        # Find chats whose name looks like a raw number (no letters, no spaces)
        cursor = mongo.chats.find({"is_group": False}, {"id": 1, "name": 1, "chat_wid": 1})
        chats = await cursor.to_list(length=100_000)
        logger.info("Checking %d non-group chats", len(chats))

        updated = 0
        for chat in chats:
            current = chat.get("name") or ""
            number = (chat.get("chat_wid") or "").split("@")[0]
            # Name is "raw" if it equals the number or the full WID
            if current != number and current != chat.get("chat_wid"):
                continue
            contact_name = contacts.get(number, "")
            if contact_name:
                await mongo.chats.update_one({"id": chat["id"]}, {"$set": {"name": contact_name}})
                logger.info("  Chat %d: '%s' → '%s'", chat["id"], current, contact_name)
                updated += 1

        logger.info("Updated %d chat names", updated)
    finally:
        db.close()


if __name__ == "__main__":
    asyncio.run(fix())
