"""AI Agent → Tools (custom), Credit Usage, Self-Training, Analytics, Logs,
Internal Contacts."""
from __future__ import annotations

import logging
from datetime import datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.api.auth import get_current_agent
from app.db.session import get_db
from app.models.agent import Agent, AgentRole
from app.models.ai_records import AICustomTool, AIInternalContact, AIRunLog, AIUsage
from app.models.knowledge_item import KnowledgeItem
from app.schemas.ai_agent import CustomToolIn, InternalContactCreate

router = APIRouter(prefix="/ai", tags=["ai-agent"])
logger = logging.getLogger(__name__)


def _admin(agent: Agent) -> None:
    if agent.role != AgentRole.ADMIN:
        raise HTTPException(403, "Only admins can change AI agent settings")


def _range(date_from: str | None, date_to: str | None, default_days: int = 30) -> tuple[datetime, datetime]:
    """[from, to) in naive UTC. Accepts ISO datetimes or YYYY-MM-DD."""
    def parse(v: str) -> datetime:
        try:
            return datetime.fromisoformat(v.replace("Z", "+00:00")).replace(tzinfo=None)
        except ValueError:
            raise HTTPException(400, f"Invalid date: {v}")
    end = parse(date_to) if date_to else datetime.utcnow() + timedelta(minutes=1)
    start = parse(date_from) if date_from else end - timedelta(days=default_days)
    if start >= end:
        raise HTTPException(400, "'from' must be before 'to'")
    if end - start > timedelta(days=400):
        raise HTTPException(400, "Date range is limited to 400 days")
    return start, end


def _local_day(dt: datetime) -> str:
    from app.services.business_time import utc_naive_to_local
    return utc_naive_to_local(dt).strftime("%Y-%m-%d")


def _day_keys(start: datetime, end: datetime) -> list[str]:
    from app.services.business_time import utc_naive_to_local
    d = utc_naive_to_local(start).date()
    last = utc_naive_to_local(end - timedelta(seconds=1)).date()
    out = []
    while d <= last and len(out) < 400:
        out.append(d.isoformat())
        d += timedelta(days=1)
    return out


# ── Internal contacts ────────────────────────────────────────────────────────
def _ic_out(r: AIInternalContact) -> dict:
    return {"id": r.id, "number": r.number, "label": r.label or "",
            "created_at": r.created_at.isoformat() if r.created_at else None}


@router.get("/internal-contacts")
def list_internal_contacts(db: Session = Depends(get_db)):
    return [_ic_out(r) for r in db.query(AIInternalContact).order_by(AIInternalContact.created_at.desc()).all()]


@router.post("/internal-contacts", status_code=201)
def add_internal_contact(req: InternalContactCreate, db: Session = Depends(get_db),
                         agent: Agent = Depends(get_current_agent)):
    from app.services.ai_agent_service import digits
    _admin(agent)
    number = digits(req.number)
    if not 5 <= len(number) <= 20:
        raise HTTPException(400, "Enter a phone number with country code, e.g. +91 98765 43210")
    if db.query(AIInternalContact).filter(AIInternalContact.number == number).first():
        raise HTTPException(409, "This number is already an internal contact")
    row = AIInternalContact(number=number, label=req.label.strip())
    db.add(row)
    db.commit()
    db.refresh(row)
    return _ic_out(row)


@router.post("/internal-contacts/import-phones")
def import_phone_numbers(db: Session = Depends(get_db), agent: Agent = Depends(get_current_agent)):
    """Add the workspace's own WhatsApp numbers (messages between our numbers
    must never trigger the agent)."""
    from app.models.phone import Phone
    from app.services.ai_agent_service import digits
    _admin(agent)
    existing = {r.number for r in db.query(AIInternalContact.number).all()}
    added = 0
    for p in db.query(Phone).all():
        n = digits(p.phone_number)
        if 5 <= len(n) <= 20 and n not in existing:
            db.add(AIInternalContact(number=n, label=f"Our phone · {p.name}"[:255]))
            existing.add(n)
            added += 1
    db.commit()
    return {"added": added}


@router.delete("/internal-contacts/{contact_id}", status_code=204)
def delete_internal_contact(contact_id: int, db: Session = Depends(get_db),
                            agent: Agent = Depends(get_current_agent)):
    _admin(agent)
    row = db.query(AIInternalContact).filter(AIInternalContact.id == contact_id).first()
    if not row:
        raise HTTPException(404, "Not found")
    db.delete(row)
    db.commit()


