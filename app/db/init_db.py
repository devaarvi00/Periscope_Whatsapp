import logging

from sqlalchemy import inspect, text

from app.db.session import engine
from app.models.base import Base
import app.models  # noqa: F401 — registers all models with Base

logger = logging.getLogger(__name__)


def _sync_missing_columns() -> None:
    """Add columns that exist on models but not in the DB (lightweight migration).

    create_all only creates missing tables; this covers new columns added to
    existing tables so upgrades work without hand-written migrations.
    """
    insp = inspect(engine)
    for table in Base.metadata.sorted_tables:
        if not insp.has_table(table.name):
            continue
        existing = {c["name"] for c in insp.get_columns(table.name)}
        for col in table.columns:
            if col.name in existing:
                continue
            col_type = col.type.compile(engine.dialect)
            ddl = f"ALTER TABLE `{table.name}` ADD COLUMN `{col.name}` {col_type}"
            if col.nullable:
                ddl += " NULL"
            elif col.default is not None and getattr(col.default, "is_scalar", False):
                default = col.default.arg
                if isinstance(default, bool):
                    default = int(default)
                if isinstance(default, str):
                    ddl += f" NOT NULL DEFAULT '{default}'"
                else:
                    ddl += f" NOT NULL DEFAULT {default}"
            try:
                with engine.begin() as conn:
                    conn.execute(text(ddl))
                logger.info("Added column %s.%s", table.name, col.name)
            except Exception as exc:
                logger.warning("Could not add column %s.%s: %s", table.name, col.name, exc)


def _migrate_chat_wid_uniqueness() -> None:
    """Replace global unique(chat_wid) with per-phone unique(phone_id, chat_wid)."""
    insp = inspect(engine)
    if not insp.has_table("chats"):
        return

    # Skip if composite unique already present
    unique_constraints = insp.get_unique_constraints("chats")
    if any(set(uc["column_names"]) == {"phone_id", "chat_wid"} for uc in unique_constraints):
        return

    # Drop the old single-column unique index on chat_wid
    indexes = insp.get_indexes("chats")
    for idx in indexes:
        if idx["column_names"] == ["chat_wid"] and idx.get("unique"):
            try:
                with engine.begin() as conn:
                    conn.execute(text(f"ALTER TABLE `chats` DROP INDEX `{idx['name']}`"))
                logger.info("Dropped old unique index chats.%s", idx["name"])
            except Exception as exc:
                logger.warning("Could not drop old unique index: %s", exc)

    # Create composite unique index
    try:
        with engine.begin() as conn:
            conn.execute(text(
                "CREATE UNIQUE INDEX `uq_chats_phone_chat` ON `chats` (`phone_id`, `chat_wid`)"
            ))
        logger.info("Created composite unique index uq_chats_phone_chat")
    except Exception as exc:
        logger.warning("Could not create composite unique index: %s", exc)


def init_db() -> None:
    logger.info("Creating database tables if they don't exist...")
    Base.metadata.create_all(bind=engine)
    _sync_missing_columns()
    _migrate_chat_wid_uniqueness()
    logger.info("Database tables ready.")
