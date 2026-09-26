import re
from datetime import datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import case, func
from sqlalchemy.orm import Session

from app.api.auth import get_current_agent
from app.db.session import get_db
from app.models.agent import Agent
from app.models.contact import LID_SUFFIX, Contact, ContactLabel
from app.models.phone import Phone
from app.schemas.contact import (
    ContactBulkIds, ContactBulkLabel, ContactCreate, ContactOut, ContactUpdate,
)
from app.services.access import is_admin, require_admin

router = APIRouter(prefix="/contacts", tags=["contacts"])

MASKED_PHONE = "***masked***"
PICTURE_TTL = timedelta(hours=24)


def mask_phone(_number: str | None) -> str:
    return MASKED_PHONE


def _digits(s: str | None) -> str:
    return re.sub(r"\D", "", s or "")


def _our_numbers(db: Session) -> set[str]:
    """Digits of every WhatsApp number connected to this workspace."""
    return {d for (n,) in db.query(Phone.phone_number).all() if (d := _digits(n))}


def _labels_for(db: Session, ids: list[int]) -> dict[int, list[int]]:
    out: dict[int, list[int]] = {i: [] for i in ids}
    if ids:
        for cid, lid in db.query(ContactLabel.contact_id, ContactLabel.label_id).filter(
            ContactLabel.contact_id.in_(ids)
        ):
            out.setdefault(cid, []).append(lid)
    return out


def _contact_out(db: Session, c: Contact, agent: Agent, with_labels: bool = True,
                 labels: list[int] | None = None, ours: set[str] | None = None) -> dict:
    """Serialize a contact; masked numbers are hidden from non-admins."""
    d = ContactOut.model_validate(c).model_dump()
    if labels is not None:
        d["labels"] = labels
    elif with_labels:
        d["labels"] = [
            r[0] for r in db.query(ContactLabel.label_id).filter(ContactLabel.contact_id == c.id).all()
        ]
    has_phone = c.has_phone
    d["has_phone"] = has_phone
    if not has_phone:
        d["phone_number"] = None
    ours = _our_numbers(db) if ours is None else ours
    d["is_internal"] = has_phone and c.phone_number in ours
    hidden = c.is_masked and not is_admin(agent)
    if hidden and has_phone:
        d["phone_number"] = mask_phone(c.phone_number)
    # Raw WhatsApp ids embed the number — admins only
    if not is_admin(agent):
        d["wid"] = None
        d["lid"] = None
    d["picture_url"] = c.picture_url or None
    return d


def _like_escape(value: str) -> str:
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def _parse_ids(raw: str | None) -> list[int]:
    return [int(x) for x in (raw or "").split(",") if x.strip().isdigit()][:100]


async def _chat_wids_for_phone(phone_id: int) -> list[str]:
    from app.db.mongo import get_mongo_db
    return await get_mongo_db().chats.distinct(
        "chat_wid", {"phone_id": phone_id, "is_group": {"$ne": True}}
    )


async def _chat_links(db: Session, agent: Agent, contacts: list[Contact]) -> dict[int, dict]:
    """contact id → {chat_id, picture_url} of its 1:1 chat on a phone the agent can see."""
    from app.core.permissions import allowed_phone_ids
    from app.db.mongo import get_mongo_db
    wid_to_contact: dict[str, int] = {}
    for c in contacts:
        for w in (c.wid, c.lid, f"{c.phone_number}@c.us" if c.has_phone else None):
            if w:
                wid_to_contact[w] = c.id
    if not wid_to_contact:
        return {}
    q: dict = {"chat_wid": {"$in": list(wid_to_contact)}, "is_group": {"$ne": True}}
    allowed = allowed_phone_ids(db, agent)
    if allowed is not None:
        q["phone_id"] = {"$in": allowed}
    out: dict[int, dict] = {}
    try:
        cursor = get_mongo_db().chats.find(
            q, {"id": 1, "chat_wid": 1, "picture_url": 1, "last_message_at": 1}
        ).sort("last_message_at", -1)
        async for ch in cursor:
            cid = wid_to_contact.get(ch.get("chat_wid"))
            if cid is not None and cid not in out:
                out[cid] = {"chat_id": ch.get("id"), "picture_url": ch.get("picture_url") or None}
    except Exception:  # Mongo down — the table still renders without chat links
        return {}
    return out