# ── Custom tools ─────────────────────────────────────────────────────────────
def _tool_out(t: AICustomTool) -> dict:
    return {"id": t.id, "name": t.name, "description": t.description or "", "method": t.method,
            "url": t.url, "header_names": sorted((t.headers or {}).keys()), "params": t.params or [],
            "enabled": bool(t.enabled), "timeout_seconds": t.timeout_seconds}


async def _check_tool(req: CustomToolIn) -> None:
    from app.services.url_safety import UnsafeURLError, assert_public_url
    try:
        await assert_public_url(req.url)
    except UnsafeURLError as exc:
        raise HTTPException(400, f"URL not allowed: {exc}")
    names = [p.name for p in req.params]
    if len(names) != len(set(names)):
        raise HTTPException(400, "Parameter names must be unique")


@router.get("/custom-tools")
def list_custom_tools(db: Session = Depends(get_db), agent: Agent = Depends(get_current_agent)):
    _admin(agent)
    return [_tool_out(t) for t in db.query(AICustomTool).order_by(AICustomTool.id).all()]


@router.post("/custom-tools", status_code=201)
async def create_custom_tool(req: CustomToolIn, db: Session = Depends(get_db),
                             agent: Agent = Depends(get_current_agent)):
    _admin(agent)
    await _check_tool(req)
    if db.query(AICustomTool).filter(AICustomTool.name == req.name).first():
        raise HTTPException(409, "A tool with this name already exists")
    t = AICustomTool(name=req.name, description=req.description, method=req.method, url=req.url.strip(),
                     headers=req.headers or None, params=[p.model_dump() for p in req.params],
                     enabled=req.enabled, timeout_seconds=req.timeout_seconds)
    db.add(t)
    db.commit()
    db.refresh(t)
    return _tool_out(t)


@router.put("/custom-tools/{tool_id}")
async def update_custom_tool(tool_id: int, req: CustomToolIn, db: Session = Depends(get_db),
                             agent: Agent = Depends(get_current_agent)):
    _admin(agent)
    t = db.query(AICustomTool).filter(AICustomTool.id == tool_id).first()
    if not t:
        raise HTTPException(404, "Tool not found")
    await _check_tool(req)
    clash = db.query(AICustomTool).filter(AICustomTool.name == req.name, AICustomTool.id != tool_id).first()
    if clash:
        raise HTTPException(409, "A tool with this name already exists")
    t.name, t.description, t.method, t.url = req.name, req.description, req.method, req.url.strip()
    t.params = [p.model_dump() for p in req.params]
    t.enabled, t.timeout_seconds = req.enabled, req.timeout_seconds
    if req.headers is not None:  # omitted = keep the stored (write-only) headers
        t.headers = req.headers or None
    db.commit()
    return _tool_out(t)


@router.delete("/custom-tools/{tool_id}", status_code=204)
def delete_custom_tool(tool_id: int, db: Session = Depends(get_db), agent: Agent = Depends(get_current_agent)):
    _admin(agent)
    t = db.query(AICustomTool).filter(AICustomTool.id == tool_id).first()
    if not t:
        raise HTTPException(404, "Tool not found")
    db.delete(t)
    db.commit()


# ── Credit usage ─────────────────────────────────────────────────────────────
@router.get("/usage")
def usage(date_from: str | None = Query(None, alias="from"), date_to: str | None = Query(None, alias="to"),
          db: Session = Depends(get_db)):
    start, end = _range(date_from, date_to)
    rows = db.query(AIUsage.created_at, AIUsage.purpose, AIUsage.total_tokens, AIUsage.prompt_tokens,
                    AIUsage.candidate_tokens, AIUsage.ok) \
        .filter(AIUsage.created_at >= start, AIUsage.created_at < end).all()
    days = {k: {"date": k, "tokens": 0, "requests": 0} for k in _day_keys(start, end)}
    purposes: dict[str, dict] = {}
    totals = {"total_tokens": 0, "prompt_tokens": 0, "candidate_tokens": 0, "requests": 0, "failed": 0}
    for created, purpose, total, prompt, cand, ok in rows:
        totals["total_tokens"] += total or 0
        totals["prompt_tokens"] += prompt or 0
        totals["candidate_tokens"] += cand or 0
        totals["requests"] += 1
        totals["failed"] += 0 if ok else 1
        d = days.get(_local_day(created))
        if d:
            d["tokens"] += total or 0
            d["requests"] += 1
        p = purposes.setdefault(purpose or "other", {"purpose": purpose or "other", "tokens": 0, "requests": 0})
        p["tokens"] += total or 0
        p["requests"] += 1
    from app.core.config import settings
    return {"totals": totals, "daily": list(days.values()),
            "by_purpose": sorted(purposes.values(), key=lambda x: x["tokens"], reverse=True),
            "model": settings.gemini_model}


