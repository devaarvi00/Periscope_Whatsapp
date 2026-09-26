import logging
import re

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.api.auth import get_current_agent
from app.db.session import get_db
from app.models.agent import Agent
from app.schemas.ai_agent import AISettingsUpdate, PlaygroundRequest
from app.services.access import get_accessible_chat
from app.services.ai_agent_service import AIAgentService
from app.services.gemini_service import GeminiService
from app.services.mongo_chat_service import MongoInboxService

router = APIRouter(prefix="/ai", tags=["ai-agent"])
logger = logging.getLogger(__name__)

_AI_ERROR = "The AI service failed to respond. Please try again."


@router.post("/chat/{chat_id}/activate")
async def activate_ai(
    chat_id: int,
    db: Session = Depends(get_db),
    agent: Agent = Depends(get_current_agent),
):
    inbox = MongoInboxService()
    await get_accessible_chat(db, agent, chat_id)
    await inbox.update_chat(chat_id, ai_active=True, ai_state="ACTIVE")
    # An explicit choice also wins over "Auto-activate for all chats"
    await inbox.db.chats.update_one({"id": chat_id}, {"$set": {"ai_opt_out": False}})
    return {"ok": True, "ai_state": "ACTIVE"}


@router.post("/chat/{chat_id}/deactivate")
async def deactivate_ai(
    chat_id: int,
    db: Session = Depends(get_db),
    agent: Agent = Depends(get_current_agent),
):
    inbox = MongoInboxService()
    await get_accessible_chat(db, agent, chat_id)
    await inbox.update_chat(chat_id, ai_active=False, ai_state="INACTIVE")
    await inbox.db.chats.update_one({"id": chat_id}, {"$set": {"ai_opt_out": True}})
    return {"ok": True, "ai_state": "INACTIVE"}


@router.post("/chat/{chat_id}/takeover")
async def human_takeover(
    chat_id: int,
    db: Session = Depends(get_db),
    agent: Agent = Depends(get_current_agent),
):
    chat = await get_accessible_chat(db, agent, chat_id)
    await AIAgentService(db).human_takeover(chat)
    return {"ok": True, "ai_state": "SNOOZED"}


@router.post("/chat/{chat_id}/summarize")
async def summarize_chat(
    chat_id: int,
    db: Session = Depends(get_db),
    agent: Agent = Depends(get_current_agent),
):
    await get_accessible_chat(db, agent, chat_id)
    inbox = MongoInboxService()
    msgs = await inbox.get_messages(chat_id=chat_id, limit=40)
    if not msgs:
        return {"summary": "No messages yet."}
    msg_list = [{"sender_name": m.get("sender_name"), "body": m.get("body")} for m in msgs]
    try:
        summary = await GeminiService(chat_id=chat_id).summarize_chat(msg_list)
    except Exception as exc:
        logger.exception("AI summarize failed: %s", exc)
        raise HTTPException(500, _AI_ERROR)
    return {"summary": summary}


@router.post("/chat/{chat_id}/suggest-reply")
async def suggest_reply(
    chat_id: int,
    db: Session = Depends(get_db),
    agent: Agent = Depends(get_current_agent),
):
    await get_accessible_chat(db, agent, chat_id)
    inbox = MongoInboxService()
    msgs = await inbox.get_messages(chat_id=chat_id, limit=10)
    context = "\n".join(
        f"[{'Me' if m.get('from_me') else m.get('sender_name')}]: {m.get('body')}"
        for m in msgs
    )
    try:
        reply = await GeminiService(chat_id=chat_id).generate_reply(context)
    except Exception as exc:
        logger.exception("AI suggest-reply failed: %s", exc)
        raise HTTPException(500, _AI_ERROR)
    return {"reply": reply}


class TranslateRequest(BaseModel):
    text: str
    target_language: str = "English"


@router.post("/translate")
async def translate_message(req: TranslateRequest):
    try:
        translated = await GeminiService().translate(req.text, req.target_language)
    except Exception as exc:
        logger.exception("AI translate failed: %s", exc)
        raise HTTPException(500, _AI_ERROR)
    return {"translated": translated}


def _require_admin(agent) -> None:
    from app.models.agent import AgentRole
    if agent.role != AgentRole.ADMIN:
        raise HTTPException(403, "Only admins can change AI agent settings")


def _setup_status(db: Session, cfg) -> dict:
    from app.models.ai_settings import identity_done, role_done
    from app.models.knowledge_item import KnowledgeItem
    kb_active = db.query(KnowledgeItem).filter(KnowledgeItem.status == "active").count()
    return {"role": role_done(cfg), "identity": identity_done(cfg), "knowledge": kb_active > 0,
            "knowledge_active": kb_active}


