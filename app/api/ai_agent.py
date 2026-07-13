from fastapi import APIRouter, Body, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.api.auth import get_current_agent
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


class AISettingsUpdate(BaseModel):
    enabled: bool | None = None
    auto_activate_new_chats: bool | None = None
    activation_rules: str | None = None
    hours_start: str | None = None
    hours_end: str | None = None
    agent_name: str | None = None
    role_description: str | None = None
    personality: str | None = None
    custom_instructions: str | None = None
    restrictions: str | None = None
    response_delay_seconds: int | None = None
    snooze_after_human_seconds: int | None = None
    flag_enabled: bool | None = None
    flag_criteria: str | None = None


def _settings_out(cfg) -> dict:
    return {
        "enabled": cfg.enabled,
        "auto_activate_new_chats": cfg.auto_activate_new_chats,
        "activation_rules": cfg.activation_rules or "",
        "hours_start": cfg.hours_start or "",
        "hours_end": cfg.hours_end or "",
        "agent_name": cfg.agent_name,
        "role_description": cfg.role_description or "",
        "personality": cfg.personality,
        "custom_instructions": cfg.custom_instructions or "",
        "restrictions": cfg.restrictions or "",
        "response_delay_seconds": cfg.response_delay_seconds,
        "snooze_after_human_seconds": cfg.snooze_after_human_seconds,
        "flag_enabled": cfg.flag_enabled,
        "flag_criteria": cfg.flag_criteria or "",
    }


@router.get("/settings")
def get_settings_endpoint(db: Session = Depends(get_db)):
    from app.models.ai_settings import get_ai_settings
    return _settings_out(get_ai_settings(db))


@router.put("/settings")
def update_settings_endpoint(
    req: AISettingsUpdate,
    db: Session = Depends(get_db),
    agent=Depends(get_current_agent),
):
    from app.models.agent import AgentRole
    from app.models.ai_settings import PERSONALITIES, get_ai_settings
    from app.services.activity_service import log_activity

    if agent.role != AgentRole.ADMIN:
        raise HTTPException(403, "Only admins can change AI agent settings")
    cfg = get_ai_settings(db)
    changes = req.model_dump(exclude_none=True)
    if "personality" in changes and changes["personality"] not in PERSONALITIES:
        raise HTTPException(400, f"personality must be one of {PERSONALITIES}")
    for field in ("response_delay_seconds", "snooze_after_human_seconds"):
        if field in changes:
            changes[field] = max(0, min(int(changes[field]), 6000))
    for field in ("hours_start", "hours_end"):
        if field in changes and changes[field]:
            import re as _re
            if not _re.fullmatch(r"\d{2}:\d{2}", changes[field]):
                raise HTTPException(400, f"{field} must be HH:MM")
    for k, v in changes.items():
        setattr(cfg, k, v if v != "" else None)
    if "agent_name" in changes and not changes["agent_name"]:
        cfg.agent_name = "AI Assistant"
    if "enabled" in changes:
        cfg.enabled = bool(req.enabled)
    if "flag_enabled" in changes:
        cfg.flag_enabled = bool(req.flag_enabled)
    if "auto_activate_new_chats" in changes:
        cfg.auto_activate_new_chats = bool(req.auto_activate_new_chats)
    db.commit()
    log_activity(
        db, "ai_settings_updated", entity_type="ai_settings", entity_id=1,
        agent_id=agent.id, description=f"AI agent settings updated: {', '.join(changes.keys())}",
    )
    return _settings_out(cfg)


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


# ── Org & Chat Assistant ─────────────────────────────────────────────────────

class AssistantRequest(BaseModel):
    prompt: str = ""
    chat_id: int | None = None   # present → chat assistant, absent → org assistant
    recipe: str | None = None    # optional shortcut action


