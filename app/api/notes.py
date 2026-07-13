import re

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from sqlalchemy.orm import Session

from app.api.auth import get_current_agent
from app.db.session import get_db
from app.models.agent import Agent
from app.models.chat import Chat
from app.models.note import Note
from app.schemas.ai_agent import NoteCreate
from app.services.activity_service import log_activity

router = APIRouter(prefix="/notes", tags=["notes"])


def _serialize(n: Note, agent_names: dict[int, str]) -> dict:
    return {
        "id": n.id,
        "chat_id": n.chat_id,
        "agent_id": n.agent_id,
        "agent_name": agent_names.get(n.agent_id, ""),
        "content": n.content,
        "created_at": n.created_at.isoformat() if n.created_at else None,
    }


def _find_mentions(db: Session, content: str) -> list[Agent]:
    """Resolve @Name mentions to agents (first-name match, case-insensitive)."""
    handles = set(re.findall(r"@([\w.]+)", content))
    if not handles:
        return []
    agents = db.query(Agent).filter(Agent.is_active == True).all()
    mentioned = []
    for agent in agents:
        first = (agent.name or "").split(" ")[0].lower()
        full = (agent.name or "").replace(" ", "").lower()
        for h in handles:
            if h.lower() in (first, full):
                mentioned.append(agent)
                break
    return mentioned


@router.get("/chat/{chat_id}")
def list_notes(
    chat_id: int,
    db: Session = Depends(get_db),
    _agent: Agent = Depends(get_current_agent),
):
    notes = db.query(Note).filter(Note.chat_id == chat_id).order_by(Note.created_at).all()
    names = {a.id: a.name for a in db.query(Agent).all()}
    return [_serialize(n, names) for n in notes]


@router.post("", status_code=201)
async def create_note(
    req: NoteCreate,
    background: BackgroundTasks,
    db: Session = Depends(get_db),
    current_agent: Agent = Depends(get_current_agent),
):
    if not req.content.strip():
        raise HTTPException(400, "Note is empty")
    note = Note(chat_id=req.chat_id, content=req.content, agent_id=current_agent.id)
    db.add(note)
    db.commit()
    db.refresh(note)

    chat = db.query(Chat).filter(Chat.id == req.chat_id).first()
    chat_name = chat.name if chat else f"#{req.chat_id}"

    # Notify @mentioned teammates over WebSocket (team-only, never sent to WhatsApp)
    mentioned = _find_mentions(db, req.content)
    from app.core.ws_manager import ws_manager
    for agent in mentioned:
        if agent.id == current_agent.id:
            continue
        await ws_manager.send_to_agent(agent.id, "note_mention", {
            "chat_id": req.chat_id,
            "chat_name": chat_name,
            "note_id": note.id,
            "by": current_agent.name,
            "content": req.content[:200],
        })
    log_activity(
        db, "private_note_added", entity_type="chat", entity_id=req.chat_id,
        agent_id=current_agent.id,
        description=f"Private note on '{chat_name}'"
                    + (f" mentioning {', '.join(a.name for a in mentioned)}" if mentioned else ""),
    )
    names = {a.id: a.name for a in db.query(Agent).all()}
    return _serialize(note, names)


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
