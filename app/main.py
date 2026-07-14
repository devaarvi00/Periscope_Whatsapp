import asyncio
import json
import logging
from contextlib import asynccontextmanager
from pathlib import Path

import httpx
from fastapi import Depends, FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import text

from app.api.ai_agent import router as ai_router
from app.api.analytics import router as analytics_router
from app.api.auth import router as auth_router
from app.api.automation import router as automation_router
from app.api.bulk_messaging import router as bulk_router
from app.api.contacts import router as contacts_router
from app.api.inbox import router as inbox_router
from app.api.knowledge_base import router as kb_router
from app.api.developer import router as developer_router
from app.api.exports import router as exports_router
from app.api.groups import router as groups_router
from app.api.scheduled import router as scheduled_router
from app.api.properties import router as properties_router
from app.api.public_api import router as public_api_router
from app.api.tasks import router as tasks_router
from app.api.labels import router as labels_router
from app.api.logs import router as logs_router
from app.api.notes import router as notes_router
from app.api.phones import router as phones_router
from app.api.quick_replies import router as qr_router
from app.api.search import router as search_router
from app.api.tickets import router as tickets_router
from app.api.webhooks import router as webhooks_router
from app.core import http_client
from app.core.config import settings
from app.core.logging import configure_logging
from app.core.ws_manager import ws_manager
from app.db.init_db import init_db
from app.db.session import get_db

_FRONTEND = Path(__file__).parent.parent / "frontend"
logger = logging.getLogger(__name__)


async def _configure_waha_webhook() -> None:
    from app.services.waha_service import WAHAService
    from app.models.phone import Phone
    from app.db.session import SessionLocal

    db = SessionLocal()
    try:
        phones = db.query(Phone).filter(Phone.is_active == True).all()
        for phone in phones:
            waha = WAHAService.from_phone(phone)
            status = await waha.get_session_status()
            if status == "UNKNOWN":
                logger.info("Session %s not running in WAHA. Starting it...", phone.session_name)
                await waha.start_session()
                await asyncio.sleep(1.0)  # Give WAHA a moment to initialize the session
            
            ok = await waha.configure_webhook(settings.waha_webhook_url, settings.waha_webhook_secret)
            if ok:
                logger.info("WAHA webhook configured for session %s", phone.session_name)
            else:
                logger.warning("Failed to configure WAHA webhook for session %s", phone.session_name)
    except Exception as exc:
        logger.warning("WAHA webhook config failed: %s", exc)
    finally:
        db.close()


@asynccontextmanager
async def lifespan(_: FastAPI):
    configure_logging()
    init_db()
    await http_client.startup()
    await _configure_waha_webhook()

    from app.workers.scheduler import build_scheduler
    from app.workers.tasks import (
        check_no_reply_timeouts,
        check_sla_breaches,
        check_task_reminders,
        run_scheduled_bulk_jobs,
        run_scheduled_messages,
        sync_phone_statuses,
    )
    from apscheduler.triggers.interval import IntervalTrigger

    scheduler = build_scheduler()
    scheduler.add_job(sync_phone_statuses, IntervalTrigger(minutes=5), id="sync-phone-statuses")
    scheduler.add_job(check_sla_breaches, IntervalTrigger(minutes=10), id="check-sla")
    scheduler.add_job(run_scheduled_bulk_jobs, IntervalTrigger(minutes=1), id="bulk-jobs")
    scheduler.add_job(check_no_reply_timeouts, IntervalTrigger(minutes=5), id="no-reply-timeouts")
    scheduler.add_job(run_scheduled_messages, IntervalTrigger(minutes=1), id="scheduled-messages")
    scheduler.add_job(check_task_reminders, IntervalTrigger(minutes=1), id="task-reminders")
    scheduler.start()

    yield

    scheduler.shutdown(wait=False)
    await http_client.shutdown()


