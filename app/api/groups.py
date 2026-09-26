import logging
import re
import time
from datetime import datetime, timedelta, timezone
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.api.auth import get_current_agent
from app.db.session import get_db
from app.models.agent import Agent
from app.models.phone import Phone
from app.services.access import agent_can_access_chat, get_accessible_chat, is_admin, require_admin
from app.services.activity_service import log_activity
from app.services.mongo_chat_service import MongoInboxService
from app.services.waha_service import WAHAError, WAHAService

logger = logging.getLogger(__name__)
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


# ── Group info & members (read from WAHA, cached ~5 min) ─────────────────── #
#
# Reads are open to anyone who can access the chat. Every change (members,
# invite link, subject/description/settings) needs a CRM admin AND our
# WhatsApp number being an admin of the group — WhatsApp itself refuses the
# rest, this just says so up front.

_TTL = 300.0
_PIC_TTL = 24 * 3600.0
_info_cache: dict[tuple[int, str], tuple[float, dict]] = {}
_members_cache: dict[tuple[int, str], tuple[float, list[dict]]] = {}
_me_cache: dict[int, tuple[float, set[str]]] = {}
_pic_cache: dict[tuple[int, str], tuple[float, str | None]] = {}
_PID_RE = re.compile(r"^\d{5,20}@(c\.us|lid)$")
_INVITE_RE = re.compile(r"^[A-Za-z0-9_-]{6,64}$")


def _fresh(ts: float, ttl: float = _TTL) -> bool:
    return time.monotonic() - ts < ttl


def _invalidate(phone_id: int, wid: str) -> None:
    _info_cache.pop((phone_id, wid), None)
    _members_cache.pop((phone_id, wid), None)


async def _group_ctx(db: Session, agent: Agent, chat_id: int) -> tuple[dict, Phone, WAHAService]:
    chat = await get_accessible_chat(db, agent, chat_id)
    if not chat.get("is_group"):
        raise HTTPException(404, "Group not found")
    phone = db.query(Phone).filter(Phone.id == chat["phone_id"]).first()
    if not phone:
        raise HTTPException(404, "Phone not found")
    return chat, phone, WAHAService.from_phone(phone)


async def _my_ids(phone: Phone, waha: WAHAService) -> set[str]:
    """Every id our own number can appear under in a participant list."""
    hit = _me_cache.get(phone.id)
    if hit and _fresh(hit[0]):
        return hit[1]
    try:
        me = await waha.get_me()
    except Exception:
        me = {}
    ids: set[str] = set()
    for key in ("id", "lid"):
        v = me.get(key) if isinstance(me, dict) else None
        if isinstance(v, dict):
            v = v.get("_serialized")
        if v:
            ids.add(str(v))
    digits = re.sub(r"\D", "", phone.phone_number or "")
    if digits:
        ids.add(f"{digits}@c.us")
    if me:
        _me_cache[phone.id] = (time.monotonic(), ids)
    return ids


def _norm_participant(p: Any) -> dict | None:
    """WAHA participants/v2 `{id, pn, role}` or v1 `{id, isAdmin, isSuperAdmin}`."""
    if not isinstance(p, dict):
        return None
    pid = p.get("id")
    if isinstance(pid, dict):
        pid = pid.get("_serialized") or ""
    pid = str(pid or "")
    if not pid:
        return None
    pn = p.get("pn")
    if isinstance(pn, dict):
        pn = pn.get("_serialized") or ""
    if not pn and pid.endswith("@c.us"):
        pn = pid
    role = p.get("role")
    if not role:
        role = ("superadmin" if p.get("isSuperAdmin")
                else "admin" if (p.get("isAdmin") or p.get("admin")) else "participant")
    if role == "left":
        return None
    number = str(pn).split("@")[0] if pn else ""
    return {
        "id": pid,
        # Digits of the phone number; LID-only members fall back to the LID
        # digits (has_number=False) so older callers still get a value.
        "number": number or pid.split("@")[0],
        "has_number": bool(number),
        "role": role,
        "is_admin": role in ("admin", "superadmin"),
        "is_super_admin": role == "superadmin",
    }


