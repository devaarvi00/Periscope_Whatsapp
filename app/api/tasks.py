from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import desc
from sqlalchemy.orm import Session

from app.api.auth import get_current_agent
from app.db.session import get_db
from app.models.agent import Agent
from app.models.task import Task
from app.services.activity_service import log_activity

router = APIRouter(prefix="/tasks", tags=["tasks"])


class TaskCreate(BaseModel):
    title: str
    notes: str | None = None
    priority: str = "low"
    due_date: str | None = None  # ISO 8601
    chat_id: int | None = None
    assigned_to: int | None = None


class TaskUpdate(BaseModel):
    title: str | None = None
    notes: str | None = None
    priority: str | None = None
    status: str | None = None
    due_date: str | None = None
    assigned_to: int | None = None


def _serialize(t: Task, agent_names: dict[int, str]) -> dict:
    return {
        "id": t.id, "title": t.title, "notes": t.notes,
        "status": t.status, "priority": t.priority,
        "due_date": t.due_date.isoformat() if t.due_date else None,
        "chat_id": t.chat_id,
        "assigned_to": t.assigned_to,
        "assignee_name": agent_names.get(t.assigned_to, ""),
        "created_by": t.created_by,
        "creator_name": agent_names.get(t.created_by, ""),
        "created_at": t.created_at.isoformat() if t.created_at else None,
    }


def _agent_names(db: Session) -> dict[int, str]:
    return {a.id: a.name for a in db.query(Agent).all()}


@router.get("")
def list_tasks(
    view: str = "my_open",  # my_open|all_active|all|assigned_to_me|high_priority
    chat_id: int | None = None,
    db: Session = Depends(get_db),
    agent: Agent = Depends(get_current_agent),
):
    q = db.query(Task)
    if chat_id:
        q = q.filter(Task.chat_id == chat_id)
    if view == "my_open":
        q = q.filter(Task.status == "open", (Task.assigned_to == agent.id) | (Task.created_by == agent.id))
    elif view == "all_active":
        q = q.filter(Task.status == "open")
    elif view == "assigned_to_me":
        q = q.filter(Task.assigned_to == agent.id)
    elif view == "high_priority":
        q = q.filter(Task.status == "open", Task.priority == "high")
    tasks = q.order_by(desc(Task.created_at)).limit(200).all()
    names = _agent_names(db)
    return [_serialize(t, names) for t in tasks]


@router.post("", status_code=201)
def create_task(
    req: TaskCreate,
    db: Session = Depends(get_db),
    agent: Agent = Depends(get_current_agent),
):
    if not req.title.strip():
        raise HTTPException(400, "Title is required")
    due = None
    if req.due_date:
        try:
            due = datetime.fromisoformat(req.due_date)
        except ValueError:
            raise HTTPException(400, "Invalid due_date (use ISO 8601)")
    task = Task(
        title=req.title.strip()[:500], notes=req.notes,
        priority=req.priority if req.priority in ("low", "medium", "high") else "low",
        due_date=due, chat_id=req.chat_id,
        assigned_to=req.assigned_to, created_by=agent.id,
    )
    db.add(task)
    db.commit()
    db.refresh(task)
    log_activity(
        db, "task_created", entity_type="task", entity_id=task.id,
        agent_id=agent.id, description=f"Task '{task.title}' created",
    )
    return _serialize(task, _agent_names(db))


@router.patch("/{task_id}")
def update_task(
    task_id: int,
    req: TaskUpdate,
    db: Session = Depends(get_db),
    agent: Agent = Depends(get_current_agent),
):
    task = db.query(Task).filter(Task.id == task_id).first()
    if not task:
        raise HTTPException(404, "Task not found")
    changes = req.model_dump(exclude_none=True)
    if "due_date" in changes:
        try:
            changes["due_date"] = datetime.fromisoformat(changes["due_date"])
        except ValueError:
            raise HTTPException(400, "Invalid due_date")
    for k, v in changes.items():
        if hasattr(task, k):
            setattr(task, k, v)
    if changes.get("status") == "done" and not task.completed_at:
        task.completed_at = datetime.utcnow()
    db.commit()
    db.refresh(task)
    log_activity(
        db, "task_updated", entity_type="task", entity_id=task.id,
        agent_id=agent.id, description=f"Task '{task.title}' updated: {', '.join(changes.keys())}",
    )
    return _serialize(task, _agent_names(db))


@router.delete("/{task_id}", status_code=204)
def delete_task(
    task_id: int,
    db: Session = Depends(get_db),
    agent: Agent = Depends(get_current_agent),
):
    task = db.query(Task).filter(Task.id == task_id).first()
    if not task:
        raise HTTPException(404, "Task not found")
    db.delete(task)
    db.commit()
    log_activity(
        db, "task_deleted", entity_type="task", entity_id=task_id,
        agent_id=agent.id, description=f"Task #{task_id} deleted",
    )
