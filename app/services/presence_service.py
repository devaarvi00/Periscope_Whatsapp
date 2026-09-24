"""Agent presence spans (agent_sessions) for the "User uptime" metric.

ws_manager calls ``span_opened`` when an agent's first socket connects and
``span_closed`` when the last one goes away. DB writes run on a single worker
thread so they never block the event loop and always apply in call order
(an open is never overtaken by its own close).
"""
from __future__ import annotations

import asyncio
import logging
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime

from sqlalchemy import func, update

from app.db.session import SessionLocal
from app.models.agent_session import AgentSession

logger = logging.getLogger(__name__)

HEARTBEAT_SECONDS = 60
# A span whose last heartbeat is older than this is treated as dead.
STALE_SECONDS = HEARTBEAT_SECONDS * 3

_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="presence")
# agent_id -> agent_sessions.id of the span this process opened (worker thread only)
_open_rows: dict[int, int] = {}
_heartbeat_task: asyncio.Task | None = None


def _submit(fn, *args) -> None:
    try:
        _executor.submit(_safe, fn, *args)
    except RuntimeError:  # executor shut down (process exiting)
        pass


def _safe(fn, *args) -> None:
    try:
        fn(*args)
    except Exception as exc:
        logger.warning("Presence write failed (%s): %s", fn.__name__, exc)


def _open(agent_id: int, at: datetime) -> None:
    if agent_id in _open_rows:
        return
    db = SessionLocal()
    try:
        row = AgentSession(agent_id=agent_id, started_at=at, last_seen_at=at)
        db.add(row)
        db.commit()
        _open_rows[agent_id] = row.id
    finally:
        db.close()


def _close(agent_id: int, at: datetime) -> None:
    row_id = _open_rows.pop(agent_id, None)
    if row_id is None:
        return
    db = SessionLocal()
    try:
        db.execute(update(AgentSession).where(AgentSession.id == row_id)
                   .values(ended_at=at, last_seen_at=at))
        db.commit()
    finally:
        db.close()


def _beat(at: datetime) -> None:
    ids = list(_open_rows.values())
    if not ids:
        return
    db = SessionLocal()
    try:
        db.execute(update(AgentSession).where(AgentSession.id.in_(ids)).values(last_seen_at=at))
        db.commit()
    finally:
        db.close()


def _close_dangling() -> None:
    """Close spans left open by a previous run (crash / restart)."""
    db = SessionLocal()
    try:
        n = db.execute(
            update(AgentSession).where(AgentSession.ended_at.is_(None))
            .values(ended_at=func.coalesce(AgentSession.last_seen_at, AgentSession.started_at))
        ).rowcount
        db.commit()
        if n:
            logger.info("Closed %s dangling agent session(s)", n)
    finally:
        db.close()


def span_opened(agent_id: int) -> None:
    _submit(_open, int(agent_id), datetime.utcnow())


def span_closed(agent_id: int) -> None:
    _submit(_close, int(agent_id), datetime.utcnow())


async def _heartbeat_loop() -> None:
    while True:
        await asyncio.sleep(HEARTBEAT_SECONDS)
        _submit(_beat, datetime.utcnow())


async def start_presence_tracking() -> None:
    """Startup hook: close dangling spans, then start the heartbeat."""
    global _heartbeat_task
    await asyncio.get_running_loop().run_in_executor(_executor, _safe, _close_dangling)
    if _heartbeat_task is None or _heartbeat_task.done():
        _heartbeat_task = asyncio.create_task(_heartbeat_loop())
