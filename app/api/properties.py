from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.api.auth import get_current_agent
from app.db.session import get_db
from app.models.agent import Agent, AgentRole
from app.models.chat import Chat
from app.models.property_definition import PropertyDefinition
from app.models.ticket import Ticket
from app.services.activity_service import log_activity

router = APIRouter(prefix="/properties", tags=["custom-properties"])

PROP_TYPES = ("text", "number", "date", "single_select", "multi_select")


class PropertyCreate(BaseModel):
    entity: str  # chat|ticket
    section: str = "General"
    name: str
    prop_type: str = "text"
    options: list[str] | None = None
    required: bool = False


class PropertyUpdate(BaseModel):
    section: str | None = None
    name: str | None = None
    options: list[str] | None = None
    required: bool | None = None
    sort_order: int | None = None


class ValueUpdate(BaseModel):
    values: dict  # {definition_id(str): value}


def _serialize(d: PropertyDefinition) -> dict:
    return {
        "id": d.id, "entity": d.entity, "section": d.section,
        "name": d.name, "prop_type": d.prop_type,
        "options": d.options or [], "required": d.required,
        "sort_order": d.sort_order,
    }


@router.get("/definitions")
def list_definitions(entity: str | None = None, db: Session = Depends(get_db)):
    q = db.query(PropertyDefinition)
    if entity:
        q = q.filter(PropertyDefinition.entity == entity)
    defs = q.order_by(PropertyDefinition.section, PropertyDefinition.sort_order,
                      PropertyDefinition.id).all()
    return [_serialize(d) for d in defs]


@router.post("/definitions", status_code=201)
def create_definition(
    req: PropertyCreate,
    db: Session = Depends(get_db),
    agent: Agent = Depends(get_current_agent),
):
    if agent.role != AgentRole.ADMIN:
        raise HTTPException(403, "Only admins can define custom properties")
    if req.entity not in ("chat", "ticket"):
        raise HTTPException(400, "entity must be 'chat' or 'ticket'")
    if req.prop_type not in PROP_TYPES:
        raise HTTPException(400, f"prop_type must be one of {PROP_TYPES}")
    if req.prop_type in ("single_select", "multi_select") and not (req.options and len(req.options) >= 1):
        raise HTTPException(400, "Select properties need at least 1 option")
    if not req.name.strip():
        raise HTTPException(400, "Name is required")
    d = PropertyDefinition(
        entity=req.entity, section=req.section.strip() or "General",
        name=req.name.strip()[:100], prop_type=req.prop_type,
        options=req.options, required=req.required,
    )
    db.add(d)
    db.commit()
    db.refresh(d)
    log_activity(
        db, "property_created", entity_type="property", entity_id=d.id,
        agent_id=agent.id, description=f"Custom {d.entity} property '{d.name}' ({d.prop_type}) created",
    )
    return _serialize(d)


@router.patch("/definitions/{def_id}")
def update_definition(
    def_id: int,
    req: PropertyUpdate,
    db: Session = Depends(get_db),
    agent: Agent = Depends(get_current_agent),
):
    if agent.role != AgentRole.ADMIN:
        raise HTTPException(403, "Only admins can edit custom properties")
    d = db.query(PropertyDefinition).filter(PropertyDefinition.id == def_id).first()
    if not d:
        raise HTTPException(404, "Property not found")
    for k, v in req.model_dump(exclude_none=True).items():
        setattr(d, k, v)
    db.commit()
    db.refresh(d)
    return _serialize(d)


@router.delete("/definitions/{def_id}", status_code=204)
def delete_definition(
    def_id: int,
    db: Session = Depends(get_db),
    agent: Agent = Depends(get_current_agent),
):
    if agent.role != AgentRole.ADMIN:
        raise HTTPException(403, "Only admins can delete custom properties")
    d = db.query(PropertyDefinition).filter(PropertyDefinition.id == def_id).first()
    if not d:
        raise HTTPException(404, "Property not found")
    db.delete(d)
    db.commit()
    log_activity(
        db, "property_deleted", entity_type="property", entity_id=def_id,
        agent_id=agent.id, description=f"Custom property '{d.name}' deleted",
    )


# ── Values ────────────────────────────────────────────────────────────────────

def _validate_values(db: Session, entity: str, values: dict) -> dict:
    defs = {
        str(d.id): d for d in
        db.query(PropertyDefinition).filter(PropertyDefinition.entity == entity).all()
    }
    clean: dict = {}
    for key, val in values.items():
        d = defs.get(str(key))
        if not d:
            continue
        if val in (None, "", []):
            clean[str(key)] = None
            continue
        if d.prop_type == "multi_select":
            vals = val if isinstance(val, list) else [val]
            clean[str(key)] = [v for v in vals if v in (d.options or [])]
        elif d.prop_type == "single_select":
            if val in (d.options or []):
                clean[str(key)] = val
        elif d.prop_type == "number":
            try:
                clean[str(key)] = float(val)
            except (TypeError, ValueError):
                raise HTTPException(400, f"'{d.name}' must be a number")
        else:
            clean[str(key)] = str(val)[:1000]
    return clean


@router.put("/chat/{chat_id}")
def set_chat_values(chat_id: int, req: ValueUpdate, db: Session = Depends(get_db)):
    chat = db.query(Chat).filter(Chat.id == chat_id).first()
    if not chat:
        raise HTTPException(404, "Chat not found")
    current = dict(chat.custom_properties or {})
    current.update(_validate_values(db, "chat", req.values))
    chat.custom_properties = {k: v for k, v in current.items() if v is not None}
    db.commit()
    return {"chat_id": chat_id, "custom_properties": chat.custom_properties}


@router.get("/chat/{chat_id}")
def get_chat_values(chat_id: int, db: Session = Depends(get_db)):
    chat = db.query(Chat).filter(Chat.id == chat_id).first()
    if not chat:
        raise HTTPException(404, "Chat not found")
    return {"chat_id": chat_id, "custom_properties": chat.custom_properties or {}}


@router.put("/ticket/{ticket_id}")
def set_ticket_values(ticket_id: int, req: ValueUpdate, db: Session = Depends(get_db)):
    ticket = db.query(Ticket).filter(Ticket.id == ticket_id).first()
    if not ticket:
        raise HTTPException(404, "Ticket not found")
    current = dict(ticket.custom_properties or {})
    current.update(_validate_values(db, "ticket", req.values))
    ticket.custom_properties = {k: v for k, v in current.items() if v is not None}
    db.commit()
    return {"ticket_id": ticket_id, "custom_properties": ticket.custom_properties}


@router.get("/ticket/{ticket_id}")
def get_ticket_values(ticket_id: int, db: Session = Depends(get_db)):
    ticket = db.query(Ticket).filter(Ticket.id == ticket_id).first()
    if not ticket:
        raise HTTPException(404, "Ticket not found")
    return {"ticket_id": ticket_id, "custom_properties": ticket.custom_properties or {}}