async def _load_members(chat: dict, phone: Phone, waha: WAHAService, refresh: bool = False) -> tuple[list[dict], bool]:
    key = (phone.id, chat["chat_wid"])
    hit = _members_cache.get(key)
    if hit and not refresh and _fresh(hit[0]):
        return hit[1], True
    raw = await waha.get_group_participants_v2(chat["chat_wid"])
    ok = raw is not None
    if raw is None:
        raw, ok = await waha.get_group_participants_with_status(chat["chat_wid"])
    members = [m for m in map(_norm_participant, raw or []) if m]
    if ok:
        _members_cache[key] = (time.monotonic(), members)
    return members, ok


def _find_me(members: list[dict], my_ids: set[str]) -> dict | None:
    for m in members:
        if m["id"] in my_ids or (m["has_number"] and f"{m['number']}@c.us" in my_ids):
            return m
    return None


def _ts_iso(v: Any) -> str | None:
    try:
        n = int(v)
    except (TypeError, ValueError):
        return None
    if n <= 0:
        return None
    if n > 10**12:          # milliseconds
        n //= 1000
    return datetime.fromtimestamp(n, timezone.utc).replace(tzinfo=None).isoformat()


async def _load_info(chat: dict, phone: Phone, waha: WAHAService, refresh: bool = False) -> dict:
    key = (phone.id, chat["chat_wid"])
    hit = _info_cache.get(key)
    if hit and not refresh and _fresh(hit[0]):
        return hit[1]
    raw = await waha.get_group_info(chat["chat_wid"]) if phone.waha_status == "WORKING" else {}
    meta = raw.get("groupMetadata") if isinstance(raw.get("groupMetadata"), dict) else raw
    desc = meta.get("desc") if "desc" in meta else meta.get("description", raw.get("description"))
    if isinstance(desc, dict):          # some engines nest {id, body}
        desc = desc.get("body") or desc.get("desc") or ""
    owner = meta.get("owner")
    if isinstance(owner, dict):
        owner = owner.get("_serialized") or ""
    subject = meta.get("subject") or raw.get("name") or raw.get("subject") or ""
    info = {
        "available": bool(raw),
        "subject": str(subject or ""),
        "description": str(desc or ""),
        "created_at": _ts_iso(meta.get("creation") or raw.get("creation")),
        "owner": str(owner or ""),
        "size": meta.get("size") if isinstance(meta.get("size"), int) else None,
        "messages_admin_only": bool(meta.get("announce")) if "announce" in meta else None,
        "info_admin_only": bool(meta.get("restrict")) if "restrict" in meta else None,
    }
    if raw:
        _info_cache[key] = (time.monotonic(), info)
    return info


async def _sender_names(chat_id: int) -> dict[str, str]:
    """number/LID digits → the push name last seen on a message in this group."""
    names: dict[str, str] = {}
    try:
        cursor = MongoInboxService().db.messages.aggregate([
            {"$match": {"chat_id": chat_id, "from_me": False, "sender_number": {"$nin": [None, ""]}}},
            {"$sort": {"timestamp": -1}},
            {"$group": {"_id": "$sender_number", "name": {"$first": "$sender_name"}}},
            {"$limit": 10000},
        ])
        async for d in cursor:
            name = (d.get("name") or "").strip()
            num = str(d.get("_id") or "")
            if name and num and re.sub(r"[\s+\-()]", "", name) != num:
                names[num] = name
    except Exception as exc:
        logger.warning("group sender names lookup failed: %s", exc)
    return names


def _access(agent: Agent, phone: Phone, me: dict | None) -> dict:
    working = phone.waha_status == "WORKING"
    me_admin = bool(me and me["is_admin"])
    return {
        "phone_working": working,
        "crm_admin": is_admin(agent),
        "me_admin": me_admin,
        "can_manage": working and me_admin and is_admin(agent),
    }


