from fastapi import APIRouter, Body, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.models.chat import Chat
from app.services.ai_agent_service import AIAgentService
from app.services.gemini_service import GeminiService
from app.services.inbox_service import InboxService

router = APIRouter(prefix="/ai", tags=["ai-agent"])


@router.post("/chat/{chat_id}/activate")
def activate_ai(chat_id: int, db: Session = Depends(get_db)):
    chat = db.query(Chat).filter(Chat.id == chat_id).first()
    if not chat:
        raise HTTPException(404, "Chat not found")
    chat.ai_active = True
    chat.ai_state = "ACTIVE"
    db.commit()
    return {"ok": True, "ai_state": "ACTIVE"}


@router.post("/chat/{chat_id}/deactivate")
def deactivate_ai(chat_id: int, db: Session = Depends(get_db)):
    chat = db.query(Chat).filter(Chat.id == chat_id).first()
    if not chat:
        raise HTTPException(404, "Chat not found")
    chat.ai_active = False
    chat.ai_state = "INACTIVE"
    db.commit()
    return {"ok": True, "ai_state": "INACTIVE"}


@router.post("/chat/{chat_id}/takeover")
def human_takeover(chat_id: int, db: Session = Depends(get_db)):
    chat = db.query(Chat).filter(Chat.id == chat_id).first()
    if not chat:
        raise HTTPException(404, "Chat not found")
    AIAgentService(db).human_takeover(chat)
    return {"ok": True, "ai_state": "SNOOZED"}


@router.post("/chat/{chat_id}/summarize")
async def summarize_chat(chat_id: int, db: Session = Depends(get_db)):
    msgs = InboxService(db).get_messages(chat_id, limit=40)
    if not msgs:
        return {"summary": "No messages yet."}
    msg_list = [{"sender_name": m.sender_name, "body": m.body} for m in msgs]
    try:
        summary = await GeminiService().summarize_chat(msg_list)
    except Exception as exc:
        raise HTTPException(500, f"AI error: {exc}")
    return {"summary": summary}


@router.post("/chat/{chat_id}/suggest-reply")
async def suggest_reply(chat_id: int, db: Session = Depends(get_db)):
    msgs = InboxService(db).get_messages(chat_id, limit=10)
    context = "\n".join(
        f"[{'Me' if m.from_me else m.sender_name}]: {m.body}"
        for m in msgs
    )
    try:
        reply = await GeminiService().generate_reply(context)
    except Exception as exc:
        raise HTTPException(500, f"AI error: {exc}")
    return {"reply": reply}


class TranslateRequest(BaseModel):
    text: str
    target_language: str = "English"


@router.post("/translate")
async def translate_message(req: TranslateRequest):
    try:
        translated = await GeminiService().translate(req.text, req.target_language)
    except Exception as exc:
        raise HTTPException(500, f"Translation error: {exc}")
    return {"translated": translated}


class PolishRequest(BaseModel):
    text: str
    tone: str = "professional"


@router.post("/polish")
async def polish_reply(req: PolishRequest):
    """Polish a draft reply: fix grammar, keep it WhatsApp-natural (Hyperscope 'Polish replies')."""
    if not req.text.strip():
        raise HTTPException(400, "Text is empty")
    try:
        polished = await GeminiService().polish_reply(req.text, req.tone)
    except Exception as exc:
        raise HTTPException(500, f"AI error: {exc}")
    return {"polished": polished}
