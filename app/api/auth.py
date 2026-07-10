import logging

from fastapi import APIRouter, Depends, HTTPException
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.orm import Session

from app.core.security import create_access_token, hash_password, verify_password
from app.db.session import get_db
from app.models.agent import Agent
from app.schemas.auth import AgentCreate, AgentOut, LoginRequest, TokenResponse

router = APIRouter(prefix="/auth", tags=["auth"])
logger = logging.getLogger(__name__)

_bearer = HTTPBearer(auto_error=True)


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
    agent = db.query(Agent).filter(Agent.email == req.email).first()
    if not agent or not verify_password(req.password, agent.password_hash):
        raise HTTPException(status_code=401, detail="Invalid email or password")
    if not agent.is_active:
        raise HTTPException(status_code=403, detail="Account disabled")
    token = create_access_token({"sub": str(agent.id), "email": agent.email, "role": agent.role.value})
    return TokenResponse(
        access_token=token,
        agent_id=agent.id,
        name=agent.name,
        email=agent.email,
        role=agent.role,
    )


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
    agent = Agent(
        email=req.email,
        name=req.name,
        password_hash=hash_password(req.password),
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
