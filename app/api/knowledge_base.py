from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.models.knowledge_item import KnowledgeItem
from app.schemas.ai_agent import KnowledgeItemCreate, KnowledgeItemOut

router = APIRouter(prefix="/knowledge-base", tags=["knowledge-base"])


@router.get("", response_model=list[KnowledgeItemOut])
def list_items(
    status: str | None = None,
    item_type: str | None = None,
    db: Session = Depends(get_db),
):
    q = db.query(KnowledgeItem)
    if status:
        q = q.filter(KnowledgeItem.status == status)
    if item_type:
        q = q.filter(KnowledgeItem.item_type == item_type)
    return q.all()


@router.post("", response_model=KnowledgeItemOut, status_code=201)
def create_item(req: KnowledgeItemCreate, db: Session = Depends(get_db)):
    item = KnowledgeItem(**req.model_dump())
    db.add(item)
    db.commit()
    db.refresh(item)
    return item


@router.patch("/{item_id}", response_model=KnowledgeItemOut)
def update_item(item_id: int, req: KnowledgeItemCreate, db: Session = Depends(get_db)):
    item = db.query(KnowledgeItem).filter(KnowledgeItem.id == item_id).first()
    if not item:
        raise HTTPException(404, "Item not found")
    for k, v in req.model_dump().items():
        setattr(item, k, v)
    db.commit()
    db.refresh(item)
    return item


@router.patch("/{item_id}/approve")
def approve_item(item_id: int, db: Session = Depends(get_db)):
    item = db.query(KnowledgeItem).filter(KnowledgeItem.id == item_id).first()
    if not item:
        raise HTTPException(404, "Item not found")
    item.status = "active"
    db.commit()
    return {"ok": True}


@router.delete("/{item_id}", status_code=204)
def delete_item(item_id: int, db: Session = Depends(get_db)):
    item = db.query(KnowledgeItem).filter(KnowledgeItem.id == item_id).first()
    if not item:
        raise HTTPException(404, "Item not found")
    db.delete(item)
    db.commit()
