import logging
import re
from typing import Any

from sqlalchemy.orm import Session

from app.models.agent import Agent, AgentRole
from app.models.automation_rule import AutomationRule
from app.models.chat import Chat, ChatLabel
from app.models.label import Label
from app.models.note import Note
from app.models.ticket import Ticket, TicketPriority
from app.services.activity_service import log_activity

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
    "escalate",
]

# Criteria operators supported in condition objects:
# {"field": "message", "op": "contains", "value": "refund"}
_OPERATORS = ("contains", "not_contains", "equals", "not_equals",
              "starts_with", "ends_with", "regex", "in")


async def fire_trigger(trigger_type: str, context: dict[str, Any]) -> None:
    """Run automation rules in a fresh DB session — safe for BackgroundTasks."""
    from app.db.session import SessionLocal

    db = SessionLocal()
    try:
        await AutomationService(db).run_rules(trigger_type, context)
    except Exception as exc:
        logger.exception("Automation trigger %s failed: %s", trigger_type, exc)
    finally:
        db.close()


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
                if taken:
                    log_activity(
                        self.db, "automation_rule_executed",
                        entity_type="automation_rule", entity_id=rule.id,
                        description=f"Rule '{rule.name}' ran on {trigger_type}: {', '.join(taken)}",
                        metadata={"trigger": trigger_type, "chat_id": context.get("chat_id"),
                                  "actions": taken},
                    )
        return actions_taken

    # ── Criteria matching ─────────────────────────────────────────────────────

    def _matches_criteria(self, rule: AutomationRule, context: dict[str, Any]) -> bool:
        criteria = rule.criteria or {}
        if not criteria:
            return True

        # Structured form: {"match": "all"|"any", "conditions": [{field, op, value}, ...]}
        conditions = criteria.get("conditions")
        if isinstance(conditions, list):
            match_mode = str(criteria.get("match", "all")).lower()
            results = [self._eval_condition(c, context) for c in conditions if isinstance(c, dict)]
            if not results:
                return True
            return any(results) if match_mode == "any" else all(results)

        # Keyword shorthand: {"keywords": ["refund", "cancel"]} — any keyword in message
        keywords = criteria.get("keywords")
        if isinstance(keywords, list):
            message = str(context.get("message", "")).lower()
            if not any(str(k).lower() in message for k in keywords):
                return False
            rest = {k: v for k, v in criteria.items() if k != "keywords"}
            return self._legacy_match(rest, context)

        # Legacy form: {key: expected} — substring for strings, equality otherwise
        return self._legacy_match(criteria, context)

    def _legacy_match(self, criteria: dict[str, Any], context: dict[str, Any]) -> bool:
        for key, expected in criteria.items():
            actual = context.get(key)
            if isinstance(expected, str) and isinstance(actual, str):
                if expected.lower() not in actual.lower():
                    return False
            elif actual != expected:
                return False
        return True

    def _eval_condition(self, cond: dict[str, Any], context: dict[str, Any]) -> bool:
        field = str(cond.get("field", ""))
        op = str(cond.get("op", "contains")).lower()
        expected = cond.get("value")
        actual = context.get(field)

        if op == "in":
            options = expected if isinstance(expected, list) else [expected]
            return actual in options or str(actual) in [str(o) for o in options]

        a = "" if actual is None else str(actual)
        e = "" if expected is None else str(expected)
        al, el = a.lower(), e.lower()

        if op == "contains":
            return el in al
        if op == "not_contains":
            return el not in al
        if op == "equals":
            return al == el or actual == expected
        if op == "not_equals":
            return al != el and actual != expected
        if op == "starts_with":
            return al.startswith(el)
        if op == "ends_with":
            return al.endswith(el)
        if op == "regex":
            try:
                return re.search(e, a, re.IGNORECASE) is not None
            except re.error:
                return False
        logger.warning("Unknown automation operator '%s'", op)
        return False

    # ── Actions ───────────────────────────────────────────────────────────────

    async def _execute_actions(self, rule: AutomationRule, context: dict[str, Any]) -> list[str]:
        actions_taken: list[str] = []
        for action in (rule.actions or []):
            action_type = action.get("type")
            try:
                result = await self._execute_action(rule, action_type, action, context)
                if result:
                    actions_taken.append(result)
            except Exception as exc:
                logger.warning("Automation action %s failed: %s", action_type, exc)
        return actions_taken

    async def _execute_action(
        self, rule: AutomationRule, action_type: str,
        action: dict[str, Any], context: dict[str, Any],
    ) -> str | None:
        chat_id = context.get("chat_id")
        chat: Chat | None = (
            self.db.query(Chat).filter(Chat.id == chat_id).first() if chat_id else None
        )

        if action_type == "send_message":
            if not chat:
                return None
            text = self._render_template(str(action.get("message", "")), chat, context)
            if not text:
                return None
            await self._send_whatsapp(chat, text)
            return "message_sent"

        if action_type == "assign_to_agent":
            if not chat:
                return None
            agent_id = action.get("agent_id")
            if agent_id in ("round_robin", None, ""):
                agent_id = self._round_robin_agent()
            if not agent_id:
                return None
            chat.assigned_to = int(agent_id)
            self.db.commit()
            from app.core.ws_manager import ws_manager
            await ws_manager.emit_chat_updated(chat.id, {"assigned_to": chat.assigned_to})
            return f"assigned_agent:{agent_id}"

        if action_type == "create_ticket":
            if not chat:
                return None
            title = self._render_template(
                str(action.get("title", "")) or f"Auto ticket: {context.get('message', '')[:80]}",
                chat, context,
            )
            ticket = Ticket(
                chat_id=chat.id,
                title=title[:500] or "Automated ticket",
                description=str(context.get("message", ""))[:2000],
                priority=self._parse_priority(action.get("priority")),
                assigned_to=action.get("agent_id"),
            )
            self.db.add(ticket)
            self.db.commit()
            from app.core.ws_manager import ws_manager
            await ws_manager.emit_ticket_event("ticket_created", ticket.id, {"chat_id": chat.id})
            return f"ticket_created:{ticket.id}"

        if action_type in ("add_label", "remove_label"):
            if not chat:
                return None
            label_id = self._resolve_label(action)
            if not label_id:
                return None
            existing = (
                self.db.query(ChatLabel)
                .filter(ChatLabel.chat_id == chat.id, ChatLabel.label_id == label_id)
                .first()
            )
            if action_type == "add_label" and not existing:
                self.db.add(ChatLabel(chat_id=chat.id, label_id=label_id))
                self.db.commit()
                await self.run_rules("label_added", {
                    "chat_id": chat.id, "label_id": label_id, "source": "automation",
                })
                return f"label_added:{label_id}"
            if action_type == "remove_label" and existing:
                self.db.delete(existing)
                self.db.commit()
                return f"label_removed:{label_id}"
            return None

        if action_type == "flag_chat":
            if not chat:
                return None
            chat.is_flagged = True
            self.db.commit()
            return "chat_flagged"

        if action_type == "archive_chat":
            if not chat:
                return None
            chat.is_archived = True
            self.db.commit()
            return "chat_archived"

        if action_type == "activate_ai":
            if not chat:
                return None
            chat.ai_active = True
            chat.ai_state = "ACTIVE"
            self.db.commit()
            return "ai_activated"

        if action_type == "send_note":
            if not chat:
                return None
            content = self._render_template(str(action.get("message", "")), chat, context)
            if not content:
                return None
            self.db.add(Note(chat_id=chat.id, agent_id=action.get("agent_id"), content=content))
            self.db.commit()
            return "note_added"

        if action_type == "escalate":
            from app.core.ws_manager import ws_manager
            reason = self._render_template(
                str(action.get("message", "")) or f"Escalation from rule '{rule.name}'",
                chat, context,
            )
            await ws_manager.broadcast("escalation", {
                "chat_id": chat.id if chat else None,
                "rule": rule.name,
                "reason": reason,
                "message": context.get("message", ""),
            })
            if chat:
                chat.is_flagged = True
                self.db.commit()
            log_activity(
                self.db, "escalation", entity_type="chat",
                entity_id=chat.id if chat else None,
                description=reason,
            )
            return "escalated"

        logger.warning("Unknown automation action '%s'", action_type)
        return None

    # ── Helpers ───────────────────────────────────────────────────────────────

    def _round_robin_agent(self) -> int | None:
        """Distribute chats to the active agent with the fewest assigned open chats."""
        agents = (
            self.db.query(Agent)
            .filter(Agent.is_active == True, Agent.role != AgentRole.VIEWER)
            .all()
        )
        if not agents:
            return None
        counts = {
            a.id: self.db.query(Chat).filter(
                Chat.assigned_to == a.id, Chat.is_archived == False
            ).count()
            for a in agents
        }
        return min(counts, key=counts.get)

    def _resolve_label(self, action: dict[str, Any]) -> int | None:
        label_id = action.get("label_id")
        if label_id:
            return int(label_id)
        name = action.get("label_name") or action.get("label")
        if not name:
            return None
        label = self.db.query(Label).filter(Label.name == str(name)).first()
        if not label:
            label = Label(name=str(name))
            self.db.add(label)
            self.db.commit()
            self.db.refresh(label)
        return label.id

    def _parse_priority(self, value: Any) -> TicketPriority:
        try:
            return TicketPriority(str(value).lower())
        except (ValueError, AttributeError):
            return TicketPriority.MEDIUM

    def _render_template(self, text: str, chat: Chat | None, context: dict[str, Any]) -> str:
        """Replace {{name}}, {{message}}, {{chat_name}} variables in action text."""
        if not text:
            return text
        values = {
            "name": (chat.name if chat else "") or "",
            "chat_name": (chat.name if chat else "") or "",
            "message": str(context.get("message", "")),
        }
        for key, val in values.items():
            text = text.replace("{{" + key + "}}", val).replace("{{ " + key + " }}", val)
        return text

    async def _send_whatsapp(self, chat: Chat, text: str) -> None:
        from app.models.phone import Phone
        from app.services.inbox_service import InboxService
        from app.services.waha_service import WAHAService
        from datetime import datetime

        phone = self.db.query(Phone).filter(Phone.id == chat.phone_id).first()
        if not phone:
            return
        waha = WAHAService(session_name=phone.session_name)
        result = await waha.send_text(chat.chat_wid, text)
        InboxService(self.db).upsert_message({
            "chat_id": chat.id,
            "phone_id": phone.id,
            "message_wid": result.message_id or f"auto_{datetime.utcnow().timestamp()}",
            "from_me": True,
            "sender_name": "Automation",
            "sender_number": phone.phone_number,
            "body": text,
            "message_type": "text",
            "timestamp": datetime.utcnow(),
        })
