"""Populate the contacts table from WhatsApp.

Sources (all read-only on the WhatsApp side):
- WAHA `GET /api/contacts/all` for every connected (WORKING) phone, with
  `GET /api/{session}/lids` to resolve LID-only contacts to phone numbers;
- the 1:1 chats already stored in MongoDB (covers phones whose WAHA session is
  down, and chats that WhatsApp addresses by LID);
- incoming 1:1 messages via `upsert_contact_from_message` (call from the
  webhook handler, see its docstring).

Upserts are idempotent: a contact is matched by phone number, WID or LID and
only WhatsApp-sourced fields are refreshed. A name typed in the CRM is never
overwritten; the WhatsApp saved-contact name only fills an empty name.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Iterable

from sqlalchemy.orm import Session

from app.models.contact import LID_SUFFIX, Contact, ContactLabel

logger = logging.getLogger(__name__)

PAGE = 1000
MAX_PAGES = 100          # 100k contacts per phone is plenty
COMMIT_EVERY = 500

# WhatsApp shows unsaved users as their formatted number ("+91 95120 86485")
_PHONE_TITLE = re.compile(r"^\+\d[\d\s\-()]{6,}$")
_SKIP_DOMAINS = ("g.us", "broadcast", "newsletter", "bot")


def _serialized(v: Any) -> str:
    if isinstance(v, dict):
        return str(v.get("_serialized") or "")
    return str(v or "")


def _digits(s: str | None) -> str:
    return re.sub(r"\D", "", s or "")


def phone_from_title(title: str | None) -> str | None:
    """Digits of a WhatsApp-formatted number title, else None."""
    t = (title or "").strip()
    if not _PHONE_TITLE.match(t):
        return None
    d = _digits(t)
    return d if 7 <= len(d) <= 15 else None


def _clip(s: Any, n: int) -> str | None:
    s = str(s or "").strip()
    return s[:n] or None


@dataclass
class ContactRecord:
    """Normalized WhatsApp contact."""
    number: str | None = None      # digits, no +
    wid: str | None = None         # …@c.us
    lid: str | None = None         # …@lid
    name: str | None = None        # saved address-book name
    pushname: str | None = None
    is_business: bool | None = None
    is_my_contact: bool | None = None
    source: str = "whatsapp"


@dataclass
class SyncStats:
    created: int = 0
    updated: int = 0
    unchanged: int = 0
    merged: int = 0
    skipped: int = 0
    phones: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {"created": self.created, "updated": self.updated, "unchanged": self.unchanged,
                "merged": self.merged, "skipped": self.skipped, "phones": self.phones}


def record_from_waha(raw: dict[str, Any], lid_map: dict[str, str] | None = None) -> ContactRecord | None:
    """Normalize one WAHA /contacts/all item (WEBJS / NOWEB / GOWS shapes)."""
    if not isinstance(raw, dict):
        return None
    cid = _serialized(raw.get("id"))
    if not cid or "@" not in cid:
        return None
    domain = cid.rsplit("@", 1)[1]
    if domain in _SKIP_DOMAINS or raw.get("isGroup"):
        return None
    rec = ContactRecord()
    if domain in ("c.us", "s.whatsapp.net"):
        rec.number = _digits(cid.split("@")[0])
        rec.wid = f"{rec.number}@c.us"
    elif domain == "lid":
        rec.lid = cid
        pn = (lid_map or {}).get(cid) or _serialized(raw.get("pn") or raw.get("phoneNumber"))
        if pn:
            rec.number = _digits(pn.split("@")[0])
            rec.wid = f"{rec.number}@c.us" if rec.number else None
    else:
        return None
    if raw.get("lid") and not rec.lid:
        rec.lid = _serialized(raw.get("lid")) or None
    if rec.number and not (7 <= len(rec.number) <= 15):
        rec.number, rec.wid = None, None
    if not rec.number and not rec.lid:
        return None
    is_my = raw.get("isMyContact")
    rec.is_my_contact = bool(is_my) if is_my is not None else None
    # `name` is the address-book name only for saved contacts (WEBJS puts the
    # formatted number / verified name there otherwise)
    name = raw.get("name") or raw.get("fullName")
    if name and (is_my or is_my is None) and not phone_from_title(name):
        rec.name = _clip(name, 255)
    rec.pushname = _clip(raw.get("pushname") or raw.get("pushName") or raw.get("notify"), 255)
    if raw.get("isBusiness") is not None or raw.get("isEnterprise") is not None:
        rec.is_business = bool(raw.get("isBusiness") or raw.get("isEnterprise"))
    return rec


class _Index:
    """In-memory lookup of existing contacts by number / wid / lid."""

    def __init__(self, db: Session) -> None:
        self.by_number: dict[str, Contact] = {}
        self.by_wid: dict[str, Contact] = {}
        self.by_lid: dict[str, Contact] = {}
        for c in db.query(Contact).all():
            self.add(c)

    def add(self, c: Contact) -> None:
        if c.phone_number:
            self.by_number[c.phone_number] = c
        if c.wid:
            self.by_wid[c.wid] = c
        if c.lid:
            self.by_lid[c.lid] = c

    def find(self, rec: ContactRecord) -> tuple[Contact | None, Contact | None]:
        """(phone contact, separate lid-only contact to merge into it)."""
        by_phone = None
        if rec.number:
            by_phone = self.by_number.get(rec.number) or (self.by_wid.get(rec.wid) if rec.wid else None)
        by_lid = None
        if rec.lid:
            by_lid = self.by_lid.get(rec.lid) or self.by_number.get(_lid_key(rec.lid))
        if by_phone and by_lid and by_phone is not by_lid:
            return by_phone, by_lid
        return by_phone or by_lid, None


def _lid_key(lid: str) -> str:
    return f"{_digits(lid.split('@')[0])}{LID_SUFFIX}"


def _apply(c: Contact, rec: ContactRecord, now: datetime) -> bool:
    changed = False

    def put(attr: str, value: Any) -> None:
        nonlocal changed
        if value is not None and getattr(c, attr) != value:
            setattr(c, attr, value)
            changed = True

    # A lid-only row learns its phone number
    if rec.number and not c.has_phone:
        put("phone_number", rec.number)
    put("wid", rec.wid)
    put("lid", rec.lid)
    if rec.name and not (c.name or "").strip():
        put("name", rec.name)
    put("pushname", rec.pushname)
    put("is_business", rec.is_business)
    put("is_my_contact", rec.is_my_contact)
    if changed or not c.synced_at:
        c.synced_at = now
    return changed


def _merge(db: Session, keep: Contact, drop: Contact) -> None:
    """Fold a lid-only duplicate into the phone-number contact."""
    db.flush()  # both rows need ids (either may have been created this run)
    have = {r[0] for r in db.query(ContactLabel.label_id).filter(ContactLabel.contact_id == keep.id)}
    for row in db.query(ContactLabel).filter(ContactLabel.contact_id == drop.id).all():
        if row.label_id not in have:
            db.add(ContactLabel(contact_id=keep.id, label_id=row.label_id))
        db.delete(row)
    for attr in ("email", "company", "notes", "username", "pushname"):
        if not getattr(keep, attr) and getattr(drop, attr):
            setattr(keep, attr, getattr(drop, attr))
    if not (keep.name or "").strip() and drop.name:
        keep.name = drop.name
    if drop.custom_properties:
        keep.custom_properties = {**drop.custom_properties, **(keep.custom_properties or {})}
    keep.is_masked = bool(keep.is_masked or drop.is_masked)
    db.flush()
    db.delete(drop)


def upsert_records(db: Session, records: Iterable[ContactRecord], stats: SyncStats | None = None) -> SyncStats:
    stats = stats or SyncStats()
    idx = _Index(db)
    now = datetime.utcnow()
    pending = 0
    for rec in records:
        if rec is None:
            stats.skipped += 1
            continue
        c, dup = idx.find(rec)
        if dup is not None:
            _merge(db, c, dup)
            idx.by_lid.pop(dup.lid or "", None)
            idx.by_number.pop(dup.phone_number, None)
            stats.merged += 1
        if c is None:
            c = Contact(phone_number=rec.number or _lid_key(rec.lid or ""), name=rec.name or "",
                        source=rec.source)
            _apply(c, rec, now)
            db.add(c)
            idx.add(c)
            stats.created += 1
        else:
            old_number = c.phone_number
            if _apply(c, rec, now):
                stats.updated += 1
                if old_number != c.phone_number:
                    idx.by_number.pop(old_number, None)
                idx.add(c)
            else:
                stats.unchanged += 1
        pending += 1
        if pending >= COMMIT_EVERY:
            db.commit()
            pending = 0
    db.commit()
    return stats


async def fetch_waha_records(phone: Any) -> list[ContactRecord]:
    """All contacts of one phone's WAHA session (raises WAHAError)."""
    from app.services.waha_service import WAHAError, WAHAService
    svc = WAHAService.from_phone(phone)
    lid_map: dict[str, str] = {}
    try:
        for page in range(MAX_PAGES):
            rows = await svc.get_lids(limit=PAGE, offset=page * PAGE)
            for r in rows:
                lid, pn = _serialized(r.get("lid")), _serialized(r.get("pn"))
                if lid and pn:
                    lid_map[lid] = pn
            if len(rows) < PAGE:
                break
    except WAHAError as exc:  # older engines have no /lids — contacts still sync
        logger.info("WAHA lids unavailable for %s: %s", getattr(phone, "session_name", "?"), exc)
    out: list[ContactRecord] = []
    for page in range(MAX_PAGES):
        rows = await svc.get_all_contacts(limit=PAGE, offset=page * PAGE)
        out.extend(r for r in (record_from_waha(x, lid_map) for x in rows) if r)
        if len(rows) < PAGE:
            break
    return out