# ── Analytics ────────────────────────────────────────────────────────────────
@router.get("/analytics")
async def analytics(date_from: str | None = Query(None, alias="from"), date_to: str | None = Query(None, alias="to"),
                    db: Session = Depends(get_db)):
    start, end = _range(date_from, date_to)
    logs = db.query(AIRunLog).filter(AIRunLog.created_at >= start, AIRunLog.created_at < end).all()
    days = {k: {"date": k, "replies": 0, "tickets": 0, "notes": 0} for k in _day_keys(start, end)}
    chats: dict[int, dict] = {}
    stats = {"messages_sent": 0, "tickets_created": 0, "private_notes": 0, "drafts": 0}
    for r in logs:
        d = days.get(_local_day(r.created_at))
        tools = r.tools or []
        n_tickets = sum(1 for t in tools if t.get("name") == "create_ticket" and t.get("ok"))
        n_notes = sum(1 for t in tools if t.get("name") == "private_note" and t.get("ok"))
        replied = r.decision == "replied"
        stats["messages_sent"] += replied
        stats["drafts"] += r.decision == "drafted"
        stats["tickets_created"] += n_tickets
        stats["private_notes"] += n_notes
        if d:
            d["replies"] += replied
            d["tickets"] += n_tickets
            d["notes"] += n_notes
        if r.chat_id is not None and (replied or tools or r.decision == "drafted"):
            c = chats.setdefault(r.chat_id, {"chat_id": r.chat_id, "chat_name": r.chat_name or f"#{r.chat_id}",
                                             "messages_sent": 0, "total_tokens": 0, "tool_calls": 0})
            c["messages_sent"] += replied
            c["tool_calls"] += len(tools)
    if chats:
        tok = dict(db.query(AIUsage.chat_id, func.sum(AIUsage.total_tokens))
                   .filter(AIUsage.created_at >= start, AIUsage.created_at < end,
                           AIUsage.chat_id.in_(list(chats.keys())))
                   .group_by(AIUsage.chat_id).all())
        for cid, c in chats.items():
            c["total_tokens"] = int(tok.get(cid) or 0)
    try:
        from app.services.mongo_chat_service import MongoInboxService
        active = await MongoInboxService().db.chats.count_documents({"ai_active": True})
    except Exception:
        active = None
    stats["active_chats"] = active
    return {"stats": stats, "daily": list(days.values()),
            "chats": sorted(chats.values(), key=lambda c: (c["messages_sent"], c["total_tokens"]), reverse=True)[:100]}


# ── Run logs ─────────────────────────────────────────────────────────────────
def _run_out(r: AIRunLog, full: bool = False) -> dict:
    out = {"id": r.id, "created_at": r.created_at.isoformat() + "Z" if r.created_at else None,
           "chat_id": r.chat_id, "chat_name": r.chat_name, "decision": r.decision, "reason": r.reason,
           "detail": r.detail, "tokens": r.tokens, "latency_ms": r.latency_ms,
           "tools": [t.get("name") for t in (r.tools or [])], "kb_miss": bool(r.kb_miss)}
    if full:
        out.update(message_preview=r.message_preview, reply=r.reply, tools_detail=r.tools or [],
                   kb_titles=r.kb_titles or [], phone_id=r.phone_id, message_wid=r.message_wid)
    return out


@router.get("/runs")
def list_runs(decision: str | None = None, reason: str | None = None, q: str | None = None,
              page: int = Query(1, ge=1), page_size: int = Query(25, ge=5, le=100),
              db: Session = Depends(get_db)):
    from app.services.ai_agent_service import prune_run_logs
    prune_run_logs(db)
    query = db.query(AIRunLog)
    if decision:
        query = query.filter(AIRunLog.decision == decision)
    if reason:
        query = query.filter(AIRunLog.reason == reason)
    if q:
        query = query.filter(AIRunLog.chat_name.ilike(f"%{q.strip()[:100]}%"))
    total = query.count()
    rows = query.order_by(AIRunLog.id.desc()).offset((page - 1) * page_size).limit(page_size).all()
    counts = dict(db.query(AIRunLog.decision, func.count(AIRunLog.id)).group_by(AIRunLog.decision).all())
    return {"items": [_run_out(r) for r in rows], "total": total, "page": page, "page_size": page_size,
            "counts": counts}


@router.get("/runs/{run_id}")
def get_run(run_id: int, db: Session = Depends(get_db)):
    r = db.query(AIRunLog).filter(AIRunLog.id == run_id).first()
    if not r:
        raise HTTPException(404, "Not found")
    return _run_out(r, full=True)


