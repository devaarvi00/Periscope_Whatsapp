"""Write / read operation logs (Logs → Group / API / Webhooks / Rules / Scheduled).

`record()` and `update()` never raise into callers: logging must not break a
send, a webhook or a rule. They use their own short-lived session so they
neither commit nor roll back the caller's transaction.
"""
from __future__ import annotations

import json
import logging
import secrets
import string
import time
from datetime import datetime, timedelta
from typing import Any

from app.models.operation_log import OPERATION_KINDS, OperationLog

logger = logging.getLogger(__name__)

RETENTION_DAYS = 7
MAX_DETAILS_BYTES = 8000
MAX_STR = 300
MAX_LIST = 50
PREVIEW_CHARS = 80

# Keys whose values must never be stored, at any depth
_SECRET_MARKERS = ("secret", "password", "token", "api_key", "apikey", "x-api-key",
                   "authorization", "signature", "key_hash", "cookie")
_UID_ALPHABET = string.ascii_lowercase + string.digits


def new_uid() -> str:
    return "".join(secrets.choice(_UID_ALPHABET) for _ in range(12))


def preview(text: Any, n: int = PREVIEW_CHARS) -> str:
    """Short, single-line preview of a message body."""
    s = " ".join(str(text or "").split())
    return s if len(s) <= n else s[: n - 1] + "…"


def _is_secret_key(key: str) -> bool:
    k = key.lower()
    return any(m in k for m in _SECRET_MARKERS)


def sanitize(value: Any, depth: int = 0) -> Any:
    """JSON-safe copy with secret-looking keys dropped and sizes bounded."""
    if depth > 5:
        return "…"
    if isinstance(value, dict):
        out = {}
        for i, (k, v) in enumerate(value.items()):
            if i >= MAX_LIST:
                out["…"] = f"{len(value) - MAX_LIST} more"
                break
            k = str(k)
            if _is_secret_key(k):
                continue
            out[k[:60]] = sanitize(v, depth + 1)
        return out
    if isinstance(value, (list, tuple, set)):
        items = list(value)
        out = [sanitize(v, depth + 1) for v in items[:MAX_LIST]]
        if len(items) > MAX_LIST:
            out.append(f"… {len(items) - MAX_LIST} more")
        return out
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, datetime):
        return value.isoformat()
    s = str(value)
    return s if len(s) <= MAX_STR else s[:MAX_STR] + "…"


def _bounded_details(details: dict | None) -> dict | None:
    if not details:
        return None
    clean = sanitize(details)
    raw = json.dumps(clean, default=str)
    if len(raw) <= MAX_DETAILS_BYTES:
        return clean
    return {"truncated": True, "preview": raw[: MAX_DETAILS_BYTES - 200]}


def derive_status(success: int, failed: int, pending: int) -> str:
    if pending:
        return "pending"
    if failed and success:
        return "partial"
    if failed:
        return "failed"
    return "success"


def record(
    kind: str,
    operation: str,
    *,
    success: int = 0,
    failed: int = 0,
    pending: int = 0,
    status: str | None = None,
    performed_by_id: int | None = None,
    performed_by: str | None = None,
    status_code: int | None = None,
    duration_ms: int | None = None,
    details: dict | None = None,
) -> str | None:
    """Store one operation log; returns its uid (None if it couldn't be saved)."""
    try:
        if kind not in OPERATION_KINDS:
            raise ValueError(f"unknown kind {kind!r}")
        from app.db.session import SessionLocal
        success, failed, pending = max(0, int(success)), max(0, int(failed)), max(0, int(pending))
        row = OperationLog(
            uid=new_uid(), kind=kind, operation=str(operation or kind)[:255],
            success_count=success, failed_count=failed, pending_count=pending,
            status=status or derive_status(success, failed, pending),
            status_code=status_code, duration_ms=duration_ms,
            performed_by_id=performed_by_id,
            performed_by=(str(performed_by)[:120] if performed_by else None),
            details=_bounded_details(details),
        )
        db = SessionLocal()
        try:
            db.add(row)
            db.commit()
            return row.uid
        finally:
            db.close()
    except Exception as exc:  # never break the caller
        logger.warning("operation log (%s %s) not saved: %s", kind, operation, exc)
        return None


def update(
    uid: str | None,
    *,
    success: int | None = None,
    failed: int | None = None,
    pending: int | None = None,
    status: str | None = None,
    duration_ms: int | None = None,
    details: dict | None = None,
) -> bool:
    """Update counts / status of a log (e.g. pending → done). Details are merged."""
    if not uid:
        return False
    try:
        from app.db.session import SessionLocal
        db = SessionLocal()
        try:
            row = db.query(OperationLog).filter(OperationLog.uid == uid).first()
            if not row:
                return False
            if success is not None:
                row.success_count = max(0, int(success))
            if failed is not None:
                row.failed_count = max(0, int(failed))
            if pending is not None:
                row.pending_count = max(0, int(pending))
            row.status = status or derive_status(row.success_count, row.failed_count, row.pending_count)
            if duration_ms is not None:
                row.duration_ms = duration_ms
            if details:
                row.details = _bounded_details({**(row.details or {}), **details})
            db.commit()
            return True
        finally:
            db.close()
    except Exception as exc:
        logger.warning("operation log %s not updated: %s", uid, exc)
        return False


