import logging

from app.db.session import engine
from app.models.base import Base
import app.models  # noqa: F401 — registers all models with Base

logger = logging.getLogger(__name__)


def init_db() -> None:
    logger.info("Creating database tables if they don't exist...")
    Base.metadata.create_all(bind=engine)
    logger.info("Database tables ready.")
