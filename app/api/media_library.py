"""Settings → Media Library: files the team uploads once and reuses.

Bytes live on disk under MEDIA_LIBRARY_DIR (default <repo>/data/media_library,
git-ignored, never under frontend/), metadata in media_library_items. Files are
only served through the authenticated GET /media-library/{id}/file.

Listing is open to every agent (the composer picker uses it); uploading and
deleting need the Media Library settings screen (admins always).
"""
from __future__ import annotations

import logging
import os
import re
import uuid
from pathlib import Path

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session

from app.api.auth import get_current_agent
from app.db.session import get_db
from app.models.agent import Agent
from app.models.org_config import MediaLibraryItem
from app.services.access import screen_guard

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/media-library", tags=["media-library"])

MAX_BYTES = 16 * 1024 * 1024
_CHUNK = 1024 * 1024

# extension → (kind, mimetype). The stored type comes from here, never from
# the client, and nothing that can execute in a browser (html, svg, js) is allowed.
ALLOWED: dict[str, tuple[str, str]] = {
    ".jpg": ("media", "image/jpeg"), ".jpeg": ("media", "image/jpeg"), ".png": ("media", "image/png"),
    ".gif": ("media", "image/gif"), ".webp": ("media", "image/webp"),
    ".mp4": ("media", "video/mp4"), ".3gp": ("media", "video/3gpp"), ".mov": ("media", "video/quicktime"),
    ".webm": ("media", "video/webm"),
    ".pdf": ("doc", "application/pdf"), ".txt": ("doc", "text/plain"), ".csv": ("doc", "text/csv"),
    ".doc": ("doc", "application/msword"),
    ".docx": ("doc", "application/vnd.openxmlformats-officedocument.wordprocessingml.document"),
    ".xls": ("doc", "application/vnd.ms-excel"),
    ".xlsx": ("doc", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"),
    ".ppt": ("doc", "application/vnd.ms-powerpoint"),
    ".pptx": ("doc", "application/vnd.openxmlformats-officedocument.presentationml.presentation"),
    ".zip": ("doc", "application/zip"),
}
_INLINE = re.compile(r"^(image/(png|jpeg|gif|webp)|video/[\w.+-]+|application/pdf)$")


def storage_dir() -> Path:
    base = os.environ.get("MEDIA_LIBRARY_DIR") or str(
        Path(__file__).resolve().parents[2] / "data" / "media_library"
    )
    p = Path(base)
    p.mkdir(parents=True, exist_ok=True)
    return p


def _path(item: MediaLibraryItem) -> Path:
    # stored_name is our own uuid + extension; never a client-supplied path
    return storage_dir() / Path(item.stored_name).name


def _out(item: MediaLibraryItem, names: dict[int, str]) -> dict:
    return {
        "id": item.id,
        "kind": item.kind,
        "name": item.name,
        "mimetype": item.mimetype,
        "size": item.size,
        "uploaded_by": item.uploaded_by,
        "uploaded_by_name": names.get(item.uploaded_by or 0, ""),
        "created_at": item.created_at.isoformat() if item.created_at else None,
        "url": f"/api/v1/media-library/{item.id}/file",
    }


def _clean_name(name: str) -> str:
    name = Path(name or "file").name
    name = re.sub(r"[\x00-\x1f\\/:*?\"<>|]", "_", name).strip() or "file"
    return name[:200]


@router.get("")
def list_items(
    kind: str | None = Query(None, description="media | doc"),
    search: str | None = None,
    limit: int = Query(200, ge=1, le=500),
    db: Session = Depends(get_db),
):
    q = db.query(MediaLibraryItem)
    if kind:
        if kind not in ("media", "doc"):
            raise HTTPException(400, "kind must be media or doc")
        q = q.filter(MediaLibraryItem.kind == kind)
    if search and search.strip():
        esc = search.strip().replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        q = q.filter(MediaLibraryItem.name.ilike(f"%{esc}%", escape="\\"))
    items = q.order_by(MediaLibraryItem.id.desc()).limit(limit).all()
    names = {a.id: a.name for a in db.query(Agent.id, Agent.name).all()}
    return [_out(i, names) for i in items]


@router.post("", status_code=201, dependencies=[Depends(screen_guard("media_library", "the Media Library"))])
async def upload_item(
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    agent: Agent = Depends(get_current_agent),
):
    name = _clean_name(file.filename or "")
    ext = Path(name).suffix.lower()
    if ext not in ALLOWED:
        raise HTTPException(415, "Unsupported file type — upload images, videos or documents "
                                 "(" + ", ".join(sorted(e.lstrip('.') for e in ALLOWED)) + ")")
    kind, mimetype = ALLOWED[ext]
    stored_name = f"{uuid.uuid4().hex}{ext}"
    dest = storage_dir() / stored_name
    size = 0
    try:
        with open(dest, "wb") as fh:
            while True:
                chunk = await file.read(_CHUNK)
                if not chunk:
                    break
                size += len(chunk)
                if size > MAX_BYTES:
                    raise HTTPException(413, "File is larger than 16 MB")
                fh.write(chunk)
    except BaseException:
        dest.unlink(missing_ok=True)
        raise
    if size == 0:
        dest.unlink(missing_ok=True)
        raise HTTPException(400, "The file is empty")
    item = MediaLibraryItem(kind=kind, name=name, stored_name=stored_name, mimetype=mimetype,
                            size=size, uploaded_by=agent.id)
    db.add(item)
    try:
        db.commit()
    except Exception:
        db.rollback()
        dest.unlink(missing_ok=True)
        raise
    db.refresh(item)
    return _out(item, {agent.id: agent.name})


@router.get("/{item_id}/file")
def item_file(item_id: int, download: bool = False, db: Session = Depends(get_db)):
    item = db.get(MediaLibraryItem, item_id)
    if not item or not _path(item).is_file():
        raise HTTPException(404, "File not found")
    inline = bool(_INLINE.match(item.mimetype)) and not download
    return FileResponse(
        _path(item),
        media_type=item.mimetype if inline else "application/octet-stream",
        filename=item.name,
        content_disposition_type="inline" if inline else "attachment",
        headers={
            "Cache-Control": "private, max-age=3600",
            "X-Content-Type-Options": "nosniff",
            "Content-Security-Policy": "sandbox; default-src 'none'",
        },
    )


@router.delete("/{item_id}", status_code=204,
               dependencies=[Depends(screen_guard("media_library", "the Media Library"))])
def delete_item(item_id: int, db: Session = Depends(get_db)):
    item = db.get(MediaLibraryItem, item_id)
    if not item:
        raise HTTPException(404, "File not found")
    path = _path(item)
    db.delete(item)
    db.commit()
    try:
        path.unlink(missing_ok=True)
    except OSError as exc:
        logger.warning("Could not delete media library file %s: %s", path, exc)