async def chat_records(phone_ids: list[int] | None = None) -> list[ContactRecord]:
    """1:1 chats stored in MongoDB → contact records."""
    from app.db.mongo import get_mongo_db
    q: dict[str, Any] = {"is_group": {"$ne": True}}
    if phone_ids is not None:
        q["phone_id"] = {"$in": phone_ids}
    out: list[ContactRecord] = []
    async for ch in get_mongo_db().chats.find(q, {"chat_wid": 1, "name": 1}):
        rec = record_from_chat(ch.get("chat_wid") or "", ch.get("name") or "")
        if rec:
            out.append(rec)
    return out


def record_from_chat(chat_wid: str, title: str = "", pushname: str = "",
                     source: str = "chat") -> ContactRecord | None:
    if "@" not in chat_wid:
        return None
    ident, domain = chat_wid.rsplit("@", 1)
    rec = ContactRecord(source=source)
    if domain == "c.us":
        rec.number = _digits(ident)
        rec.wid = f"{rec.number}@c.us"
        if not (7 <= len(rec.number) <= 15):
            return None
    elif domain == "lid":
        rec.lid = chat_wid
        pn = phone_from_title(title)
        if pn:
            rec.number, rec.wid = pn, f"{pn}@c.us"
    else:
        return None
    t = (title or "").strip()
    if pushname:
        rec.pushname = _clip(pushname, 255)
    elif t and not phone_from_title(t) and "@" not in t and t != ident:
        # Chat titles are the saved name or the pushname — we can't tell which,
        # so keep it as the WhatsApp name and leave the CRM name to the user.
        rec.pushname = _clip(t, 255)
    return rec


