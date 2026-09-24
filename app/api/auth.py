import logging
import threading
import time

from fastapi import APIRouter, Depends, HTTPException
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.orm import Session

from app.core.security import create_access_token, hash_password, verify_password
from app.db.session import get_db
from app.models.agent import Agent
from app.schemas.auth import AgentCreate, AgentOut, ChangePasswordRequest, LoginRequest, TokenResponse

router = APIRouter(prefix="/auth", tags=["auth"])
logger = logging.getLogger(__name__)

_bearer = HTTPBearer(auto_error=True)

# ── Failed-login lockout ─────────────────────────────────────────── #
# In-memory and per-process: correct only because the app runs a single
# uvicorn worker. Moving to multiple workers/replicas needs a shared store
# (e.g. Redis). State is lost on restart, which is acceptable here.
MAX_FAILED_LOGINS = 5
FAILED_LOGIN_WINDOW_SECONDS = 15 * 60
LOCKOUT_SECONDS = 15 * 60
_MAX_TRACKED_EMAILS = 10_000

_login_lock = threading.Lock()
# email -> (failure_count, first_failure_ts, locked_until_ts)
_failed_logins: dict[str, tuple[int, float, float]] = {}


def _lockout_remaining(email: str) -> int:
    """Seconds left on an active lockout for this email, else 0."""
    now = time.monotonic()
    with _login_lock:
        entry = _failed_logins.get(email)
        if not entry:
            return 0
        count, first_ts, locked_until = entry
        if locked_until > now:
            return int(locked_until - now) + 1
        if locked_until or now - first_ts > FAILED_LOGIN_WINDOW_SECONDS:
            # Lock expired or failure window elapsed — start fresh
            _failed_logins.pop(email, None)
        return 0


def _record_failed_login(email: str) -> None:
    now = time.monotonic()
    with _login_lock:
        if len(_failed_logins) > _MAX_TRACKED_EMAILS:
            # Bound memory under credential-spraying: drop stale entries
            for key, (_, f_ts, l_until) in list(_failed_logins.items()):
                if l_until <= now and now - f_ts > FAILED_LOGIN_WINDOW_SECONDS:
                    del _failed_logins[key]
        count, first_ts, _ = _failed_logins.get(email, (0, now, 0.0))
        count += 1
        locked_until = now + LOCKOUT_SECONDS if count >= MAX_FAILED_LOGINS else 0.0
        _failed_logins[email] = (count, first_ts, locked_until)
    if locked_until:
        logger.warning("Login locked for %s after %d failed attempts", email, count)


def _reset_failed_logins(email: str) -> None:
    with _login_lock:
        _failed_logins.pop(email, None)


def get_current_agent(
    credentials: HTTPAuthorizationCredentials = Depends(_bearer),
    db: Session = Depends(get_db),
) -> Agent:
    from app.core.security import decode_access_token
    payload = decode_access_token(credentials.credentials)
    if not payload:
        raise HTTPException(status_code=401, detail="Invalid or expired token")
    try:
        agent_id = int(payload.get("sub", 0))
    except (TypeError, ValueError):
        raise HTTPException(status_code=401, detail="Invalid token payload")
    agent = db.query(Agent).filter(Agent.id == agent_id).first()
    if not agent or not agent.is_active:
        raise HTTPException(status_code=401, detail="Agent not found or inactive")
    return agent


@router.post("/login", response_model=TokenResponse)
def login(req: LoginRequest, db: Session = Depends(get_db)):
    email_key = str(req.email).strip().lower()
    remaining = _lockout_remaining(email_key)
    if remaining:
        raise HTTPException(
            status_code=429,
            detail="Too many failed login attempts. Try again later.",
            headers={"Retry-After": str(remaining)},
        )
    agent = db.query(Agent).filter(Agent.email == req.email).first()
    if not agent or not verify_password(req.password, agent.password_hash):
        _record_failed_login(email_key)
        raise HTTPException(status_code=401, detail="Invalid email or password")
    if not agent.is_active:
        raise HTTPException(status_code=403, detail="Account disabled")
    _reset_failed_logins(email_key)
    token = create_access_token({"sub": str(agent.id), "email": agent.email, "role": agent.role.value})
    return TokenResponse(
        access_token=token,
        agent_id=agent.id,
        name=agent.name,
        email=agent.email,
        role=agent.role,
    )


