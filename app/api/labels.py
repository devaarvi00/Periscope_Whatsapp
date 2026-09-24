from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.api.auth import get_current_agent
from app.db.session import get_db
from app.models.agent import Agent
from app.models.label import LABEL_TYPES, Label
from app.schemas.ai_agent import LabelCreate, LabelOut, LabelUpdate
from app.services.access import has_action, has_screen, require_screen

router = APIRouter(prefix="/labels", tags=["labels"])


@router.get("", response_model=list[LabelOut])
def list_labels(
    type: str | None = Query(None, description="chat | ticket | phone; omit for every label"),
    db: Session = Depends(get_db),
):
    q = db.query(Label)
    if type:
        if type not in LABEL_TYPES:
            raise HTTPException(400, "type must be one of: " + ", ".join(LABEL_TYPES))
        q = q.filter(Label.type == type)
    return q.order_by(Label.name).all()


@router.post("", response_model=LabelOut, status_code=201)
def create_label(
    req: LabelCreate,
    db: Session = Depends(get_db),
    agent: Agent = Depends(get_current_agent),
):
    # Agents create labels inline from the chat/ticket label picker, so either
    # the "Update Labels" action or the Labels settings screen is enough.
    if not (has_action(db, agent, "update_labels") or has_screen(db, agent, "labels")):
        raise HTTPException(403, "Your organization doesn't allow agents to change labels")
    name = req.name.strip()
    if not name:
        raise HTTPException(422, "Label name is required")
    if db.query(Label).filter(Label.name == name).first():
        raise HTTPException(400, "Label name already exists")
    label = Label(name=name, color=req.color, type=req.type)
    db.add(label)
    db.commit()
    db.refresh(label)
    return label


@router.patch("/{label_id}", response_model=LabelOut)
def update_label(
    label_id: int,
    req: LabelUpdate,
    db: Session = Depends(get_db),
    agent: Agent = Depends(get_current_agent),
):
    require_screen(db, agent, "labels", "label management")
    label = db.query(Label).filter(Label.id == label_id).first()
    if not label:
        raise HTTPException(404, "Label not found")
    changes = req.model_dump(exclude_unset=True, exclude_none=True)
    if "name" in changes:
        name = changes["name"].strip()
        clash = db.query(Label).filter(Label.name == name, Label.id != label_id).first()
        if clash:
            raise HTTPException(400, "Label name already exists")
        changes["name"] = name
    for k, v in changes.items():
        setattr(label, k, v)
    db.commit()
    db.refresh(label)
    return label


@router.delete("/{label_id}", status_code=204)
def delete_label(
    label_id: int,
    db: Session = Depends(get_db),
    agent: Agent = Depends(get_current_agent),
):
    require_screen(db, agent, "labels", "label management")
    label = db.query(Label).filter(Label.id == label_id).first()
    if not label:
        raise HTTPException(404, "Label not found")
    db.delete(label)
    db.commit()
