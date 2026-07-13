import logging
from typing import Any

from sqlalchemy import desc
from sqlalchemy.orm import Session

from app.models.activity_log import ActivityLog

logger = logging.getLogger(__name__)


def log_activity(
    db: Session,
    action: str,
    entity_type: str | None = None,
    entity_id: int | None = None,
    agent_id: int | None = None,
    description: str | None = None,
    metadata: dict | None = None,
    commit: bool = True,
) -> ActivityLog | None:
    """Write an audit-log entry. Never raises — logging must not break the caller."""
    try:
        entry = ActivityLog(
            action=action,
            entity_type=entity_type,
            entity_id=entity_id,
            agent_id=agent_id,
            description=(description or "")[:500] or None,
            metadata_=metadata,
        )
        db.add(entry)
        if commit:
            db.commit()
        return entry
    except Exception as exc:
        logger.warning("Activity log write failed (%s): %s", action, exc)
        try:
            db.rollback()
        except Exception:
            pass
        return None


class ActivityService:
    def __init__(self, db: Session) -> None:
        self.db = db

    def list_logs(
        self,
        action: str | None = None,
        entity_type: str | None = None,
        agent_id: int | None = None,
        search: str | None = None,
        start_date: Any = None,
        end_date: Any = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[ActivityLog]:
        q = self.db.query(ActivityLog)
        if action:
            q = q.filter(ActivityLog.action == action)
        if entity_type:
            q = q.filter(ActivityLog.entity_type == entity_type)
        if agent_id is not None:
            q = q.filter(ActivityLog.agent_id == agent_id)
        if search:
            q = q.filter(ActivityLog.description.ilike(f"%{search}%"))
        if start_date:
            q = q.filter(ActivityLog.created_at >= start_date)
        if end_date:
            q = q.filter(ActivityLog.created_at <= end_date)
        return q.order_by(desc(ActivityLog.created_at)).offset(offset).limit(min(limit, 500)).all()

    def distinct_actions(self) -> list[str]:
        rows = self.db.query(ActivityLog.action).distinct().all()
        return sorted(r[0] for r in rows)

    def serialize(self, entry: ActivityLog, agent_names: dict[int, str] | None = None) -> dict[str, Any]:
        return {
            "id": entry.id,
            "action": entry.action,
            "entity_type": entry.entity_type,
            "entity_id": entry.entity_id,
            "agent_id": entry.agent_id,
            "agent_name": (agent_names or {}).get(entry.agent_id, ""),
            "description": entry.description,
            "metadata": entry.metadata_,
            "created_at": entry.created_at.isoformat() if entry.created_at else None,
        }
