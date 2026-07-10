from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.api.auth import get_current_agent
from app.db.session import get_db
from app.models.agent import Agent
from app.schemas.ai_agent import AutomationRuleCreate, AutomationRuleOut
from app.services.activity_service import log_activity
from app.services.automation_service import ACTION_TYPES, TRIGGER_TYPES, AutomationService

router = APIRouter(prefix="/automation", tags=["automation"])


@router.get("/action-types")
def get_action_types():
    return ACTION_TYPES


@router.get("/trigger-types")
def get_trigger_types():
    return TRIGGER_TYPES


@router.get("/rules", response_model=list[AutomationRuleOut])
def list_rules(db: Session = Depends(get_db)):
    from app.models.automation_rule import AutomationRule
    return db.query(AutomationRule).all()


@router.post("/rules", response_model=AutomationRuleOut, status_code=201)
def create_rule(
    req: AutomationRuleCreate,
    db: Session = Depends(get_db),
    agent: Agent = Depends(get_current_agent),
):
    rule = AutomationService(db).create_rule(**req.model_dump())
    log_activity(
        db, "automation_rule_created", entity_type="automation_rule", entity_id=rule.id,
        agent_id=agent.id, description=f"Rule '{rule.name}' created ({rule.trigger_type})",
    )
    return rule


@router.patch("/rules/{rule_id}", response_model=AutomationRuleOut)
def update_rule(
    rule_id: int,
    req: AutomationRuleCreate,
    db: Session = Depends(get_db),
    agent: Agent = Depends(get_current_agent),
):
    rule = AutomationService(db).update_rule(rule_id, **req.model_dump())
    if not rule:
        raise HTTPException(404, "Rule not found")
    log_activity(
        db, "automation_rule_updated", entity_type="automation_rule", entity_id=rule.id,
        agent_id=agent.id, description=f"Rule '{rule.name}' updated",
    )
    return rule


@router.delete("/rules/{rule_id}", status_code=204)
def delete_rule(
    rule_id: int,
    db: Session = Depends(get_db),
    agent: Agent = Depends(get_current_agent),
):
    if not AutomationService(db).delete_rule(rule_id):
        raise HTTPException(404, "Rule not found")
    log_activity(
        db, "automation_rule_deleted", entity_type="automation_rule", entity_id=rule_id,
        agent_id=agent.id, description=f"Automation rule #{rule_id} deleted",
    )