_last_cleanup = 0.0


def cleanup(days: int = RETENTION_DAYS, *, min_interval: float = 3600.0) -> int:
    """Delete rows older than `days`. Throttled to once per `min_interval` s."""
    global _last_cleanup
    now = time.monotonic()
    if _last_cleanup and now - _last_cleanup < min_interval:
        return 0
    _last_cleanup = now
    try:
        from app.db.session import SessionLocal
        db = SessionLocal()
        try:
            cutoff = datetime.utcnow() - timedelta(days=days)
            n = (db.query(OperationLog).filter(OperationLog.created_at < cutoff)
                 .delete(synchronize_session=False))
            db.commit()
            if n:
                logger.info("Purged %d operation log(s) older than %d days", n, days)
            return n
        finally:
            db.close()
    except Exception as exc:
        logger.warning("operation log cleanup failed: %s", exc)
        return 0


# ── Reading ──────────────────────────────────────────────────────────────── #

# Performed-by filter values besides a numeric agent id
PERFORMER_FILTERS = {
    "api": "API key",
    "automation": "Automation",
    "scheduler": "Scheduler",
    "system": "System",
}


def list_logs(db, *, kind: str | None, q: str | None, status: str | None,
              since: datetime | None, until: datetime | None,
              performed_by: str | None, page: int, page_size: int):
    from sqlalchemy import or_
    query = db.query(OperationLog)
    if kind:
        query = query.filter(OperationLog.kind == kind)
    if since:
        query = query.filter(OperationLog.created_at >= since)
    if until:
        query = query.filter(OperationLog.created_at < until)
    if status == "failed":
        query = query.filter(OperationLog.status.in_(("failed", "partial")))
    elif status:
        query = query.filter(OperationLog.status == status)
    if performed_by:
        if performed_by.isdigit():
            query = query.filter(OperationLog.performed_by_id == int(performed_by))
        elif performed_by in PERFORMER_FILTERS:
            label = PERFORMER_FILTERS[performed_by]
            query = query.filter(OperationLog.performed_by_id.is_(None),
                                 OperationLog.performed_by.like(label + "%"))
    if q:
        q = q.strip()[:100]
        like = "%" + q.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
        conds = [OperationLog.uid == q.lower(), OperationLog.operation.like(like, escape="\\"),
                 OperationLog.performed_by.like(like, escape="\\")]
        from app.models.agent import Agent
        ids = [a.id for a in db.query(Agent.id).filter(Agent.name.like(like, escape="\\")).limit(50)]
        if ids:
            conds.append(OperationLog.performed_by_id.in_(ids))
        query = query.filter(or_(*conds))
    total = query.count()
    rows = (query.order_by(OperationLog.created_at.desc(), OperationLog.id.desc())
            .offset((page - 1) * page_size).limit(page_size).all())
    return rows, total


def serialize(row: OperationLog, agent_names: dict[int, str], *, full: bool = False,
              admin: bool = True, mask=None) -> dict:
    performer = row.performed_by or "System"
    if row.performed_by_id is not None:
        performer = agent_names.get(row.performed_by_id) or f"Member #{row.performed_by_id}"
    details = row.details or {}
    if not admin and row.kind == "api":
        # Agents never see which API key was used
        if performer.startswith("API key"):
            performer = "API key"
        details = {k: v for k, v in details.items() if k not in ("api_key_id", "api_key_name", "client_ip")}
    if mask:  # Settings → Config → "Mask User Phone Numbers"
        details = dict(details)
        if isinstance(details.get("participants"), list):
            details["participants"] = [
                {**p, "id": mask(p.get("id"))} if isinstance(p, dict) else p
                for p in details["participants"]]
        if isinstance(details.get("chat"), str):
            details["chat"] = mask(details["chat"])
    out = {
        "uid": row.uid,
        "kind": row.kind,
        "operation": row.operation,
        "status": row.status,
        "success_count": row.success_count,
        "failed_count": row.failed_count,
        "pending_count": row.pending_count,
        "status_code": row.status_code,
        "duration_ms": row.duration_ms,
        "performed_by": performer,
        "performed_by_id": row.performed_by_id,
        "created_at": row.created_at.isoformat() if row.created_at else None,
        "updated_at": row.updated_at.isoformat() if row.updated_at else None,
    }
    if full:
        out["details"] = details
    return out
