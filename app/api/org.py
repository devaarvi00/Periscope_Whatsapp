import base64
import binascii
import re
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Response
from sqlalchemy.orm import Session

from app.api.auth import get_current_agent
from app.db.session import get_db
from app.models.agent import Agent
from app.models.org_settings import OrgSettings
from app.schemas.org import ORG_LOGO_MAX_BYTES, OrgLogoUpload, OrgOut, OrgUpdate
from app.services.access import require_admin

router = APIRouter(prefix="/org", tags=["org"])


def _get_or_create(db: Session) -> OrgSettings:
    org = db.query(OrgSettings).filter(OrgSettings.id == 1).first()
    if not org:
        org = OrgSettings(id=1)
        db.add(org)
        db.commit()
        db.refresh(org)
    return org


def _out(org: OrgSettings) -> OrgOut:
    return OrgOut(
        uid=org.uid, name=org.name,
        support_email=org.support_email, support_url=org.support_url,
        has_logo=bool(org.logo_mime),
        logo_version=org.logo_updated_at.strftime("%Y%m%d%H%M%S%f") if org.logo_mime and org.logo_updated_at else None,
    )


@router.get("", response_model=OrgOut)
def get_org(db: Session = Depends(get_db)):
    return _out(_get_or_create(db))


@router.patch("", response_model=OrgOut)
def update_org(
    req: OrgUpdate,
    db: Session = Depends(get_db),
    agent: Agent = Depends(get_current_agent),
):
    require_admin(agent, "Only admins can change organization settings")
    org = _get_or_create(db)
    for field, value in req.model_dump(exclude_unset=True).items():
        if field == "name" and value is None:
            continue  # name is required; ignore an explicit null
        setattr(org, field, value)
    db.commit()
    db.refresh(org)
    return _out(org)


# ── Workspace logo ─────────────────────────────────────────────────── #

_DATA_URL_RE = re.compile(r"^data:(image/(?:png|jpeg|jpg));base64,([A-Za-z0-9+/=\s]+)$")


def _sniff_image(data: bytes) -> str | None:
    """Trust the bytes, not the declared type."""
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if data.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    return None


@router.get("/logo")
def get_logo(db: Session = Depends(get_db)):
    org = _get_or_create(db)
    if not org.logo_mime or not org.logo_data:
        raise HTTPException(404, "No logo set")
    return Response(
        content=org.logo_data, media_type=org.logo_mime,
        headers={"Cache-Control": "private, max-age=86400", "X-Content-Type-Options": "nosniff"},
    )


@router.put("/logo", response_model=OrgOut)
def upload_logo(
    req: OrgLogoUpload,
    db: Session = Depends(get_db),
    agent: Agent = Depends(get_current_agent),
):
    require_admin(agent, "Only admins can change the organization logo")
    m = _DATA_URL_RE.match(req.data_url.strip())
    if not m:
        raise HTTPException(400, "Logo must be a PNG or JPG image")
    try:
        data = base64.b64decode(re.sub(r"\s+", "", m.group(2)), validate=True)
    except (binascii.Error, ValueError):
        raise HTTPException(400, "Logo data is not valid base64")
    if len(data) > ORG_LOGO_MAX_BYTES:
        raise HTTPException(400, "Logo must be 512 KB or smaller")
    mime = _sniff_image(data)
    if not mime:
        raise HTTPException(400, "Logo must be a PNG or JPG image")
    org = _get_or_create(db)
    org.logo_data = data
    org.logo_mime = mime
    org.logo_updated_at = datetime.utcnow()
    db.commit()
    db.refresh(org)
    return _out(org)


@router.delete("/logo", response_model=OrgOut)
def delete_logo(
    db: Session = Depends(get_db),
    agent: Agent = Depends(get_current_agent),
):
    require_admin(agent, "Only admins can change the organization logo")
    org = _get_or_create(db)
    org.logo_data = None
    org.logo_mime = None
    org.logo_updated_at = datetime.utcnow()
    db.commit()
    db.refresh(org)
    return _out(org)
