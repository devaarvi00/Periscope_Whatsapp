import logging
from typing import Any

from sqlalchemy.orm import Session

from app.models.automation_rule import AutomationRule
from app.models.chat import Chat
from app.models.ticket import Ticket

logger = logging.getLogger(__name__)

TRIGGER_TYPES = [
    "message_received",
    "message_keyword",
    "chat_created",
    "ticket_created",
    "ticket_updated",
    "chat_assigned",
    "no_reply_timeout",
    "label_added",
]

ACTION_TYPES = [
    "send_message",
    "assign_to_agent",
    "create_ticket",
    "add_label",
    "remove_label",
    "flag_chat",
    "archive_chat",
    "activate_ai",
    "send_note",
]


class AutomationService:
    def __init__(self, db: Session) -> None:
        self.db = db

    def get_active_rules(self, trigger_type: str) -> list[AutomationRule]:
        return (
            self.db.query(AutomationRule)
            .filter(AutomationRule.is_active == True, AutomationRule.trigger_type == trigger_type)
            .all()
        )

    def create_rule(self, **kwargs: Any) -> AutomationRule:
        rule = AutomationRule(**kwargs)
        self.db.add(rule)
        self.db.commit()
        self.db.refresh(rule)
        return rule

    def update_rule(self, rule_id: int, **kwargs: Any) -> AutomationRule | None:
        rule = self.db.query(AutomationRule).filter(AutomationRule.id == rule_id).first()
        if not rule:
            return None
        for k, v in kwargs.items():
            if hasattr(rule, k):
                setattr(rule, k, v)
        self.db.commit()
        self.db.refresh(rule)
        return rule

    def delete_rule(self, rule_id: int) -> bool:
        rule = self.db.query(AutomationRule).filter(AutomationRule.id == rule_id).first()
        if not rule:
            return False
        self.db.delete(rule)
        self.db.commit()
        return True

    async def run_rules(self, trigger_type: str, context: dict[str, Any]) -> list[str]:
        """Evaluate and execute matching rules. Returns list of actions taken."""
        rules = self.get_active_rules(trigger_type)
        actions_taken: list[str] = []
        for rule in rules:
            if self._matches_criteria(rule, context):
                taken = await self._execute_actions(rule, context)
                actions_taken.extend(taken)
                rule.runs_count = (rule.runs_count or 0) + 1
                self.db.commit()
        return actions_taken

    def _matches_criteria(self, rule: AutomationRule, context: dict[str, Any]) -> bool:
        criteria = rule.criteria or {}
        if not criteria:
            return True
        for key, expected in criteria.items():
            actual = context.get(key)
            if isinstance(expected, str) and isinstance(actual, str):
                if expected.lower() not in actual.lower():
                    return False
            elif actual != expected:
                return False
        return True

    async def _execute_actions(self, rule: AutomationRule, context: dict[str, Any]) -> list[str]:
        actions_taken: list[str] = []
        for action in (rule.actions or []):
            action_type = action.get("type")
            try:
                if action_type == "add_label":
                    actions_taken.append(f"add_label:{action.get('label_id')}")
                elif action_type == "assign_to_agent":
                    chat_id = context.get("chat_id")
                    if chat_id:
                        self.db.query(Chat).filter(Chat.id == chat_id).update(
                            {"assigned_to": action.get("agent_id")}
                        )
                        self.db.commit()
                    actions_taken.append(f"assigned_agent:{action.get('agent_id')}")
                elif action_type == "flag_chat":
                    chat_id = context.get("chat_id")
                    if chat_id:
                        self.db.query(Chat).filter(Chat.id == chat_id).update({"is_flagged": True})
                        self.db.commit()
                    actions_taken.append("chat_flagged")
                elif action_type == "activate_ai":
                    chat_id = context.get("chat_id")
                    if chat_id:
                        self.db.query(Chat).filter(Chat.id == chat_id).update(
                            {"ai_active": True, "ai_state": "ACTIVE"}
                        )
                        self.db.commit()
                    actions_taken.append("ai_activated")
                else:
                    actions_taken.append(f"action:{action_type}")
            except Exception as exc:
                logger.warning("Automation action %s failed: %s", action_type, exc)
        return actions_taken