def _settings_out(cfg, db: Session | None = None) -> dict:
    from app.models.ai_settings import (
        DEFAULT_ACTIVATION_RULES, PERSONALITIES, default_hours_schedule, required_steps_done,
    )
    sched = cfg.hours_schedule or None
    if not sched:
        sched = default_hours_schedule()
        if cfg.hours_start and cfg.hours_end:  # legacy single window
            sched = {d: {"on": True, "start": cfg.hours_start, "end": cfg.hours_end} for d in sched}
    out = {
        "enabled": bool(cfg.enabled),
        "effective_enabled": bool(cfg.enabled) and required_steps_done(cfg),
        "auto_activate_new_chats": bool(cfg.auto_activate_new_chats),
        "activation_rules": cfg.activation_rules or DEFAULT_ACTIVATION_RULES,
        "default_activation_rules": DEFAULT_ACTIVATION_RULES,
        "allowed_phone_ids": [int(x) for x in (cfg.allowed_phone_ids or [])],
        "response_delay_seconds": int(cfg.response_delay_seconds or 0),
        "snooze_after_human_seconds": int(cfg.snooze_after_human_seconds or 0),
        "hours_enabled": bool(cfg.hours_enabled) or bool(cfg.hours_start and cfg.hours_end and not cfg.hours_schedule),
        "hours_schedule": sched,
        "hours_start": cfg.hours_start or "",
        "hours_end": cfg.hours_end or "",
        "agent_name": cfg.agent_name or "",
        "personality": cfg.personality if cfg.personality in PERSONALITIES else "friendly",
        "personalities": list(PERSONALITIES),
        "role_description": cfg.role_description or "",
        "custom_instructions": cfg.custom_instructions or "",
        "restrictions": cfg.restrictions or "",
        "allow_send_messages": bool(cfg.allow_send_messages),
        "allow_create_tickets": bool(cfg.allow_create_tickets),
        "ticket_instructions": cfg.ticket_instructions or "",
        "allow_private_notes": bool(cfg.allow_private_notes),
        "note_instructions": cfg.note_instructions or "",
        "flag_enabled": bool(cfg.flag_enabled),
        "flag_criteria": cfg.flag_criteria or "",
    }
    if db is not None:
        out["setup"] = _setup_status(db, cfg)
    return out


def _validated_changes(req: AISettingsUpdate) -> dict:
    from app.models.ai_settings import DEFAULT_ACTIVATION_RULES, PERSONALITIES
    changes = req.model_dump(exclude_unset=True)
    changes = {k: v for k, v in changes.items() if v is not None or k in ("allowed_phone_ids",)}
    if "personality" in changes and changes["personality"] not in PERSONALITIES:
        raise HTTPException(400, f"personality must be one of {', '.join(PERSONALITIES)}")
    if "response_delay_seconds" in changes:
        changes["response_delay_seconds"] = max(3, min(int(changes["response_delay_seconds"]), 6000))
    if "snooze_after_human_seconds" in changes:
        changes["snooze_after_human_seconds"] = max(0, min(int(changes["snooze_after_human_seconds"]), 6000))
    for field in ("hours_start", "hours_end"):
        if changes.get(field) and not re.fullmatch(r"([01]\d|2[0-3]):[0-5]\d", changes[field]):
            raise HTTPException(400, f"{field} must be HH:MM")
    if "hours_schedule" in changes and changes["hours_schedule"] is not None:
        from app.models.ai_settings import WEEKDAYS
        sched = changes["hours_schedule"]
        unknown = set(sched) - set(WEEKDAYS)
        if unknown:
            raise HTTPException(400, f"Unknown day(s): {', '.join(sorted(unknown))}")
        changes["hours_schedule"] = {d: dict(sched.get(d) or {"on": False, "start": "09:00", "end": "18:00"})
                                     for d in WEEKDAYS}
    if "allowed_phone_ids" in changes:
        changes["allowed_phone_ids"] = sorted({int(x) for x in (changes["allowed_phone_ids"] or [])})
    if "activation_rules" in changes and changes["activation_rules"].strip() == DEFAULT_ACTIVATION_RULES.strip():
        changes["activation_rules"] = ""  # keep following the default
    return changes


@router.get("/settings")
def get_settings_endpoint(db: Session = Depends(get_db)):
    from app.models.ai_settings import get_ai_settings
    return _settings_out(get_ai_settings(db), db)


