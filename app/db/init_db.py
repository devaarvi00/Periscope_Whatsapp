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


def _drop_stale_foreign_keys() -> None:
    """Drop FK constraints the database still enforces but the models no longer declare.

    Chats and messages moved from MySQL tables to MongoDB documents a while back.
    `notes.chat_id`, `tasks.chat_id`/`message_id`, `tickets.chat_id`/`message_id`,
    `scheduled_messages.chat_id` and `bulk_message_logs.chat_id` are now plain
    integers referencing Mongo ids — the models intentionally define no
    ForeignKey for them. But a database that was ever `create_all()`'d before
    those legacy `chats`/`messages` tables were dropped from the models still
    physically enforces the old constraint, since neither create_all nor
    _sync_missing_columns ever alters or drops existing constraints. The result:
    inserting a note/task/ticket/scheduled message on a real Mongo chat id that
    doesn't *also* happen to exist as a row in the orphaned legacy table fails
    with IntegrityError 1452 ("foreign key constraint fails").

    This removes exactly the constraints the current model set no longer wants,
    on tables the current models still manage — it never touches the legacy
    chats/messages/chat_labels tables themselves (they aren't in Base.metadata
    any more, so this loop never reaches them) and never drops a constraint a
    model still declares. Safe to run on every startup; a no-op once cleaned up.
    """
    insp = inspect(engine)
    for table in Base.metadata.sorted_tables:
        if not insp.has_table(table.name):
            continue
        try:
            live_fks = insp.get_foreign_keys(table.name)
        except Exception as exc:
            logger.warning("Could not inspect foreign keys on %s: %s", table.name, exc)
            continue
        if not live_fks:
            continue
        model_fk_columns = {fk.parent.name for fk in table.foreign_keys}
        for fk in live_fks:
            cols = fk.get("constrained_columns") or []
            name = fk.get("name")
            if not name or len(cols) != 1 or cols[0] in model_fk_columns:
                continue  # composite/unnamed FK, or one the model still declares
            try:
                with engine.begin() as conn:
                    conn.execute(text(f"ALTER TABLE `{table.name}` DROP FOREIGN KEY `{name}`"))
                logger.info(
                    "Dropped stale foreign key %s.%s -> %s (model no longer references it)",
                    table.name, cols[0], fk.get("referred_table"),
                )
            except Exception as exc:
                logger.warning("Could not drop stale foreign key %s.%s: %s", table.name, name, exc)


def init_db() -> None:
    logger.info("Creating database tables if they don't exist...")
    Base.metadata.create_all(bind=engine)
    _sync_missing_columns()
    _drop_stale_foreign_keys()
    logger.info("Database tables ready.")