@router.get("/{chat_id}/participants")
async def group_participants(
    chat_id: int,
    refresh: bool = False,
    db: Session = Depends(get_db),
    agent: Agent = Depends(get_current_agent),
):
    chat, phone, waha = await _group_ctx(db, agent, chat_id)
    members, api_ok = await _load_members(chat, phone, waha, refresh)
    me = _find_me(members, await _my_ids(phone, waha)) if api_ok else None
    names = await _sender_names(chat_id) if members else {}
    result = [{
        **m,
        "name": names.get(m["number"]) or names.get(m["id"].split("@")[0]) or "",
        "is_me": m is me,
    } for m in members]
    # Admins first (owner on top), then everyone else in WhatsApp's order
    result.sort(key=lambda m: (not m["is_me"], not m["is_super_admin"], not m["is_admin"]))
    return {
        "group": chat.get("name") or "",
        "count": len(result),
        "participants": result,
        "api_available": api_ok,
        "is_member": (me is not None) if api_ok else None,
        **_access(agent, phone, me),
    }


@router.get("/{chat_id}/info")
async def group_info(
    chat_id: int,
    refresh: bool = False,
    db: Session = Depends(get_db),
    agent: Agent = Depends(get_current_agent),
):
    """Subject, description, creation time and security settings of a group,
    plus whether our number is a member / admin (→ what the UI may offer)."""
    chat, phone, waha = await _group_ctx(db, agent, chat_id)
    info = await _load_info(chat, phone, waha, refresh)
    members, api_ok = (await _load_members(chat, phone, waha, refresh)
                       if phone.waha_status == "WORKING" else ([], False))
    me = _find_me(members, await _my_ids(phone, waha)) if api_ok else None
    return {
        **info,
        "count": len(members) if api_ok else info.get("size"),
        "is_member": (me is not None) if api_ok else None,
        **_access(agent, phone, me),
    }


@router.get("/{chat_id}/members/picture")
async def member_picture(
    chat_id: int,
    id: str = Query(..., max_length=40),
    db: Session = Depends(get_db),
    agent: Agent = Depends(get_current_agent),
):
    """Profile picture of one group member (WhatsApp CDN URL), cached a day."""
    if not _PID_RE.match(id):
        raise HTTPException(400, "Invalid member id")
    chat, phone, waha = await _group_ctx(db, agent, chat_id)
    key = (phone.id, id)
    hit = _pic_cache.get(key)
    if hit and _fresh(hit[0], _PIC_TTL):
        return {"url": hit[1]}
    if phone.waha_status != "WORKING":
        return {"url": None}
    url = await waha.get_contact_picture(id)
    from urllib.parse import urlsplit
    try:
        parts = urlsplit(url or "")
        ok = parts.scheme == "https" and (parts.hostname or "").endswith(".whatsapp.net")
    except ValueError:
        ok = False
    url = url if ok else None
    if len(_pic_cache) > 20000:
        _pic_cache.clear()
    _pic_cache[key] = (time.monotonic(), url)
    return {"url": url}


async def _manage_ctx(db: Session, agent: Agent, chat_id: int):
    """Chat, phone, WAHA client, fresh member list and our own member entry —
    only when a CRM admin acts through a number that is a group admin."""
    require_admin(agent, "Only admins can manage WhatsApp groups")
    chat, phone, waha = await _group_ctx(db, agent, chat_id)
    if phone.waha_status != "WORKING":
        raise HTTPException(409, "This WhatsApp number is not connected")
    members, ok = await _load_members(chat, phone, waha, refresh=True)
    if not ok:
        raise HTTPException(502, "Could not load the group's members from WhatsApp")
    me = _find_me(members, await _my_ids(phone, waha))
    if not me:
        raise HTTPException(403, "Your WhatsApp number is not a member of this group")
    if not me["is_admin"]:
        raise HTTPException(403, "Your WhatsApp number is not an admin of this group")
    return chat, phone, waha, members, me


def _waha_fail(exc: WAHAError) -> HTTPException:
    return HTTPException(502, f"WhatsApp refused: {exc}")


