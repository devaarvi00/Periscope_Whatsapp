from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.api.auth import get_current_agent
from app.db.session import get_db
from app.models.agent import Agent
from app.models.contact import Contact, ContactLabel
from app.schemas.contact import ContactCreate, ContactOut, ContactUpdate
from app.services.access import is_admin, require_admin

router = APIRouter(prefix="/contacts", tags=["contacts"])

MASKED_PHONE = "***masked***"


def mask_phone(_number: str | None) -> str:
    return MASKED_PHONE


def _contact_out(db: Session, c: Contact, agent: Agent, with_labels: bool = True) -> dict:
    """Serialize a contact; masked numbers are hidden from non-admins."""
    d = ContactOut.model_validate(c).model_dump()
    if with_labels:
        d["labels"] = [
            r[0] for r in db.query(ContactLabel.label_id).filter(ContactLabel.contact_id == c.id).all()
        ]
    if c.is_masked and not is_admin(agent):
        d["phone_number"] = mask_phone(c.phone_number)
    return d


def _like_escape(value: str) -> str:
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


@router.get("", response_model=list[dict])
def list_contacts(
    search: str | None = None,
    label_id: int | None = None,
    limit: int = 50,
    offset: int = 0,
    db: Session = Depends(get_db),
    agent: Agent = Depends(get_current_agent),
):
    q = db.query(Contact)
    if search:
        pattern = f"%{_like_escape(search)}%"
        name_match = Contact.name.ilike(pattern, escape="\\")
        phone_match = Contact.phone_number.ilike(pattern, escape="\\")
        if is_admin(agent):
            q = q.filter(name_match | phone_match)
        else:
            # Hidden digits of masked contacts must not be searchable
            q = q.filter(name_match | ((Contact.is_masked == False) & phone_match))  # noqa: E712
    if label_id:
        q = q.join(ContactLabel, Contact.id == ContactLabel.contact_id).filter(
            ContactLabel.label_id == label_id
        )
    contacts = q.offset(max(0, offset)).limit(max(1, min(limit, 500))).all()
    return [_contact_out(db, c, agent) for c in contacts]


@router.post("", response_model=dict, status_code=201)
def create_contact(
    req: ContactCreate,
    db: Session = Depends(get_db),
    agent: Agent = Depends(get_current_agent),
):
    if req.is_masked:
        require_admin(agent, "Only admins can mask contacts")
    existing = db.query(Contact).filter(Contact.phone_number == req.phone_number).first()
    if existing:
        raise HTTPException(400, "Contact already exists")
    c = Contact(**req.model_dump())
    db.add(c)
    db.commit()
    db.refresh(c)
    return _contact_out(db, c, agent)


@router.get("/{contact_id}", response_model=dict)
def get_contact(
    contact_id: int,
    db: Session = Depends(get_db),
    agent: Agent = Depends(get_current_agent),
):
    c = db.query(Contact).filter(Contact.id == contact_id).first()
    if not c:
        raise HTTPException(404, "Contact not found")
    return _contact_out(db, c, agent)


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