app = FastAPI(
    title=settings.app_name,
    version=settings.app_version,
    lifespan=lifespan,
    docs_url="/docs" if settings.environment != "production" else None,
    redoc_url=None,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.allowed_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

PREFIX = settings.api_prefix

from app.api.auth import get_current_agent  # noqa: E402

_auth = [Depends(get_current_agent)]

# Public routes: auth (login/register), WAHA webhooks, and the API-key-guarded
# developer API (it authenticates via X-API-Key instead of a JWT)
app.include_router(auth_router, prefix=PREFIX)
app.include_router(webhooks_router, prefix=PREFIX)
app.include_router(public_api_router, prefix=PREFIX)

# All other routes require a valid JWT
app.include_router(inbox_router, prefix=PREFIX, dependencies=_auth)
app.include_router(tickets_router, prefix=PREFIX, dependencies=_auth)
app.include_router(contacts_router, prefix=PREFIX, dependencies=_auth)
app.include_router(phones_router, prefix=PREFIX, dependencies=_auth)
app.include_router(labels_router, prefix=PREFIX, dependencies=_auth)
app.include_router(notes_router, prefix=PREFIX, dependencies=_auth)
app.include_router(qr_router, prefix=PREFIX, dependencies=_auth)
app.include_router(bulk_router, prefix=PREFIX, dependencies=_auth)
app.include_router(analytics_router, prefix=PREFIX, dependencies=_auth)
app.include_router(automation_router, prefix=PREFIX, dependencies=_auth)
app.include_router(ai_router, prefix=PREFIX, dependencies=_auth)
app.include_router(kb_router, prefix=PREFIX, dependencies=_auth)
app.include_router(search_router, prefix=PREFIX, dependencies=_auth)
app.include_router(logs_router, prefix=PREFIX, dependencies=_auth)
app.include_router(exports_router, prefix=PREFIX, dependencies=_auth)
app.include_router(developer_router, prefix=PREFIX, dependencies=_auth)
app.include_router(groups_router, prefix=PREFIX, dependencies=_auth)
app.include_router(scheduled_router, prefix=PREFIX, dependencies=_auth)
app.include_router(tasks_router, prefix=PREFIX, dependencies=_auth)
app.include_router(properties_router, prefix=PREFIX, dependencies=_auth)


@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket, token: str = ""):
    """
    Authenticated WebSocket endpoint.
    Client must pass ?token=<JWT> in the URL.
    Heartbeat: server sends {"type":"ping"} every 25 s; client must reply {"type":"pong"}.
    """
    from app.core.security import decode_access_token
    from app.db.session import SessionLocal
    from app.models.agent import Agent

    # ── Authenticate ──────────────────────────────────────────────── #
    payload = decode_access_token(token) if token else None
    if not payload:
        await websocket.close(code=4001, reason="Unauthorized")
        return

    try:
        agent_id = int(payload.get("sub", 0))
    except (TypeError, ValueError):
        await websocket.close(code=4001, reason="Invalid token")
        return

    db = SessionLocal()
    try:
        agent = db.query(Agent).filter(Agent.id == agent_id, Agent.is_active == True).first()
    finally:
        db.close()

    if not agent:
        await websocket.close(code=4001, reason="Agent not found")
        return

    await ws_manager.connect(websocket, agent_id)

    # ── Message loop with heartbeat ───────────────────────────────── #
    PING_INTERVAL = 25  # seconds
    try:
        while True:
            try:
                raw = await asyncio.wait_for(websocket.receive_text(), timeout=PING_INTERVAL)
                msg = json.loads(raw)
                mtype = msg.get("type", "")

                if mtype == "pong":
                    pass  # heartbeat acknowledged
                elif mtype == "ping":
                    await websocket.send_text(json.dumps({"type": "pong"}))
                # Future: handle subscribe / unsubscribe messages here

            except asyncio.TimeoutError:
                # No message received — send ping to check if client is alive
                sent = await ws_manager._send(websocket, {"type": "ping"})
                if not sent:
                    break

    except (WebSocketDisconnect, Exception):
        pass
    finally:
        ws_manager.disconnect(websocket, agent_id)


@app.get("/health")
async def health():
    db_gen = get_db()
    try:
        db = next(db_gen)
        db.execute(text("SELECT 1"))
        return {"status": "ok", "app": settings.app_name}
    except Exception:
        from fastapi import HTTPException
        raise HTTPException(503, "Database unavailable")
    finally:
        try:
            next(db_gen)
        except StopIteration:
            pass


if _FRONTEND.is_dir():
    app.mount("/static", StaticFiles(directory=str(_FRONTEND)), name="static")


@app.get("/", include_in_schema=False)
async def serve_frontend():
    index = _FRONTEND / "index.html"
    if index.exists():
        return FileResponse(str(index))
    return {"message": "Hyperscope WhatsApp CRM API", "docs": "/docs"}


@app.get("/{path:path}", include_in_schema=False)
async def spa_fallback(path: str):
    index = _FRONTEND / "index.html"
    if index.exists() and not path.startswith("api/"):
        return FileResponse(str(index))
    from fastapi import HTTPException
    raise HTTPException(404, "Not found")