@router.post("/change-password", status_code=204)
def change_password(
    req: ChangePasswordRequest,
    db: Session = Depends(get_db),
    agent: Agent = Depends(get_current_agent),
):
    # 400 (not 401) on a wrong current password: the frontend treats 401 as
    # "session expired" and logs the user out.
    email_key = agent.email.strip().lower()
    remaining = _lockout_remaining(email_key)
    if remaining:
        raise HTTPException(
            status_code=429,
            detail="Too many failed attempts. Try again later.",
            headers={"Retry-After": str(remaining)},
        )
    if not verify_password(req.current_password, agent.password_hash):
        _record_failed_login(email_key)
        raise HTTPException(status_code=400, detail="Current password is incorrect")
    if req.new_password == req.current_password:
        raise HTTPException(status_code=400, detail="New password must be different")
    try:
        agent.password_hash = hash_password(req.new_password)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    _reset_failed_logins(email_key)
    db.commit()
    logger.info("Password changed for agent_id=%s", agent.id)
    return None


@router.post("/register", response_model=AgentOut, status_code=201)
def register(
    req: AgentCreate,
    db: Session = Depends(get_db),
    current_agent: Agent = Depends(get_current_agent),
):
    """Create a new agent. Only admins can do this."""
    from app.models.agent import AgentRole
    if current_agent.role != AgentRole.ADMIN:
        raise HTTPException(status_code=403, detail="Only admins can create agents")
    existing = db.query(Agent).filter(Agent.email == req.email).first()
    if existing:
        raise HTTPException(status_code=400, detail="Email already registered")
    try:
        password_hash = hash_password(req.password)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    agent = Agent(
        email=req.email,
        name=req.name,
        password_hash=password_hash,
        role=req.role,
    )
    db.add(agent)
    db.commit()
    db.refresh(agent)
    return agent


@router.get("/me", response_model=AgentOut)
def get_me(agent: Agent = Depends(get_current_agent)):
    return agent


@router.get("/agents", response_model=list[AgentOut])
def list_agents(
    db: Session = Depends(get_db),
    _agent: Agent = Depends(get_current_agent),
):
    return db.query(Agent).filter(Agent.is_active == True).all()


@router.get("/agents/{agent_id}/phones")
def get_agent_phones(
    agent_id: int,
    db: Session = Depends(get_db),
    current_agent: Agent = Depends(get_current_agent),
):
    """Number-level permissions for an agent. Empty list = access to all numbers."""
    from app.models.agent_phone import AgentPhone
    rows = db.query(AgentPhone.phone_id).filter(AgentPhone.agent_id == agent_id).all()
    return {"agent_id": agent_id, "phone_ids": [r[0] for r in rows]}


@router.put("/agents/{agent_id}/phones")
def set_agent_phones(
    agent_id: int,
    phone_ids: list[int],
    db: Session = Depends(get_db),
    current_agent: Agent = Depends(get_current_agent),
):
    """Restrict an agent to specific numbers (admin only). Empty list clears restrictions."""
    from app.models.agent import AgentRole
    from app.models.agent_phone import AgentPhone
    from app.services.activity_service import log_activity

    if current_agent.role != AgentRole.ADMIN:
        raise HTTPException(status_code=403, detail="Only admins can set number permissions")
    if not db.query(Agent).filter(Agent.id == agent_id).first():
        raise HTTPException(status_code=404, detail="Agent not found")

    db.query(AgentPhone).filter(AgentPhone.agent_id == agent_id).delete()
    for pid in set(phone_ids):
        db.add(AgentPhone(agent_id=agent_id, phone_id=pid))
    db.commit()
    log_activity(
        db, "agent_phones_updated", entity_type="agent", entity_id=agent_id,
        agent_id=current_agent.id,
        description=f"Number permissions for agent #{agent_id} set to {sorted(set(phone_ids)) or 'all'}",
    )
    return {"agent_id": agent_id, "phone_ids": sorted(set(phone_ids))}