class AddMembersRequest(BaseModel):
    numbers: list[str] = Field(..., min_length=1, max_length=50)


@router.post("/{chat_id}/members/add")
async def add_members(
    chat_id: int,
    req: AddMembersRequest,
    db: Session = Depends(get_db),
    agent: Agent = Depends(get_current_agent),
):
    chat, phone, waha, members, _me = await _manage_ctx(db, agent, chat_id)
    digits, invalid = [], []
    for n in req.numbers:
        d = re.sub(r"\D", "", n or "")
        if not d:
            continue
        (digits if 7 <= len(d) <= 15 else invalid).append(d)
    if invalid:
        raise HTTPException(400, f"Invalid number(s): {', '.join(invalid[:5])}")
    digits = list(dict.fromkeys(digits))
    if not digits:
        raise HTTPException(400, "Enter at least one phone number")
    existing = {m["number"] for m in members if m["has_number"]}
    already = [d for d in digits if d in existing]
    to_add = [d for d in digits if d not in existing]
    result: Any = None
    if to_add:
        try:
            result = await waha.group_participants_action(chat["chat_wid"], "add", [f"{d}@c.us" for d in to_add])
        except WAHAError as exc:
            raise _waha_fail(exc)
        _invalidate(phone.id, chat["chat_wid"])
        log_activity(
            db, "group_participants_added", entity_type="chat", entity_id=chat_id, agent_id=agent.id,
            description=f"Added {len(to_add)} member(s) to '{chat.get('name') or ''}'",
        )
    return {"ok": True, "requested": len(to_add), "already_members": already, "result": result}


class MemberActionRequest(BaseModel):
    participants: list[str] = Field(..., min_length=1, max_length=50)


_ACTION_LOG = {
    "remove": ("group_participants_removed", "Removed {n} member(s) from '{g}'"),
    "promote": ("group_participants_promoted", "Made {n} member(s) admin in '{g}'"),
    "demote": ("group_participants_demoted", "Dismissed {n} admin(s) in '{g}'"),
}


@router.post("/{chat_id}/members/{action}")
async def member_action(
    chat_id: int,
    action: Literal["remove", "promote", "demote"],
    req: MemberActionRequest,
    db: Session = Depends(get_db),
    agent: Agent = Depends(get_current_agent),
):
    chat, phone, waha, members, me = await _manage_ctx(db, agent, chat_id)
    by_id = {m["id"]: m for m in members}
    by_num = {m["number"]: m for m in members if m["has_number"]}
    targets: list[dict] = []
    for pid in dict.fromkeys(req.participants):
        m = by_id.get(pid) or by_num.get(re.sub(r"\D", "", pid.split("@")[0]))
        if not m:
            raise HTTPException(400, "Not a member of this group")
        if m is me:
            raise HTTPException(400, "You can't change your own number here")
        if m["is_super_admin"] and action in ("remove", "demote"):
            raise HTTPException(400, "The group owner can't be removed or dismissed")
        if action == "promote" and m["is_admin"]:
            raise HTTPException(400, "Already an admin")
        if action == "demote" and not m["is_admin"]:
            raise HTTPException(400, "Not an admin")
        targets.append(m)
    try:
        result = await waha.group_participants_action(chat["chat_wid"], action, [m["id"] for m in targets])
    except WAHAError as exc:
        raise _waha_fail(exc)
    _invalidate(phone.id, chat["chat_wid"])
    act, text = _ACTION_LOG[action]
    log_activity(db, act, entity_type="chat", entity_id=chat_id, agent_id=agent.id,
                 description=text.format(n=len(targets), g=chat.get("name") or ""))
    return {"ok": True, "count": len(targets), "result": result}


