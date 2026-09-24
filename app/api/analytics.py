"""Analytics endpoints — one per Analytics sub-page.

Common query params: ``from`` / ``to`` (ISO 8601; default the last 24 h),
``tz`` (IANA zone for hour/day bucket boundaries), ``chat_id``,
``phone_ids`` and ``agent_ids`` (comma-separated ids). Results are always
limited to the phones the caller may access.
"""
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.api.auth import get_current_agent
from app.core.permissions import allowed_phone_ids
from app.core.ws_manager import ws_manager
from app.db.session import get_db
from app.models.agent import Agent
from app.services.access import get_accessible_chat
from app.services.analytics_service import AnalyticsService, Scope

router = APIRouter(prefix="/analytics", tags=["analytics"])

MAX_RANGE = timedelta(days=366)
HOURLY_UP_TO = timedelta(days=2)


def _parse_dt(value: str | None, name: str) -> datetime | None:
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError:
        raise HTTPException(422, f"Invalid '{name}' datetime")
    if dt.tzinfo is not None:
        dt = dt.astimezone(timezone.utc).replace(tzinfo=None)
    return dt


def _parse_ids(value: str | None, name: str) -> list[int] | None:
    if value is None or not value.strip():
        return None
    try:
        return sorted({int(v) for v in value.split(",") if v.strip()})
    except ValueError:
        raise HTTPException(422, f"'{name}' must be comma-separated integers")


def _resolve_tz(tz: str, offset_minutes: int | None) -> str:
    """IANA zone when this server knows it, else the client's fixed UTC offset ("+05:30").

    Browsers may report legacy names (e.g. Asia/Calcutta) missing from slim tzdata.
    """
    try:
        ZoneInfo(tz)
        return tz
    except (ZoneInfoNotFoundError, ValueError):
        pass
    if offset_minutes is None:
        return "UTC"
    sign = "+" if offset_minutes >= 0 else "-"
    h, m = divmod(abs(offset_minutes), 60)
    return f"{sign}{h:02d}:{m:02d}"


async def get_scope(
    from_: str | None = Query(None, alias="from"),
    to: str | None = None,
    tz: str = "UTC",
    tz_offset: int | None = Query(None, ge=-840, le=840),
    chat_id: int | None = None,
    phone_ids: str | None = None,
    agent_ids: str | None = None,
    db: Session = Depends(get_db),
    agent: Agent = Depends(get_current_agent),
) -> Scope:
    end = _parse_dt(to, "to") or datetime.utcnow()
    start = _parse_dt(from_, "from") or end - timedelta(days=1)
    if start >= end:
        raise HTTPException(422, "'from' must be before 'to'")
    if end - start > MAX_RANGE:
        raise HTTPException(422, "Date range can be at most 366 days")
    tz = _resolve_tz(tz, tz_offset)

    # Phone scope = requested ∩ allowed (None = every phone)
    allowed = allowed_phone_ids(db, agent)
    requested = _parse_ids(phone_ids, "phone_ids")
    if requested is None:
        phones = allowed
    elif allowed is None:
        phones = requested
    else:
        phones = [p for p in requested if p in set(allowed)]

    if chat_id is not None:
        await get_accessible_chat(db, agent, chat_id)  # 404 when not visible

    return Scope(
        frm=start, to=end, tz=tz,
        bucket="hour" if end - start <= HOURLY_UP_TO else "day",
        phone_ids=phones, chat_id=chat_id,
        agent_ids=_parse_ids(agent_ids, "agent_ids"),
    )


@router.get("/team")
async def team_analytics(scope: Scope = Depends(get_scope), db: Session = Depends(get_db)):
    return await AnalyticsService(db).team(scope, ws_manager.online_agent_ids())


@router.get("/phones")
async def phone_analytics(scope: Scope = Depends(get_scope), db: Session = Depends(get_db)):
    return await AnalyticsService(db).phones(scope)


@router.get("/chats")
async def chat_metrics(scope: Scope = Depends(get_scope), db: Session = Depends(get_db)):
    return await AnalyticsService(db).chats(scope)


@router.get("/tickets")
async def ticket_metrics(scope: Scope = Depends(get_scope), db: Session = Depends(get_db)):
    return await AnalyticsService(db).tickets(scope)


@router.get("/messages")
async def message_metrics(scope: Scope = Depends(get_scope), db: Session = Depends(get_db)):
    return await AnalyticsService(db).messages(scope)


@router.get("/members")
async def member_metrics(scope: Scope = Depends(get_scope), db: Session = Depends(get_db)):
    return await AnalyticsService(db).members(scope)


@router.get("/chat-options")
async def chat_options(
    q: str = "",
    db: Session = Depends(get_db),
    agent: Agent = Depends(get_current_agent),
):
    """Chats for the analytics chat picker (includes archived chats)."""
    return await AnalyticsService(db).chat_options(allowed_phone_ids(db, agent), q.strip()[:100])