@router.put("/settings")
def update_settings_endpoint(
    req: AISettingsUpdate,
    db: Session = Depends(get_db),
    agent=Depends(get_current_agent),
):
    from app.models.ai_settings import DEFAULT_AGENT_NAME, get_ai_settings, required_steps_done
    from app.services.activity_service import log_activity

    _require_admin(agent)
    cfg = get_ai_settings(db)
    changes = _validated_changes(req)
    for k, v in changes.items():
        if k == "enabled":
            continue
        if isinstance(v, str):
            v = v.strip() or None
        setattr(cfg, k, v)
    if "agent_name" in changes:
        cfg.agent_name = (changes["agent_name"] or "").strip() or DEFAULT_AGENT_NAME
        cfg.identity_configured = bool((changes["agent_name"] or "").strip())
    if "hours_schedule" in changes:  # the per-day schedule replaces the legacy window
        cfg.hours_start = cfg.hours_end = None
    if "enabled" in changes:
        if changes["enabled"] and not required_steps_done(cfg):
            db.rollback()
            raise HTTPException(400, "Finish the required setup steps (role and identity) before turning the agent on")
        cfg.enabled = bool(changes["enabled"])
    db.commit()
    log_activity(
        db, "ai_settings_updated", entity_type="ai_settings", entity_id=1,
        agent_id=agent.id, description=f"AI agent settings updated: {', '.join(changes.keys()) or 'no changes'}",
    )
    return _settings_out(cfg, db)


@router.post("/playground")
async def playground(
    req: PlaygroundRequest,
    db: Session = Depends(get_db),
    agent: Agent = Depends(get_current_agent),
):
    """Chat with the agent using the current (possibly unsaved) settings.
    Never sends WhatsApp messages and never creates tickets or notes; only
    Gemini usage is recorded (purpose=playground)."""
    from app.models.ai_settings import DEFAULT_ACTIVATION_RULES, get_ai_settings
    from app.services.ai_agent_service import cfg_snapshot, compose_reply, transcript
    from app.services.gemini_service import GeminiBlocked

    overrides = _validated_changes(req.settings) if req.settings else {}
    overrides.pop("enabled", None)
    cfg = cfg_snapshot(get_ai_settings(db), overrides)
    db.rollback()  # nothing from the playground is ever persisted
    history = [{"from_me": m.role == "agent", "body": m.text} for m in req.messages]
    if history[-1]["from_me"]:
        raise HTTPException(400, "The last message must be from the customer")
    out: dict = {"decision": None, "reply": "", "known": True, "actions": [], "knowledge": [], "tokens": 0,
                 "would_send": bool(cfg.allow_send_messages)}
    try:
        if req.check_rules:
            verdict = await GeminiService().classify_for_ai_agent(
                history[-1]["body"], rules=(cfg.activation_rules or "").strip() or DEFAULT_ACTIVATION_RULES,
                history=transcript(history[:-1][-6:]), purpose="playground")
            out["tokens"] += verdict.get("tokens", 0)
            out["decision"] = {"should_respond": verdict["should_respond"], "reason": verdict.get("reason", "")}
            if not verdict["should_respond"]:
                return out
        result = await compose_reply(db, cfg, history, purpose="playground", run_tools="get_only")
    except GeminiBlocked as exc:
        raise HTTPException(422, f"Gemini blocked this conversation: {exc}")
    except Exception as exc:
        logger.warning("AI playground failed: %s", exc)
        raise HTTPException(502, _AI_ERROR)
    out["tokens"] += result.tokens
    out.update(reply=result.reply, known=result.known,
               knowledge=[{"title": p["title"], "type": p["type"]} for p in result.passages])
    if result.create_ticket:
        out["actions"].append({"type": "create_ticket", **result.create_ticket})
    if result.private_note:
        out["actions"].append({"type": "private_note", "content": result.private_note})
    for c in result.tool_calls:
        out["actions"].append({"type": "tool", **c})
    return out


class PolishRequest(BaseModel):
    text: str
    tone: str = "professional"


@router.post("/polish")
async def polish_reply(req: PolishRequest):
    if not req.text.strip():
        raise HTTPException(400, "Text is empty")
    try:
        polished = await GeminiService().polish_reply(req.text, req.tone)
    except Exception as exc:
        logger.exception("AI polish failed: %s", exc)
        raise HTTPException(500, _AI_ERROR)
    return {"polished": polished}


# ── Org & Chat Assistant ──────────────────────────────────────────────────────

class AssistantRequest(BaseModel):
    prompt: str = ""
    chat_id: int | None = None
    recipe: str | None = None