async def sync_all(db: Session) -> SyncStats:
    """Sync every active phone from WAHA, then fill gaps from stored chats."""
    from app.models.phone import Phone
    from app.services.waha_service import WAHAError
    stats = SyncStats()
    records: list[ContactRecord] = []
    for phone in db.query(Phone).filter(Phone.is_active.is_(True)).all():
        key = str(phone.id)
        try:
            recs = await fetch_waha_records(phone)
            records.extend(recs)
            stats.phones[key] = {"ok": True, "contacts": len(recs)}
        except WAHAError as exc:
            stats.phones[key] = {"ok": False, "error": str(exc)[:200]}
        except Exception as exc:  # network etc. — never abort the whole sync
            logger.warning("Contact sync failed for phone %s: %s", phone.id, exc)
            stats.phones[key] = {"ok": False, "error": "WhatsApp API unreachable"}
    chats = await chat_records()
    stats.phones["chats"] = {"ok": True, "contacts": len(chats)}
    # WAHA records last so their richer fields win within the same run
    upsert_records(db, [*chats, *records], stats)
    return stats


def upsert_contact_from_message(db: Session, chat_wid: str, pushname: str = "") -> Contact | None:
    """Create / refresh the contact for an incoming 1:1 message.

    Intended call site: app/api/webhooks.py `_process_message_event`, right
    after the chat upsert, for `not from_me and not chat_wid.endswith("@g.us")`
    with `notify_name` as pushname. Cheap: indexed lookups, one commit.
    """
    rec = record_from_chat(chat_wid, "", pushname, source="message")
    if not rec:
        return None
    q = db.query(Contact)
    c = None
    if rec.number:
        c = q.filter(Contact.phone_number == rec.number).first()
    if c is None and rec.lid:
        c = q.filter((Contact.lid == rec.lid) | (Contact.phone_number == _lid_key(rec.lid))).first()
    now = datetime.utcnow()
    if c is None:
        c = Contact(phone_number=rec.number or _lid_key(rec.lid or ""), name="", source="message")
        _apply(c, rec, now)
        db.add(c)
    elif not _apply(c, rec, now):
        return c
    try:
        db.commit()
    except Exception:  # concurrent insert of the same number — keep the other one
        db.rollback()
        return None
    return c
