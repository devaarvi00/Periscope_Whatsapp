from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.api.auth import get_current_agent
from app.db.session import get_db
from app.models.agent import Agent
from app.models.org_settings import OrgSettings
from app.schemas.org import OrgOut, OrgUpdate
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


@router.get("", response_model=OrgOut)
def get_org(db: Session = Depends(get_db)):
    return _get_or_create(db)


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
    return org
