from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.api.auth import get_current_agent
from app.db.session import get_db
from app.models.agent import Agent
from app.models.note import Note
from app.schemas.ai_agent import NoteCreate, NoteOut

router = APIRouter(prefix="/notes", tags=["notes"])


@router.get("/chat/{chat_id}", response_model=list[NoteOut])
def list_notes(
    chat_id: int,
    db: Session = Depends(get_db),
    _agent: Agent = Depends(get_current_agent),
):
    return db.query(Note).filter(Note.chat_id == chat_id).order_by(Note.created_at).all()


@router.post("", response_model=NoteOut, status_code=201)
def create_note(
    req: NoteCreate,
    db: Session = Depends(get_db),
    current_agent: Agent = Depends(get_current_agent),
):
    note = Note(chat_id=req.chat_id, content=req.content, agent_id=current_agent.id)
    db.add(note)
    db.commit()
    db.refresh(note)
    return note


@router.delete("/{note_id}", status_code=204)
def delete_note(
    note_id: int,
    db: Session = Depends(get_db),
    _agent: Agent = Depends(get_current_agent),
):
    note = db.query(Note).filter(Note.id == note_id).first()
    if not note:
        raise HTTPException(404, "Note not found")
    db.delete(note)
    db.commit()