@router.post("/{chat_id}/invite-link")
async def invite_link(
    chat_id: int,
    db: Session = Depends(get_db),
    agent: Agent = Depends(get_current_agent),
):
    """The group's invite link (WhatsApp creates one if the group has none)."""
    chat, phone, waha, _members, _me = await _manage_ctx(db, agent, chat_id)
    try:
        code = await waha.get_group_invite_code(chat["chat_wid"])
    except WAHAError as exc:
        raise _waha_fail(exc)
    code = code.rsplit("/", 1)[-1]
    if not _INVITE_RE.match(code):
        raise HTTPException(502, "WhatsApp returned no invite code")
    log_activity(db, "group_invite_link", entity_type="chat", entity_id=chat_id, agent_id=agent.id,
                 description=f"Fetched the invite link of '{chat.get('name') or ''}'")
    link = f"https://chat.whatsapp.com/{code}"
    return {"code": code, "link": link, "invite_message": _invite_message(db, chat, link)}


def _invite_message(db: Session, chat: dict, link: str) -> str | None:
    """Settings → Group Settings → "Enable Custom Group Invite Message": the
    text to share with the link ({{group_name}}, {{invite_link}}). None when
    off — the client then shares the bare link. Nothing is sent from here."""
    from app.models.org_config import get_org_config
    cfg = get_org_config(db)["groups"]
    if not cfg.get("invite_message_enabled"):
        return None
    text = (cfg.get("invite_template") or "").replace("{{group_name}}", chat.get("name") or "our group")
    if "{{invite_link}}" in text:
        text = text.replace("{{invite_link}}", link)
    else:
        text = f"{text}\n{link}".strip()
    return text.strip() or None


class GroupSettingsRequest(BaseModel):
    subject: str | None = Field(None, max_length=100)
    description: str | None = Field(None, max_length=2048)
    messages_admin_only: bool | None = None
    info_admin_only: bool | None = None


@router.patch("/{chat_id}/settings")
async def update_group_settings(
    chat_id: int,
    req: GroupSettingsRequest,
    db: Session = Depends(get_db),
    agent: Agent = Depends(get_current_agent),
):
    changes = req.model_dump(exclude_none=True)
    if not changes:
        raise HTTPException(400, "Nothing to change")
    if "subject" in changes:
        changes["subject"] = changes["subject"].strip()
        if not changes["subject"]:
            raise HTTPException(400, "Group name can't be empty")
    chat, phone, waha, _members, _me = await _manage_ctx(db, agent, chat_id)
    wid = chat["chat_wid"]
    done: list[str] = []
    try:
        if "subject" in changes:
            await waha.set_group_subject(wid, changes["subject"])
            await MongoInboxService().update_chat(chat_id, name=changes["subject"])
            done.append("name")
        if "description" in changes:
            await waha.set_group_description(wid, changes["description"])
            done.append("description")
        if "messages_admin_only" in changes:
            await waha.set_group_admin_only(wid, "messages", changes["messages_admin_only"])
            done.append("who can send messages")
        if "info_admin_only" in changes:
            await waha.set_group_admin_only(wid, "info", changes["info_admin_only"])
            done.append("who can edit group info")
    except WAHAError as exc:
        _invalidate(phone.id, wid)
        raise _waha_fail(exc)
    finally:
        if done:
            log_activity(db, "group_settings_changed", entity_type="chat", entity_id=chat_id, agent_id=agent.id,
                         description=f"Changed {', '.join(done)} of '{chat.get('name') or ''}'")
    _invalidate(phone.id, wid)
    info = await _load_info(chat, phone, waha, refresh=True)
    return {"ok": True, **info}



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
        if not agent_can_access_chat(db, agent, chat) or not chat.get("is_group"):
            results.append({"chat_id": cid, "ok": False, "error": "Not a group"})
            continue
        phone = db.query(Phone).filter(Phone.id == chat["phone_id"]).first()
        if not phone:
            results.append({"chat_id": cid, "ok": False, "error": "Phone missing"})
            continue
        waha = WAHAService.from_phone(phone)
        ok = await waha.add_group_participants(chat["chat_wid"], wids)
        results.append({"chat_id": cid, "group": chat.get("name") or "", "ok": ok})
    log_activity(
        db, "group_participants_added", entity_type="chat", agent_id=agent.id,
        description=f"Added {len(wids)} participant(s) to {sum(1 for r in results if r['ok'])} group(s)",
    )
    return {"results": results}


