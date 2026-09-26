"""Business-timezone helpers.

All datetimes are stored naive-UTC. Wall-clock rules (AI operating hours,
"send on Mondays", "day 15 of the month") are defined in the business's
local timezone, configured with BUSINESS_TIMEZONE (default Asia/Kolkata).
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from functools import lru_cache
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from app.core.config import settings

logger = logging.getLogger(__name__)

DEFAULT_BUSINESS_TIMEZONE = "Asia/Kolkata"


@lru_cache(maxsize=1)
def business_tz() -> ZoneInfo:
    name = str(getattr(settings, "business_timezone", "") or DEFAULT_BUSINESS_TIMEZONE)
    try:
        return ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError):
        logger.warning("Unknown BUSINESS_TIMEZONE '%s' — falling back to %s", name, DEFAULT_BUSINESS_TIMEZONE)
        try:
            return ZoneInfo(DEFAULT_BUSINESS_TIMEZONE)
        except ZoneInfoNotFoundError:
            return ZoneInfo("UTC")


def business_now() -> datetime:
    """Current wall-clock time in the business timezone (aware)."""
    return datetime.now(timezone.utc).astimezone(business_tz())


def utc_naive_to_local(dt: datetime) -> datetime:
    """Naive-UTC → aware business-local."""
    return dt.replace(tzinfo=timezone.utc).astimezone(business_tz())


def local_to_utc_naive(dt: datetime) -> datetime:
    """Aware (or naive business-local) → naive UTC."""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=business_tz())
    return dt.astimezone(timezone.utc).replace(tzinfo=None)