@router.get("")
async def list_contacts(
    search: str | None = None,
    label_id: int | None = None,
    label_ids: str | None = Query(None, description="Comma-separated label ids (any)"),
    type: str | None = Query(None, pattern="^(internal|external)$"),
    has_phone: bool | None = None,
    phone_id: int | None = Query(None, description="Only contacts with a chat on this number of ours"),
    limit: int = 50,
    offset: int = 0,
    db: Session = Depends(get_db),
    agent: Agent = Depends(get_current_agent),
):
    """Paged contacts: `{items, total, limit, offset}` sorted by name."""
    q = db.query(Contact)
    admin = is_admin(agent)
    if search and search.strip():
        s = search.strip()
        pattern = f"%{_like_escape(s)}%"
        text_match = Contact.name.ilike(pattern, escape="\\") | Contact.pushname.ilike(pattern, escape="\\")
        digits = _digits(s)
        if digits and len(digits) >= 3:
            num_match = Contact.phone_number.like(f"%{digits}%") & ~Contact.phone_number.like(f"%{LID_SUFFIX}")
            if not admin:
                # Hidden digits of masked contacts must not be searchable
                num_match = num_match & (Contact.is_masked == False)  # noqa: E712
            q = q.filter(text_match | num_match)
        else:
            q = q.filter(text_match)
    ids = _parse_ids(label_ids) + ([label_id] if label_id else [])
    if ids:
        sub = db.query(ContactLabel.contact_id).filter(ContactLabel.label_id.in_(ids))
        q = q.filter(Contact.id.in_(sub))
    if has_phone is not None:
        lid_row = Contact.phone_number.like(f"%{LID_SUFFIX}")
        q = q.filter(~lid_row if has_phone else lid_row)
    if type:
        ours = list(_our_numbers(db)) or ["-"]
        q = q.filter(Contact.phone_number.in_(ours) if type == "internal" else ~Contact.phone_number.in_(ours))
    if phone_id:
        from app.services.access import assert_phone_access
        assert_phone_access(db, agent, phone_id)
        wids = await _chat_wids_for_phone(phone_id)
        numbers = [w.split("@")[0] for w in wids if w.endswith("@c.us")]
        lids = [w for w in wids if w.endswith("@lid")]
        q = q.filter(
            Contact.wid.in_(wids or ["-"]) | Contact.lid.in_(lids or ["-"])
            | Contact.phone_number.in_(numbers or ["-"])
        )

    total = q.with_entities(func.count(Contact.id)).scalar() or 0
    display = func.coalesce(func.nullif(Contact.name, ""), Contact.pushname)
    rows = (
        q.order_by(case((display.is_(None), 1), else_=0), display.asc(), Contact.id.asc())
        .offset(max(0, offset)).limit(max(1, min(limit, 200))).all()
    )
    labels = _labels_for(db, [c.id for c in rows])
    ours_set = _our_numbers(db)
    links = await _chat_links(db, agent, rows)
    items = []
    for c in rows:
        d = _contact_out(db, c, agent, labels=labels.get(c.id, []), ours=ours_set)
        link = links.get(c.id) or {}
        d["chat_id"] = link.get("chat_id")
        d["picture_url"] = d["picture_url"] or link.get("picture_url")
        items.append(d)
    return {"items": items, "total": total, "limit": limit, "offset": max(0, offset)}


@router.post("/sync")
async def sync_contacts(
    db: Session = Depends(get_db),
    agent: Agent = Depends(get_current_agent),
):
    """Import contacts from every connected WhatsApp number (read-only on WhatsApp)."""
    require_admin(agent, "Only admins can sync contacts")
    from app.services.contact_sync import sync_all
    stats = await sync_all(db)
    return {**stats.as_dict(), "total": db.query(func.count(Contact.id)).scalar() or 0}


@router.post("/bulk-delete")
def bulk_delete(
    req: ContactBulkIds,
    db: Session = Depends(get_db),
    agent: Agent = Depends(get_current_agent),
):
    require_admin(agent, "Only admins can delete contacts in bulk")
    ids = list(dict.fromkeys(req.ids))[:1000]
    if not ids:
        return {"deleted": 0}
    db.query(ContactLabel).filter(ContactLabel.contact_id.in_(ids)).delete(synchronize_session=False)
    n = db.query(Contact).filter(Contact.id.in_(ids)).delete(synchronize_session=False)
    db.commit()
    return {"deleted": n}


@router.post("/bulk-label")
def bulk_label(
    req: ContactBulkLabel,
    db: Session = Depends(get_db),
    agent: Agent = Depends(get_current_agent),
):
    from app.models.label import Label
    if not db.query(Label).filter(Label.id == req.label_id).first():
        raise HTTPException(404, "Label not found")
    ids = [r[0] for r in db.query(Contact.id).filter(Contact.id.in_(list(dict.fromkeys(req.ids))[:1000]))]
    have = {r[0] for r in db.query(ContactLabel.contact_id).filter(
        ContactLabel.label_id == req.label_id, ContactLabel.contact_id.in_(ids or [-1]))}
    added = 0
    for cid in ids:
        if cid not in have:
            db.add(ContactLabel(contact_id=cid, label_id=req.label_id))
            added += 1
    db.commit()
    return {"added": added}