def _parse_day(v: str | None, end: bool = False) -> datetime | None:
    """'YYYY-MM-DD' or full ISO → naive UTC datetime. A bare date used as the
    upper bound means "through the end of that day"."""
    if not v:
        return None
    try:
        dt = datetime.fromisoformat(v.replace("Z", "+00:00"))
    except ValueError:
        raise HTTPException(400, f"Invalid date: {v}")
    if dt.tzinfo is not None:
        from datetime import timezone
        dt = dt.astimezone(timezone.utc).replace(tzinfo=None)
    if end and len(v) <= 10:
        dt += timedelta(days=1)
    return dt


@router.get("/{chat_id}/analytics")
async def group_analytics(
    chat_id: int,
    days: int = 30,
    from_: str | None = Query(None, alias="from"),
    to: str | None = None,
    db: Session = Depends(get_db),
    agent: Agent = Depends(get_current_agent),
):
    """Group activity for a window: message / reaction / join / exit counts,
    daily message volume, top senders, in/out split.

    Window: `from`/`to` (ISO date or datetime, UTC) or the last `days` days.
    Reactions come from `message_reactions`, joins/exits from `group_events`
    (both filled by WAHA webhooks); a metric is null (not tracked) until
    that feed has stored anything for this number.
    """
    inbox = MongoInboxService()
    chat = await get_accessible_chat(db, agent, chat_id)
    if not chat.get("is_group"):
        raise HTTPException(404, "Group not found")
    until = _parse_day(to, end=True) or datetime.utcnow()
    since = _parse_day(from_) or (until - timedelta(days=max(1, min(days, 365))))
    if since >= until:
        raise HTTPException(400, "'from' must be before 'to'")
    if until - since > timedelta(days=366):
        raise HTTPException(400, "Date range is limited to one year")
    window = {"$gte": since, "$lt": until}

    total = await inbox.db.messages.count_documents({"chat_id": chat_id, "timestamp": window})
    incoming = await inbox.db.messages.count_documents({"chat_id": chat_id, "from_me": False, "timestamp": window})

    # Daily volume
    daily_pipeline = [
        {"$match": {"chat_id": chat_id, "timestamp": window}},
        {"$group": {"_id": {"$dateToString": {"format": "%Y-%m-%d", "date": "$timestamp"}}, "count": {"$sum": 1}}},
        {"$sort": {"_id": 1}},
    ]
    daily_docs = await inbox.db.messages.aggregate(daily_pipeline).to_list(400)
    daily = [{"date": d["_id"], "count": d["count"]} for d in daily_docs]

    # Top senders
    sender_pipeline = [
        {"$match": {"chat_id": chat_id, "from_me": False, "timestamp": window}},
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

    phone_id = chat["phone_id"]
    reactions_since = await inbox.tracking_since("message_reactions", phone_id)
    members_since = await inbox.tracking_since("group_events", phone_id)
    reactions = await inbox.count_reactions(chat_id, since, until) if reactions_since else None
    joined = left = removed = exited = None
    if members_since:
        joined = await inbox.count_group_events(chat_id, ["join", "add"], since, until)
        left = await inbox.count_group_events(chat_id, ["leave"], since, until)
        removed = await inbox.count_group_events(chat_id, ["remove"], since, until)
        exited = left + removed

    return {
        "group": chat.get("name") or "",
        "days": days,
        "from": since.isoformat(),
        "to": until.isoformat(),
        "total_messages": total,
        "incoming": incoming,
        "outgoing": total - incoming,
        "messages": total,
        "reactions": reactions,
        "members_joined": joined,
        "members_exited": exited,       # left + removed
        "members_left": left,
        "members_removed": removed,
        # When each event feed started (null = never received on this number)
        "tracked_since": {
            "reactions": reactions_since.isoformat() if reactions_since else None,
            "members": members_since.isoformat() if members_since else None,
        },
        "daily_volume": daily,
        "top_senders": top_senders,
    }
