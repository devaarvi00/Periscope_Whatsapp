from datetime import datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.api.auth import get_current_agent
from app.db.session import get_db
from app.models.agent import Agent
from app.models.phone import Phone
from app.services.mongo_chat_service import MongoInboxService
from app.services.waha_service import WAHAService

router = APIRouter(prefix="/groups", tags=["groups"])


@router.get("")
async def list_groups(
    search: str | None = None,
    limit: int = 100,
    offset: int = 0,
    db: Session = Depends(get_db),
    agent: Agent = Depends(get_current_agent),
):
    from app.core.permissions import allowed_phone_ids
    inbox = MongoInboxService()
    phone_ids = allowed_phone_ids(db, agent)
    groups = await inbox.list_chats(
        is_group=True,
        is_archived=False,
        search=search,
        phone_ids=phone_ids,
        limit=limit,
        offset=offset,
    )

    week_ago = datetime.utcnow() - timedelta(days=7)
    results = []
    for g in groups:
        msg_count = await inbox.db.messages.count_documents({
            "chat_id": g["id"],
            "timestamp": {"$gte": week_ago},
        })
        lma = g.get("last_message_at")
        results.append({
            "id": g["id"], "chat_wid": g["chat_wid"], "name": g.get("name") or "",
            "phone_id": g["phone_id"], "unread_count": g.get("unread_count") or 0,
            "is_flagged": bool(g.get("is_flagged")), "assigned_to": g.get("assigned_to"),
            "last_message": g.get("last_message") or "",
            "last_message_at": lma.isoformat() if isinstance(lma, datetime) else lma,
            "messages_7d": msg_count,
        })
    return results


@router.get("/{chat_id}/participants")
async def group_participants(chat_id: int, db: Session = Depends(get_db)):
    inbox = MongoInboxService()
    chat = await inbox.get_chat_by_id(chat_id)
    if not chat or not chat.get("is_group"):
        raise HTTPException(404, "Group not found")
    phone = db.query(Phone).filter(Phone.id == chat["phone_id"]).first()
    if not phone:
        raise HTTPException(404, "Phone not found")
    waha = WAHAService.from_phone(phone)
    raw, api_ok = await waha.get_group_participants_with_status(chat["chat_wid"])
    result = []
    for p in raw:
        pid = p.get("id")
        if isinstance(pid, dict):
            pid = pid.get("_serialized") or pid.get("user", "")
        result.append({
            "id": str(pid),
            "number": str(pid).split("@")[0],
            "is_admin": bool(p.get("isAdmin") or p.get("admin")),
        })
    return {
        "group": chat.get("name") or "",
        "count": len(result),
        "participants": result,
        "api_available": api_ok,
    }


class AddParticipantsRequest(BaseModel):
    chat_ids: list[int]        # group chats to add into
    phone_numbers: list[str]   # digits only, e.g. "9198xxxxxx"


@router.post("/add-participants")
async def add_participants(
    req: AddParticipantsRequest,
    db: Session = Depends(get_db),
    agent: Agent = Depends(get_current_agent),
):
    """Bulk action: add contacts to every selected group in one go."""
    if not req.chat_ids or not req.phone_numbers:
        raise HTTPException(400, "Select groups and enter at least one number")
    wids = [n.strip().replace("+", "") + "@c.us" for n in req.phone_numbers if n.strip()]
    inbox = MongoInboxService()
    results = []
    for cid in req.chat_ids[:50]:
        chat = await inbox.get_chat_by_id(cid)
        if not chat or not chat.get("is_group"):
            results.append({"chat_id": cid, "ok": False, "error": "Not a group"})
            continue
        phone = db.query(Phone).filter(Phone.id == chat["phone_id"]).first()
        if not phone:
            results.append({"chat_id": cid, "ok": False, "error": "Phone missing"})
            continue
        waha = WAHAService.from_phone(phone)
        ok = await waha.add_group_participants(chat["chat_wid"], wids)
        results.append({"chat_id": cid, "group": chat.get("name") or "", "ok": ok})
    from app.services.activity_service import log_activity
    log_activity(
        db, "group_participants_added", entity_type="chat", agent_id=agent.id,
        description=f"Added {len(wids)} participant(s) to {sum(1 for r in results if r['ok'])} group(s)",
    )
    return {"results": results}


@router.get("/{chat_id}/analytics")
async def group_analytics(
    chat_id: int,
    days: int = 30,
    db: Session = Depends(get_db),
):
    """Group activity: daily message volume, top senders, in/out split."""
    inbox = MongoInboxService()
    chat = await inbox.get_chat_by_id(chat_id)
    if not chat or not chat.get("is_group"):
        raise HTTPException(404, "Group not found")
    since = datetime.utcnow() - timedelta(days=min(days, 180))

    total = await inbox.db.messages.count_documents({"chat_id": chat_id, "timestamp": {"$gte": since}})
    incoming = await inbox.db.messages.count_documents({"chat_id": chat_id, "from_me": False, "timestamp": {"$gte": since}})

    # Daily volume
    daily_pipeline = [
        {"$match": {"chat_id": chat_id, "timestamp": {"$gte": since}}},
        {"$group": {"_id": {"$dateToString": {"format": "%Y-%m-%d", "date": "$timestamp"}}, "count": {"$sum": 1}}},
        {"$sort": {"_id": 1}},
    ]
    daily_docs = await inbox.db.messages.aggregate(daily_pipeline).to_list(200)
    daily = [{"date": d["_id"], "count": d["count"]} for d in daily_docs]

    # Top senders
    sender_pipeline = [
        {"$match": {"chat_id": chat_id, "from_me": False, "timestamp": {"$gte": since}}},
        {"$group": {"_id": {"name": "$sender_name", "number": "$sender_number"}, "n": {"$sum": 1}}},
        {"$sort": {"n": -1}},
        {"$limit": 10},
    ]
    sender_docs = await inbox.db.messages.aggregate(sender_pipeline).to_list(10)
    top_senders = [
        {
            "name": d["_id"].get("name") or d["_id"].get("number") or "",
            "number": d["_id"].get("number") or "",
            "messages": d["n"],
        }
        for d in sender_docs
    ]
    return {
        "group": chat.get("name") or "",
        "days": days,
        "total_messages": total,
        "incoming": incoming,
        "outgoing": total - incoming,
        "daily_volume": daily,
        "top_senders": top_senders,
    }
