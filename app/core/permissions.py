from sqlalchemy.orm import Session

from app.models.agent import Agent, AgentRole
from app.models.agent_phone import AgentPhone


def allowed_phone_ids(db: Session, agent: Agent) -> list[int] | None:
    """Phone IDs this agent may access. None means unrestricted (admins,
    or agents with no number-level restrictions configured)."""
    if agent.role == AgentRole.ADMIN:
        return None
    rows = db.query(AgentPhone.phone_id).filter(AgentPhone.agent_id == agent.id).all()
    if not rows:
        return None
    return [r[0] for r in rows]