# ── Self-training ────────────────────────────────────────────────────────────
_SELF_TRAIN_SCHEMA = {
    "type": "ARRAY",
    "items": {"type": "OBJECT", "properties": {
        "question": {"type": "STRING"}, "answer": {"type": "STRING"}, "chat": {"type": "INTEGER"},
    }, "required": ["question", "answer"]},
}


@router.post("/self-training/generate")
async def generate_suggestions(days: int = Query(14, ge=1, le=60), db: Session = Depends(get_db),
                               agent: Agent = Depends(get_current_agent)):
    """Ask Gemini to extract reusable Q&A pairs from recent conversations
    where a teammate answered the customer — chats where the AI said it
    didn't know come first. New pairs land in the KB as Needs Review."""
    from app.services.ai_agent_service import AI_SENDER_NAME
    from app.services.gemini_service import GeminiService
    from app.services.mongo_chat_service import MongoInboxService
    _admin(agent)
    since = datetime.utcnow() - timedelta(days=days)
    inbox = MongoInboxService()
    miss_ids = [cid for (cid,) in db.query(AIRunLog.chat_id).filter(
        AIRunLog.kb_miss == True, AIRunLog.created_at >= since, AIRunLog.chat_id.isnot(None))  # noqa: E712
        .distinct().limit(20).all()]
    # Chats where a teammate (not the AI) replied recently
    human = await inbox.db.messages.aggregate([
        {"$match": {"from_me": True, "timestamp": {"$gte": since}, "sender_name": {"$ne": AI_SENDER_NAME},
                    "body": {"$nin": ["", None]}}},
        {"$group": {"_id": "$chat_id", "n": {"$sum": 1}}},
        {"$sort": {"n": -1}}, {"$limit": 20},
    ]).to_list(20)
    chat_ids = list(dict.fromkeys(miss_ids + [h["_id"] for h in human if h.get("_id") is not None]))[:8]
    blocks, used = [], 0
    for cid in chat_ids:
        chat = await inbox.get_chat_by_id(cid)
        if not chat or chat.get("is_group"):
            continue
        msgs = await inbox.get_messages(chat_id=cid, limit=30)
        lines = [f"[{'Business' if m.get('from_me') else 'Customer'}] {(m.get('body') or '')[:400]}"
                 for m in msgs if (m.get("body") or "").strip()]
        if not any(line.startswith("[Business]") for line in lines):
            continue
        block = f"=== Chat {cid} ===\n" + "\n".join(lines)
        if used + len(block) > 16000:
            break
        blocks.append(block)
        used += len(block)
    if not blocks:
        return {"created": 0, "items": [], "message": "No recent conversations with teammate answers to learn from."}
    existing = {(t or "").strip().lower() for (t,) in db.query(KnowledgeItem.title).all()}
    prompt = (
        "Below are WhatsApp conversations between customers and a business's team. Extract up to 8 reusable "
        "question-and-answer pairs that would help an AI agent answer FUTURE customers: general facts about the "
        "business's products, prices, policies, hours, delivery, process. Use the business's actual answers. "
        "Skip anything specific to one person (names, order numbers, addresses, phone numbers, payment details), "
        "greetings, and anything uncertain. Write the question the way a customer would ask it, and the answer as "
        "a short standalone fact. Include the chat number each pair came from.\n\n" + "\n\n".join(blocks)
    )
    try:
        res = await GeminiService().generate(prompt, schema=_SELF_TRAIN_SCHEMA, temperature=0.2,
                                             max_tokens=2048, purpose="self_training")
    except Exception as exc:
        logger.warning("Self-training generation failed: %s", exc)
        raise HTTPException(502, "The AI service failed to respond. Please try again.")
    pairs = res.data if isinstance(res.data, list) else []
    created = []
    for p in pairs[:8]:
        if not isinstance(p, dict):
            continue
        q, a = str(p.get("question") or "").strip()[:500], str(p.get("answer") or "").strip()[:4000]
        if not q or not a or q.lower() in existing:
            continue
        existing.add(q.lower())
        cid = p.get("chat") if isinstance(p.get("chat"), int) and p.get("chat") in chat_ids else None
        item = KnowledgeItem(item_type="self_learned", title=q, content=a, status="review",
                             is_self_learned=True, origin_chat_id=cid, created_by=agent.id)
        db.add(item)
        created.append(item)
    db.commit()
    return {"created": len(created), "items": [{"id": i.id, "title": i.title} for i in created],
            "tokens": res.total_tokens}
