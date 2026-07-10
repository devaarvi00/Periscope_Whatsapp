from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.models.label import Label
from app.schemas.ai_agent import LabelCreate, LabelOut

router = APIRouter(prefix="/labels", tags=["labels"])


@router.get("", response_model=list[LabelOut])
def list_labels(db: Session = Depends(get_db)):
    return db.query(Label).all()


@router.post("", response_model=LabelOut, status_code=201)
def create_label(req: LabelCreate, db: Session = Depends(get_db)):
    existing = db.query(Label).filter(Label.name == req.name).first()
    if existing:
        raise HTTPException(400, "Label name already exists")
    label = Label(**req.model_dump())
    db.add(label)
    db.commit()
    db.refresh(label)
    return label


@router.patch("/{label_id}", response_model=LabelOut)
def update_label(label_id: int, req: LabelCreate, db: Session = Depends(get_db)):
    label = db.query(Label).filter(Label.id == label_id).first()
    if not label:
        raise HTTPException(404, "Label not found")
    label.name = req.name
    label.color = req.color
    db.commit()
    db.refresh(label)
    return label


@router.delete("/{label_id}", status_code=204)
def delete_label(label_id: int, db: Session = Depends(get_db)):
    label = db.query(Label).filter(Label.id == label_id).first()
    if not label:
        raise HTTPException(404, "Label not found")
    db.delete(label)
    db.commit()
