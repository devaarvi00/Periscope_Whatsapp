from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.schemas.ai_agent import AutomationRuleCreate, AutomationRuleOut
from app.services.automation_service import TRIGGER_TYPES, AutomationService

router = APIRouter(prefix="/automation", tags=["automation"])


@router.get("/trigger-types")
def get_trigger_types():
    return TRIGGER_TYPES


@router.get("/rules", response_model=list[AutomationRuleOut])
def list_rules(db: Session = Depends(get_db)):
    from app.models.automation_rule import AutomationRule
    return db.query(AutomationRule).all()


@router.post("/rules", response_model=AutomationRuleOut, status_code=201)
def create_rule(req: AutomationRuleCreate, db: Session = Depends(get_db)):
    return AutomationService(db).create_rule(**req.model_dump())


@router.patch("/rules/{rule_id}", response_model=AutomationRuleOut)
def update_rule(rule_id: int, req: AutomationRuleCreate, db: Session = Depends(get_db)):
    rule = AutomationService(db).update_rule(rule_id, **req.model_dump())
    if not rule:
        raise HTTPException(404, "Rule not found")
    return rule


@router.delete("/rules/{rule_id}", status_code=204)
def delete_rule(rule_id: int, db: Session = Depends(get_db)):
    if not AutomationService(db).delete_rule(rule_id):
        raise HTTPException(404, "Rule not found")
