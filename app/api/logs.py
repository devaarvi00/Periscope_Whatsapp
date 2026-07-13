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
    start_date: str | None = None,
    end_date: str | None = None,
    limit: int = 100,
    offset: int = 0,
    db: Session = Depends(get_db),
):
    from datetime import datetime
    start_dt = None
    if start_date:
        try:
            start_dt = datetime.fromisoformat(start_date.replace("Z", "+00:00"))
        except ValueError:
            pass
    end_dt = None
    if end_date:
        try:
            end_dt = datetime.fromisoformat(end_date.replace("Z", "+00:00"))
        except ValueError:
            pass

    svc = ActivityService(db)
    logs = svc.list_logs(
        action=action, entity_type=entity_type,
        agent_id=agent_id, search=search,
        start_date=start_dt, end_date=end_dt,
        limit=limit, offset=offset,
    )
    agent_names = {a.id: a.name for a in db.query(Agent).all()}
    return [svc.serialize(entry, agent_names) for entry in logs]


@router.get("/actions")
def list_actions(db: Session = Depends(get_db)):
    return ActivityService(db).distinct_actions()
