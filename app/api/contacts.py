from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.models.contact import Contact, ContactLabel
from app.schemas.contact import ContactCreate, ContactOut, ContactUpdate

router = APIRouter(prefix="/contacts", tags=["contacts"])


@router.get("", response_model=list[dict])
def list_contacts(
    search: str | None = None,
    label_id: int | None = None,
    limit: int = 50,
    offset: int = 0,
    db: Session = Depends(get_db),
):
    q = db.query(Contact)
    if search:
        q = q.filter(
            Contact.name.ilike(f"%{search}%") | Contact.phone_number.ilike(f"%{search}%")
        )
    if label_id:
        q = q.join(ContactLabel, Contact.id == ContactLabel.contact_id).filter(
            ContactLabel.label_id == label_id
        )
    contacts = q.offset(offset).limit(limit).all()
    result = []
    for c in contacts:
        labels = [r[0] for r in db.query(ContactLabel.label_id).filter(ContactLabel.contact_id == c.id).all()]
        d = ContactOut.model_validate(c).model_dump()
        d["labels"] = labels
        if c.is_masked:
            d["phone_number"] = "***masked***"
        result.append(d)
    return result


@router.post("", response_model=ContactOut, status_code=201)
def create_contact(req: ContactCreate, db: Session = Depends(get_db)):
    existing = db.query(Contact).filter(Contact.phone_number == req.phone_number).first()
    if existing:
        raise HTTPException(400, "Contact already exists")
    c = Contact(**req.model_dump())
    db.add(c)
    db.commit()
    db.refresh(c)
    return c


@router.get("/{contact_id}", response_model=ContactOut)
def get_contact(contact_id: int, db: Session = Depends(get_db)):
    c = db.query(Contact).filter(Contact.id == contact_id).first()
    if not c:
        raise HTTPException(404, "Contact not found")
    return c


@router.patch("/{contact_id}", response_model=ContactOut)
def update_contact(contact_id: int, req: ContactUpdate, db: Session = Depends(get_db)):
    c = db.query(Contact).filter(Contact.id == contact_id).first()
    if not c:
        raise HTTPException(404, "Contact not found")
    for k, v in req.model_dump(exclude_none=True).items():
        setattr(c, k, v)
    db.commit()
    db.refresh(c)
    return c


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
