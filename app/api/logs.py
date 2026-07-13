from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.models.agent import Agent
from app.services.activity_service import ActivityService

router = APIRouter(prefix="/logs", tags=["activity-logs"])


@router.get("")
def list_logs(
    action: str | None = None,
    entity_type: str | None = None,
    agent_id: int | None = None,
    search: str | None = None,
    limit: int = 100,
    offset: int = 0,
    db: Session = Depends(get_db),
):
    svc = ActivityService(db)
    logs = svc.list_logs(
        action=action, entity_type=entity_type,
        agent_id=agent_id, search=search,
        limit=limit, offset=offset,
    )
    agent_names = {a.id: a.name for a in db.query(Agent).all()}
    return [svc.serialize(entry, agent_names) for entry in logs]


@router.get("/actions")
def list_actions(db: Session = Depends(get_db)):
    return ActivityService(db).distinct_actions()
