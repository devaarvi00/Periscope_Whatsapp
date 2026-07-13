import hashlib
import secrets as pysecrets

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.api.auth import get_current_agent
from app.db.session import get_db
from app.models.agent import Agent, AgentRole
from app.models.api_key import ApiKey, WebhookEndpoint
from app.services.activity_service import log_activity
from app.services.webhook_dispatcher import WEBHOOK_EVENTS, dispatch_event

router = APIRouter(prefix="/developer", tags=["developer"])


def _require_admin(agent: Agent) -> None:
    if agent.role != AgentRole.ADMIN:
        raise HTTPException(403, "Only admins can manage developer settings")


# ── API keys ──────────────────────────────────────────────────────────────────

class ApiKeyCreate(BaseModel):
    name: str


@router.get("/api-keys")
def list_api_keys(
    db: Session = Depends(get_db),
    agent: Agent = Depends(get_current_agent),
):
    _require_admin(agent)
    keys = db.query(ApiKey).order_by(ApiKey.created_at.desc()).all()
    return [
        {
            "id": k.id, "name": k.name, "key_prefix": k.key_prefix,
            "is_active": k.is_active,
            "last_used_at": k.last_used_at.isoformat() if k.last_used_at else None,
            "created_at": k.created_at.isoformat() if k.created_at else None,
        }
        for k in keys
    ]


@router.post("/api-keys", status_code=201)
def create_api_key(
    req: ApiKeyCreate,
    db: Session = Depends(get_db),
    agent: Agent = Depends(get_current_agent),
):
    """Create an API key. The full key is returned ONCE — store it safely."""
    _require_admin(agent)
    raw = "psk_" + pysecrets.token_urlsafe(32)
    key = ApiKey(
        name=req.name,
        key_prefix=raw[:10],
        key_hash=hashlib.sha256(raw.encode()).hexdigest(),
        created_by=agent.id,
    )
    db.add(key)
    db.commit()
    db.refresh(key)
    log_activity(
        db, "api_key_created", entity_type="api_key", entity_id=key.id,
        agent_id=agent.id, description=f"API key '{key.name}' created",
    )
    return {"id": key.id, "name": key.name, "api_key": raw,
            "note": "Store this key now — it will not be shown again."}


@router.delete("/api-keys/{key_id}", status_code=204)
def revoke_api_key(
    key_id: int,
    db: Session = Depends(get_db),
    agent: Agent = Depends(get_current_agent),
):
    _require_admin(agent)
    key = db.query(ApiKey).filter(ApiKey.id == key_id).first()
    if not key:
        raise HTTPException(404, "API key not found")
    key.is_active = False
    db.commit()
    log_activity(
        db, "api_key_revoked", entity_type="api_key", entity_id=key_id,
        agent_id=agent.id, description=f"API key '{key.name}' revoked",
    )


# ── Outbound webhooks ─────────────────────────────────────────────────────────

class WebhookCreate(BaseModel):
    url: str
    secret: str = ""
    events: list[str] | None = None


@router.get("/webhook-events")
def list_webhook_events():
    return WEBHOOK_EVENTS


@router.get("/webhooks")
def list_webhooks(
    db: Session = Depends(get_db),
    agent: Agent = Depends(get_current_agent),
):
    _require_admin(agent)
    hooks = db.query(WebhookEndpoint).order_by(WebhookEndpoint.created_at.desc()).all()
    return [
        {
            "id": h.id, "url": h.url, "events": h.events or [],
            "is_active": h.is_active, "failure_count": h.failure_count,
            "has_secret": bool(h.secret),
        }
        for h in hooks
    ]


@router.post("/webhooks", status_code=201)
def create_webhook(
    req: WebhookCreate,
    db: Session = Depends(get_db),
    agent: Agent = Depends(get_current_agent),
):
    _require_admin(agent)
    if not req.url.startswith(("http://", "https://")):
        raise HTTPException(400, "URL must start with http:// or https://")
    invalid = [e for e in (req.events or []) if e not in WEBHOOK_EVENTS]
    if invalid:
        raise HTTPException(400, f"Unknown events: {invalid}")
    hook = WebhookEndpoint(
        url=req.url, secret=req.secret, events=req.events, created_by=agent.id
    )
    db.add(hook)
    db.commit()
    db.refresh(hook)
    log_activity(
        db, "webhook_created", entity_type="webhook", entity_id=hook.id,
        agent_id=agent.id, description=f"Outbound webhook {hook.url} created",
    )
    return {"id": hook.id, "url": hook.url, "events": hook.events or []}


@router.post("/webhooks/{hook_id}/test")
async def test_webhook(
    hook_id: int,
    background: BackgroundTasks,
    db: Session = Depends(get_db),
    agent: Agent = Depends(get_current_agent),
):
    _require_admin(agent)
    hook = db.query(WebhookEndpoint).filter(WebhookEndpoint.id == hook_id).first()
    if not hook:
        raise HTTPException(404, "Webhook not found")
    background.add_task(dispatch_event, "message.received", {
        "test": True, "chat_id": 0, "body": "Test event from Hyperscope CRM",
    })
    return {"ok": True, "message": "Test event queued"}


@router.delete("/webhooks/{hook_id}", status_code=204)
def delete_webhook(
    hook_id: int,
    db: Session = Depends(get_db),
    agent: Agent = Depends(get_current_agent),
):
    _require_admin(agent)
    hook = db.query(WebhookEndpoint).filter(WebhookEndpoint.id == hook_id).first()
    if not hook:
        raise HTTPException(404, "Webhook not found")
    db.delete(hook)
    db.commit()
    log_activity(
        db, "webhook_deleted", entity_type="webhook", entity_id=hook_id,
        agent_id=agent.id, description=f"Outbound webhook {hook.url} deleted",
    )
