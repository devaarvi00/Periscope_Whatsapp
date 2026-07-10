from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.models.quick_reply import QuickReply
from app.schemas.ai_agent import QuickReplyCreate, QuickReplyOut

router = APIRouter(prefix="/quick-replies", tags=["quick-replies"])


@router.get("", response_model=list[QuickReplyOut])
def list_quick_replies(db: Session = Depends(get_db)):
    return db.query(QuickReply).all()


@router.post("", response_model=QuickReplyOut, status_code=201)
def create_quick_reply(req: QuickReplyCreate, db: Session = Depends(get_db)):
    existing = db.query(QuickReply).filter(QuickReply.command == req.command).first()
    if existing:
        raise HTTPException(400, "Command already exists")
    qr = QuickReply(command=req.command, message=req.message)
    db.add(qr)
    db.commit()
    db.refresh(qr)
    return qr


@router.patch("/{qr_id}", response_model=QuickReplyOut)
def update_quick_reply(qr_id: int, req: QuickReplyCreate, db: Session = Depends(get_db)):
    qr = db.query(QuickReply).filter(QuickReply.id == qr_id).first()
    if not qr:
        raise HTTPException(404, "Quick reply not found")
    qr.command = req.command
    qr.message = req.message
    db.commit()
    db.refresh(qr)
    return qr


@router.delete("/{qr_id}", status_code=204)
def delete_quick_reply(qr_id: int, db: Session = Depends(get_db)):
    qr = db.query(QuickReply).filter(QuickReply.id == qr_id).first()
    if not qr:
        raise HTTPException(404, "Quick reply not found")
    db.delete(qr)
    db.commit()
