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


# ── Operation logs (Group / API / Webhooks / Rules / Scheduled) ──────────── #
# Same "logs" screen guard as the activity log (installed by org_config).
# Agents never see which API key made a request.

from datetime import datetime as _dt, timedelta as _td, timezone as _tz  # noqa: E402

from fastapi import HTTPException, Query  # noqa: E402

from app.api.auth import get_current_agent  # noqa: E402
from app.models.operation_log import OPERATION_KINDS, OPERATION_STATUSES, OperationLog  # noqa: E402
from app.services import operation_log as oplog  # noqa: E402
from app.services.access import is_admin, mask_number, should_mask_numbers  # noqa: E402


def _parse_when(v: str | None, end: bool = False) -> _dt | None:
    if not v:
        return None
    try:
        d = _dt.fromisoformat(v.replace("Z", "+00:00"))
    except ValueError:
        raise HTTPException(400, f"Invalid date: {v}")
    if d.tzinfo is not None:
        d = d.astimezone(_tz.utc).replace(tzinfo=None)
    if end and len(v) <= 10:
        d += _td(days=1)
    return d


def _agent_names(db: Session) -> dict[int, str]:
    return {a.id: a.name for a in db.query(Agent.id, Agent.name).all()}


@router.get("/operations")
def list_operation_logs(
    kind: str | None = None,
    q: str | None = Query(None, max_length=100),
    status: str | None = None,
    from_: str | None = Query(None, alias="from"),
    to: str | None = None,
    performed_by: str | None = Query(None, max_length=20),
    page: int = Query(1, ge=1, le=10000),
    page_size: int = Query(50, ge=1, le=200),
    db: Session = Depends(get_db),
    agent: Agent = Depends(get_current_agent),
):
    if kind and kind not in OPERATION_KINDS:
        raise HTTPException(400, "Unknown log kind")
    if status and status not in OPERATION_STATUSES:
        raise HTTPException(400, "Unknown status")
    retention_start = _dt.utcnow() - _td(days=oplog.RETENTION_DAYS)
    since = max(_parse_when(from_) or retention_start, retention_start)
    until = _parse_when(to, end=True)
    rows, total = oplog.list_logs(
        db, kind=kind, q=q, status=status, since=since, until=until,
        performed_by=performed_by, page=page, page_size=page_size,
    )
    names = _agent_names(db)
    admin = is_admin(agent)
    return {
        "items": [oplog.serialize(r, names, admin=admin) for r in rows],
        "total": total,
        "page": page,
        "page_size": page_size,
        "retention_days": oplog.RETENTION_DAYS,
    }


@router.get("/operations/{uid}")
def get_operation_log(
    uid: str,
    db: Session = Depends(get_db),
    agent: Agent = Depends(get_current_agent),
):
    row = db.query(OperationLog).filter(OperationLog.uid == uid[:16]).first()
    if not row:
        raise HTTPException(404, "Log not found")
    mask = mask_number if should_mask_numbers(db, agent) else None
    return oplog.serialize(row, _agent_names(db), full=True, admin=is_admin(agent), mask=mask)