@router.post("", response_model=dict, status_code=201)
def create_contact(
    req: ContactCreate,
    db: Session = Depends(get_db),
    agent: Agent = Depends(get_current_agent),
):
    if req.is_masked:
        require_admin(agent, "Only admins can mask contacts")
    number = _digits(req.phone_number)
    if not 7 <= len(number) <= 15:
        raise HTTPException(422, "Enter a valid phone number with country code")
    existing = db.query(Contact).filter(Contact.phone_number == number).first()
    if existing:
        raise HTTPException(400, "Contact already exists")
    data = req.model_dump()
    data["phone_number"] = number
    c = Contact(**data, wid=f"{number}@c.us", source="manual")
    db.add(c)
    db.commit()
    db.refresh(c)
    return _contact_out(db, c, agent)


@router.get("/{contact_id}", response_model=dict)
async def get_contact(
    contact_id: int,
    db: Session = Depends(get_db),
    agent: Agent = Depends(get_current_agent),
):
    c = db.query(Contact).filter(Contact.id == contact_id).first()
    if not c:
        raise HTTPException(404, "Contact not found")
    d = _contact_out(db, c, agent)
    link = (await _chat_links(db, agent, [c])).get(c.id) or {}
    d["chat_id"] = link.get("chat_id")
    d["picture_url"] = d["picture_url"] or link.get("picture_url")
    return d


@router.get("/{contact_id}/picture")
async def contact_picture(
    contact_id: int,
    refresh: bool = False,
    db: Session = Depends(get_db),
    agent: Agent = Depends(get_current_agent),
):
    """WhatsApp profile picture URL, looked up from WAHA at most once a day."""
    c = db.query(Contact).filter(Contact.id == contact_id).first()
    if not c:
        raise HTTPException(404, "Contact not found")
    checked = c.picture_checked_at
    if not refresh and checked and datetime.utcnow() - checked < PICTURE_TTL:
        return {"url": c.picture_url or None}
    wa_id = c.wid or (f"{c.phone_number}@c.us" if c.has_phone else None) or c.lid
    phone = db.query(Phone).filter(Phone.is_active.is_(True), Phone.waha_status == "WORKING").first()
    if not wa_id or not phone:
        return {"url": c.picture_url or None}
    from urllib.parse import urlsplit
    from app.services.waha_service import WAHAService
    url = await WAHAService.from_phone(phone).get_contact_picture(wa_id)
    # Only WhatsApp's own CDN over https ever reaches an <img src>
    try:
        parts = urlsplit(url or "")
        ok = parts.scheme == "https" and (parts.hostname or "").endswith(".whatsapp.net")
    except ValueError:
        ok = False
    c.picture_url = (url if ok else None)
    c.picture_checked_at = datetime.utcnow()
    db.commit()
    return {"url": c.picture_url}


@router.patch("/{contact_id}", response_model=dict)
def update_contact(
    contact_id: int,
    req: ContactUpdate,
    db: Session = Depends(get_db),
    agent: Agent = Depends(get_current_agent),
):
    c = db.query(Contact).filter(Contact.id == contact_id).first()
    if not c:
        raise HTTPException(404, "Contact not found")
    changes = req.model_dump(exclude_none=True)
    if "is_masked" in changes and changes["is_masked"] != c.is_masked:
        require_admin(agent, "Only admins can change contact masking")
    for k, v in changes.items():
        setattr(c, k, v)
    db.commit()
    db.refresh(c)
    return _contact_out(db, c, agent)


@router.delete("/{contact_id}", status_code=204)
def delete_contact(contact_id: int, db: Session = Depends(get_db)):
    c = db.query(Contact).filter(Contact.id == contact_id).first()
    if not c:
        raise HTTPException(404, "Contact not found")
    db.query(ContactLabel).filter(ContactLabel.contact_id == contact_id).delete(synchronize_session=False)
    db.delete(c)
    db.commit()


# ── Contact labels ────────────────────────────────────────────────────────────

@router.get("/{contact_id}/labels")
def get_contact_labels(contact_id: int, db: Session = Depends(get_db)):
    from app.models.label import Label
    rows = (
        db.query(Label)
        .join(ContactLabel, ContactLabel.label_id == Label.id)
        .filter(ContactLabel.contact_id == contact_id)
        .all()
    )
    return [{"id": l.id, "name": l.name, "color": l.color} for l in rows]


@router.post("/{contact_id}/labels/{label_id}", status_code=201)
def add_contact_label(contact_id: int, label_id: int, db: Session = Depends(get_db)):
    if not db.query(Contact).filter(Contact.id == contact_id).first():
        raise HTTPException(404, "Contact not found")
    exists = db.query(ContactLabel).filter(
        ContactLabel.contact_id == contact_id, ContactLabel.label_id == label_id
    ).first()
    if not exists:
        db.add(ContactLabel(contact_id=contact_id, label_id=label_id))
        db.commit()
    return {"ok": True}


@router.delete("/{contact_id}/labels/{label_id}", status_code=204)
def remove_contact_label(contact_id: int, label_id: int, db: Session = Depends(get_db)):
    row = db.query(ContactLabel).filter(
        ContactLabel.contact_id == contact_id, ContactLabel.label_id == label_id
    ).first()
    if row:
        db.delete(row)
        db.commit()