def _org_context_pack(db: Session) -> str:
    """Snapshot of workspace state the assistant can reason over."""
    from datetime import datetime, timedelta

    from app.models.agent import Agent as AgentModel
    from app.models.task import Task
    from app.models.ticket import Ticket, TicketStatus
    from app.services.analytics_service import AnalyticsService

    dash = AnalyticsService(db).get_dashboard_metrics()
    lines = [
        f"Now (UTC): {datetime.utcnow().isoformat(timespec='minutes')}",
        f"Totals: {dash['total_chats']} chats, {dash['unread_chats']} unread, "
        f"{dash['flagged_chats']} flagged, {dash['open_tickets']} open tickets, "
        f"{dash['in_progress_tickets']} in-progress tickets",
    ]

    agents = {a.id: a.name for a in db.query(AgentModel).all()}

    recent = (
        db.query(Chat).filter(Chat.is_archived == False)
        .order_by(Chat.last_message_at.desc()).limit(20).all()
    )
    lines.append("\nRecent chats (name | unread | flagged | assigned | last message):")
    for c in recent:
        lines.append(
            f"- {c.name or c.chat_wid} | unread={c.unread_count or 0} | "
            f"flagged={'yes' if c.is_flagged else 'no'} | "
            f"assigned={agents.get(c.assigned_to, 'nobody')} | "
            f"{(c.last_message or '')[:70]}"
        )

    tickets = (
        db.query(Ticket)
        .filter(Ticket.status.in_([TicketStatus.OPEN, TicketStatus.IN_PROGRESS]))
        .order_by(Ticket.created_at.desc()).limit(15).all()
    )
    lines.append("\nOpen tickets (title | priority | assigned | age):")
    now = datetime.utcnow()
    for t in tickets:
        age_h = int((now - t.created_at).total_seconds() // 3600) if t.created_at else 0
        prio = t.priority.value if hasattr(t.priority, "value") else str(t.priority)
        lines.append(
            f"- #{t.id} {t.title[:60]} | {prio} | "
            f"{agents.get(t.assigned_to, 'unassigned')} | {age_h}h old"
        )

    open_tasks = db.query(Task).filter(Task.status == "open").count()
    overdue = db.query(Task).filter(
        Task.status == "open", Task.due_date.isnot(None),
        Task.due_date < now,
    ).count()
    lines.append(f"\nTasks: {open_tasks} open, {overdue} overdue")
    return "\n".join(lines)


def _chat_context_pack(db: Session, chat_id: int) -> str:
    from app.services.inbox_service import InboxService

    chat = db.query(Chat).filter(Chat.id == chat_id).first()
    if not chat:
        return "Chat not found."
    msgs = InboxService(db).get_messages(chat_id, limit=40)
    lines = [
        f"Chat: {chat.name or chat.chat_wid} ({'group' if chat.is_group else '1:1'}), "
        f"unread={chat.unread_count or 0}, flagged={'yes' if chat.is_flagged else 'no'}",
        "\nConversation (oldest first):",
    ]
    for m in reversed(msgs):
        who = "Business" if m.from_me else (m.sender_name or "Customer")
        lines.append(f"[{who}] {(m.body or '(media)')[:200]}")
    return "\n".join(lines)


RECIPES = {
    # org scope
    "summarize_24h": "Summarize what happened across all chats in the last 24 hours: "
                     "key conversations, unanswered customers, and anything urgent.",
    "find_followups": "Which chats are waiting on a reply from us? List them by name with "
                      "what the customer last said, most urgent first.",
    "triage_unassigned": "List chats and tickets that have no assigned agent and suggest "
                         "who should pick each up, with a one-line reason.",
    "stale_tickets": "Which open tickets look stale (old, unassigned or high priority)? "
                     "Recommend next steps for each.",
    # chat scope
    "summarize_chat": "Summarize this conversation in 3-5 short bullet points.",
    "sentiment": "What is the customer's sentiment in this conversation and why? "
                 "One short paragraph.",
    "draft_reply": "Draft a short WhatsApp-style reply to the customer's last message. "
                   "Return only the reply text.",
}


@router.post("/assistant")
async def assistant(req: AssistantRequest, db: Session = Depends(get_db)):
    """Org & Chat Assistant: answers workspace questions from real data.

    Read-only by design — it analyzes and drafts, it never sends messages
    or changes records itself.
    """
    question = (RECIPES.get(req.recipe) or req.prompt or "").strip()
    if not question:
        raise HTTPException(400, "Ask a question or pick a recipe")

    if req.chat_id:
        pack = _chat_context_pack(db, req.chat_id)
    else:
        pack = _org_context_pack(db)
        # Include last-24h message activity for the summary recipe
        if req.recipe == "summarize_24h":
            from datetime import datetime, timedelta
            from app.models.message import Message
            since = datetime.utcnow() - timedelta(hours=24)
            msgs = (
                db.query(Message).filter(Message.timestamp >= since)
                .order_by(Message.chat_id, Message.timestamp).limit(300).all()
            )
            chat_names = {c.id: c.name for c in db.query(Chat).all()}
            lines = ["\nMessages in the last 24h:"]
            for m in msgs:
                who = "Business" if m.from_me else (m.sender_name or "Customer")
                lines.append(f"[{chat_names.get(m.chat_id, m.chat_id)}] {who}: {(m.body or '(media)')[:100]}")
            pack += "\n".join(lines)

    try:
        answer = await GeminiService().assistant_answer(question, pack)
    except Exception as exc:
        raise HTTPException(500, f"AI error: {exc}")
    return {"answer": answer, "scope": "chat" if req.chat_id else "org"}
