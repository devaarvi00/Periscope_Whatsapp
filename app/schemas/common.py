"""Shared schema helpers.

Datetimes are stored naive-UTC throughout the database. Clients may send
timezone-aware ISO strings (e.g. "2026-09-24T10:00:00.000Z"); these helpers
convert them to naive UTC. Naive inputs are assumed to already be UTC.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Annotated

from pydantic import AfterValidator


def to_naive_utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is not None:
        return value.astimezone(timezone.utc).replace(tzinfo=None)
    return value


def parse_client_datetime(value: str | datetime | None) -> datetime | None:
    """Parse an ISO 8601 string (with optional 'Z' / offset) into naive UTC.

    Raises ValueError on malformed input; returns None for empty input.
    """
    if value is None:
        return None
    if isinstance(value, datetime):
        return to_naive_utc(value)
    text = str(value).strip()
    if not text:
        return None
    if text.endswith(("Z", "z")):
        text = text[:-1] + "+00:00"
    return to_naive_utc(datetime.fromisoformat(text))


# Use as a pydantic field type: `due_date: NaiveUTCDatetime | None = None`
NaiveUTCDatetime = Annotated[datetime, AfterValidator(to_naive_utc)]
