"""AI Agent → Knowledge Base: FAQ entries, uploaded documents, external
sources and self-learned suggestions (Needs Review)."""
from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from sqlalchemy import or_
from sqlalchemy.orm import Session

from app.api.auth import get_current_agent
from app.db.session import get_db
from app.models.agent import Agent, AgentRole
from app.models.knowledge_item import KB_STATUSES, KB_TYPES, KnowledgeItem, kb_status_of, kb_type_of
from app.schemas.ai_agent import ExternalSourceCreate, KnowledgeItemCreate, KnowledgeItemUpdate

router = APIRouter(prefix="/knowledge-base", tags=["knowledge-base"])

MAX_UPLOAD_BYTES = 5_000_000


def _admin(agent: Agent) -> None:
    if agent.role != AgentRole.ADMIN:
        raise HTTPException(403, "Only admins can change the knowledge base")


def _out(i: KnowledgeItem, full: bool = True) -> dict:
    content = i.content or ""
    return {
        "id": i.id, "item_type": kb_type_of(i), "title": i.title,
        "content": content if full else content[:300], "chars": len(content),
        "status": kb_status_of(i), "is_self_learned": bool(i.is_self_learned),
        "source": i.source, "origin_chat_id": i.origin_chat_id,
        "created_at": i.created_at.isoformat() if i.created_at else None,
        "updated_at": i.updated_at.isoformat() if i.updated_at else None,
    }


def _get(db: Session, item_id: int) -> KnowledgeItem:
    item = db.query(KnowledgeItem).filter(KnowledgeItem.id == item_id).first()
    if not item:
        raise HTTPException(404, "Item not found")
    return item


def _type_filter(q, t: str):
    if t == "self_learned":
        return q.filter(or_(KnowledgeItem.item_type == "self_learned", KnowledgeItem.is_self_learned == True))  # noqa: E712
    if t == "faq":
        return q.filter(KnowledgeItem.item_type == "faq", KnowledgeItem.is_self_learned == False)  # noqa: E712
    return q.filter(KnowledgeItem.item_type == t)


def _status_filter(q, s: str):
    if s == "inactive":
        return q.filter(KnowledgeItem.status.in_(("inactive", "archived")))
    return q.filter(KnowledgeItem.status == s)


@router.get("")
def list_items(
    status: str | None = None,
    item_type: str | None = None,
    q: str | None = None,
    summary: bool = False,
    db: Session = Depends(get_db),
):
    query = db.query(KnowledgeItem)
    if item_type:
        query = _type_filter(query, item_type)
    if q:
        like = f"%{q.strip()[:200]}%"
        query = query.filter(or_(KnowledgeItem.title.ilike(like), KnowledgeItem.content.ilike(like)))
    counts_q = query
    if status:
        query = _status_filter(query, status)
    items = query.order_by(KnowledgeItem.updated_at.desc(), KnowledgeItem.id.desc()).limit(1000).all()
    counts = {s: 0 for s in KB_STATUSES}
    for (st,) in counts_q.with_entities(KnowledgeItem.status).all():
        counts["inactive" if st == "archived" else (st if st in counts else "active")] += 1
    counts["all"] = sum(counts[s] for s in KB_STATUSES)
    return {"items": [_out(i, full=not summary) for i in items], "counts": counts}


@router.get("/{item_id}")
def get_item(item_id: int, db: Session = Depends(get_db)):
    return _out(_get(db, item_id))


@router.post("", status_code=201)
def create_item(req: KnowledgeItemCreate, db: Session = Depends(get_db),
                agent: Agent = Depends(get_current_agent)):
    _admin(agent)
    if req.item_type not in KB_TYPES or req.status not in KB_STATUSES:
        raise HTTPException(400, "Invalid type or status")
    item = KnowledgeItem(item_type=req.item_type, title=req.title.strip(), content=req.content.strip(),
                         status=req.status, is_self_learned=req.item_type == "self_learned",
                         created_by=agent.id)
    db.add(item)
    db.commit()
    db.refresh(item)
    return _out(item)


@router.post("/upload", status_code=201)
async def upload_document(file: UploadFile = File(...), title: str = Form(""),
                          db: Session = Depends(get_db), agent: Agent = Depends(get_current_agent)):
    from app.services.ai_knowledge import UnsupportedFile, extract_upload_text
    _admin(agent)
    data = await file.read(MAX_UPLOAD_BYTES + 1)
    if len(data) > MAX_UPLOAD_BYTES:
        raise HTTPException(413, "File is larger than 5 MB")
    try:
        text = extract_upload_text(file.filename or "", data)
    except UnsupportedFile as exc:
        raise HTTPException(400, str(exc))
    name = (file.filename or "document").rsplit("/", 1)[-1][:255]
    item = KnowledgeItem(item_type="document", title=(title.strip() or name)[:500], content=text,
                         status="active", source=name, created_by=agent.id)
    db.add(item)
    db.commit()
    db.refresh(item)
    return _out(item, full=False)


@router.post("/external", status_code=201)
async def add_external(req: ExternalSourceCreate, db: Session = Depends(get_db),
                       agent: Agent = Depends(get_current_agent)):
    from app.services.ai_knowledge import fetch_external
    from app.services.url_safety import UnsafeURLError
    _admin(agent)
    try:
        page_title, text = await fetch_external(req.url)
    except UnsafeURLError as exc:
        raise HTTPException(400, f"URL not allowed: {exc}")
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    except Exception:
        raise HTTPException(502, "Could not fetch that page")
    item = KnowledgeItem(item_type="external", title=((req.title or "").strip() or page_title or req.url)[:500],
                         content=text, status="active", source=req.url.strip()[:1000], created_by=agent.id)
    db.add(item)
    db.commit()
    db.refresh(item)
    return _out(item, full=False)


@router.post("/reindex")
async def reindex(db: Session = Depends(get_db), agent: Agent = Depends(get_current_agent)):
    """Re-fetch external sources and rebuild the retrieval index."""
    from app.services.ai_knowledge import fetch_external, get_index, invalidate_index
    _admin(agent)
    refreshed, failed = 0, 0
    for item in db.query(KnowledgeItem).filter(KnowledgeItem.item_type == "external",
                                               KnowledgeItem.source.isnot(None)).all():
        try:
            _, text = await fetch_external(item.source)
            item.content = text
            refreshed += 1
        except Exception:
            failed += 1
    db.commit()
    invalidate_index()
    idx = get_index(db, force=True)
    return {"passages": len(idx.passages), "refreshed_sources": refreshed, "failed_sources": failed}


@router.patch("/{item_id}")
def update_item(item_id: int, req: KnowledgeItemUpdate, db: Session = Depends(get_db),
                agent: Agent = Depends(get_current_agent)):
    _admin(agent)
    item = _get(db, item_id)
    changes = req.model_dump(exclude_unset=True, exclude_none=True)
    if "status" in changes and changes["status"] not in KB_STATUSES:
        raise HTTPException(400, "Invalid status")
    for k, v in changes.items():
        setattr(item, k, v.strip() if isinstance(v, str) else v)
    db.commit()
    db.refresh(item)
    return _out(item)


@router.patch("/{item_id}/approve")
def approve_item(item_id: int, db: Session = Depends(get_db), agent: Agent = Depends(get_current_agent)):
    _admin(agent)
    item = _get(db, item_id)
    item.status = "active"
    db.commit()
    return {"ok": True}


@router.delete("/{item_id}", status_code=204)
def delete_item(item_id: int, db: Session = Depends(get_db), agent: Agent = Depends(get_current_agent)):
    _admin(agent)
    db.delete(_get(db, item_id))
    db.commit()