async def _org_context_pack(db: Session, phone_ids: list[int] | None = None) -> str:
    from datetime import datetime
    from app.models.agent import Agent as AgentModel
    from app.models.task import Task
    from app.models.ticket import Ticket, TicketStatus
    from app.services.analytics_service import AnalyticsService

    dash = await AnalyticsService(db).get_dashboard_metrics()
    lines = [
        f"Now (UTC): {datetime.utcnow().isoformat(timespec='minutes')}",
        f"Totals: {dash['total_chats']} chats, {dash['unread_chats']} unread, "
        f"{dash['flagged_chats']} flagged, {dash['open_tickets']} open tickets, "
        f"{dash['in_progress_tickets']} in-progress tickets",
    ]
    agents = {a.id: a.name for a in db.query(AgentModel).all()}
    inbox = MongoInboxService()
    recent = await inbox.list_chats(is_archived=False, phone_ids=phone_ids, limit=20)
    lines.append("\nRecent chats (name | unread | flagged | assigned | last message):")
    for c in recent:
        lines.append(
            f"- {c.get('name') or c.get('chat_wid')} | unread={c.get('unread_count') or 0} | "
            f"flagged={'yes' if c.get('is_flagged') else 'no'} | "
            f"assigned={agents.get(c.get('assigned_to'), 'nobody')} | "
            f"{(c.get('last_message') or '')[:70]}"
        )
    tickets = (
        db.query(Ticket)
        .filter(Ticket.status.in_([TicketStatus.OPEN, TicketStatus.IN_PROGRESS]))
        .order_by(Ticket.created_at.desc()).limit(15).all()
    )
    lines.append("\nOpen tickets:")
    now = datetime.utcnow()
    for t in tickets:
        age_h = int((now - t.created_at).total_seconds() // 3600) if t.created_at else 0
        prio = t.priority.value if hasattr(t.priority, "value") else str(t.priority)
        lines.append(f"- #{t.id} {t.title[:60]} | {prio} | {agents.get(t.assigned_to, 'unassigned')} | {age_h}h old")
    open_tasks = db.query(Task).filter(Task.status == "open").count()
    lines.append(f"\nTasks: {open_tasks} open")
    return "\n".join(lines)


async def _chat_context_pack(chat_id: int) -> str:
    inbox = MongoInboxService()
    chat = await inbox.get_chat_by_id(chat_id)
    if not chat:
        return "Chat not found."
    msgs = await inbox.get_messages(chat_id=chat_id, limit=40)
    lines = [
        f"Chat: {chat.get('name') or chat.get('chat_wid')} ({'group' if chat.get('is_group') else '1:1'}), "
        f"unread={chat.get('unread_count') or 0}, flagged={'yes' if chat.get('is_flagged') else 'no'}",
        "\nConversation (oldest first):",
    ]
    for m in msgs:
        who = "Business" if m.get("from_me") else (m.get("sender_name") or "Customer")
        lines.append(f"[{who}] {(m.get('body') or '(media)')[:200]}")
    return "\n".join(lines)


RECIPES = {
    "summarize_24h": "Summarize what happened across all chats in the last 24 hours.",
    "find_followups": "Which chats are waiting on a reply from us? List them by name.",
    "triage_unassigned": "List chats and tickets with no assigned agent and suggest who should pick each up.",
    "stale_tickets": "Which open tickets look stale? Recommend next steps for each.",
    "summarize_chat": "Summarize this conversation in 3-5 short bullet points.",
    "sentiment": "What is the customer's sentiment in this conversation and why?",
    "draft_reply": "Draft a short WhatsApp-style reply to the customer's last message.",
}


@router.post("/assistant")
async def assistant(
    req: AssistantRequest,
    db: Session = Depends(get_db),
    agent: Agent = Depends(get_current_agent),
):
    from app.core.permissions import allowed_phone_ids
    question = (RECIPES.get(req.recipe) or req.prompt or "").strip()
    if not question:
        raise HTTPException(400, "Ask a question or pick a recipe")

    phone_ids = allowed_phone_ids(db, agent)
    if req.chat_id:
        await get_accessible_chat(db, agent, req.chat_id)
        pack = await _chat_context_pack(req.chat_id)
    else:
        pack = await _org_context_pack(db, phone_ids)
        if req.recipe == "summarize_24h":
            from datetime import datetime, timedelta
            inbox = MongoInboxService()
            since = datetime.utcnow() - timedelta(hours=24)
            msg_filter: dict = {"timestamp": {"$gte": since}}
            if phone_ids is not None:
                msg_filter["phone_id"] = {"$in": phone_ids}
            recent_msgs = await (
                inbox.db.messages.find(msg_filter)
                .sort("timestamp", 1).limit(300).to_list(300)
            )
            lines = ["\nMessages in the last 24h:"]
            for m in recent_msgs:
                who = "Business" if m.get("from_me") else (m.get("sender_name") or "Customer")
                chat = await inbox.get_chat_by_id(m.get("chat_id"))
                chat_label = (chat.get("name") if chat else None) or str(m.get("chat_id"))
                lines.append(f"[{chat_label}] {who}: {(m.get('body') or '(media)')[:100]}")
            pack += "\n".join(lines)

    try:
        answer = await GeminiService().assistant_answer(question, pack)
    except Exception as exc:
        logger.exception("AI assistant failed: %s", exc)
        raise HTTPException(500, _AI_ERROR)
    return {"answer": answer, "scope": "chat" if req.chat_id else "org"}
