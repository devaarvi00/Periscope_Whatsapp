import logging
import re
from typing import Any

from sqlalchemy.orm import Session

from app.models.chat import Chat
from app.models.knowledge_item import KnowledgeItem
from app.services.gemini_service import GeminiService

logger = logging.getLogger(__name__)

AI_STATES = ("INACTIVE", "ACTIVE", "THINKING", "SNOOZED")


class AIAgentService:
    def __init__(self, db: Session) -> None:
        self.db = db
        self.gemini = GeminiService()

    def set_ai_state(self, chat: Chat, state: str) -> None:
        if state in AI_STATES:
            chat.ai_state = state
            self.db.commit()

    def _within_operating_hours(self, cfg) -> bool:
        if not cfg.hours_start or not cfg.hours_end:
            return True
        from datetime import datetime
        now = datetime.now().strftime("%H:%M")
        if cfg.hours_start <= cfg.hours_end:
            return cfg.hours_start <= now <= cfg.hours_end
        # window crosses midnight, e.g. 22:00 - 06:00
        return now >= cfg.hours_start or now <= cfg.hours_end

    def _snooze_expired(self, chat: Chat, cfg) -> bool:
        """SNOOZED chats wake up once the configured quiet period has passed."""
        if chat.ai_state != "SNOOZED":
            return True
        if not chat.ai_snoozed_at:
            return False
        from datetime import datetime, timedelta
        return datetime.utcnow() - chat.ai_snoozed_at >= timedelta(
            seconds=max(0, cfg.snooze_after_human_seconds or 0)
        )

    async def handle_incoming_message(
        self,
        chat: Chat,
        message_body: str,
        recent_messages: list[dict[str, Any]],
    ) -> str | None:
        """Process an incoming message and return an AI reply if appropriate."""
        from app.models.ai_settings import build_persona_prompt, get_ai_settings

        cfg = get_ai_settings(self.db)
        if not cfg.enabled or not chat.ai_active:
            return None
        if not self._within_operating_hours(cfg):
            return None
        if chat.ai_state == "SNOOZED":
            if not self._snooze_expired(chat, cfg):
                return None
            chat.ai_state = "ACTIVE"
            chat.ai_snoozed_at = None
            self.db.commit()

        persona = build_persona_prompt(cfg)
        self.set_ai_state(chat, "THINKING")
        try:
            rules = (
                f"Activation rules from the business (follow strictly): {cfg.activation_rules}"
                if cfg.activation_rules else ""
            )
            classification = await self.gemini.classify_for_ai_agent(message_body, rules=rules)
            if not classification.get("should_respond", True):
                self.set_ai_state(chat, "ACTIVE")
                return None

            # Let a human take it first if a response delay is configured
            if cfg.response_delay_seconds:
                import asyncio
                await asyncio.sleep(min(cfg.response_delay_seconds, 300))
                self.db.refresh(chat)
                if chat.ai_state == "SNOOZED" or not chat.ai_active:
                    return None

            knowledge = self._get_relevant_knowledge(message_body)
            context = "\n".join(
                f"[{'Me' if m.get('from_me') else m.get('sender_name','Customer')}]: {m.get('body','')}"
                for m in recent_messages[-10:]
            )

            if knowledge:
                reply = await self.gemini.answer_from_knowledge(message_body, knowledge, persona=persona)
            else:
                reply = await self.gemini.generate_reply(context, persona=persona)

            self.set_ai_state(chat, "ACTIVE")
            return reply
        except Exception as exc:
            logger.error("AI agent error for chat %s: %s", chat.id, exc)
            self.set_ai_state(chat, "ACTIVE")
            return None

    def _get_relevant_knowledge(self, query: str) -> list[dict[str, Any]]:
        """Rank active knowledge items by keyword overlap with the query."""
        items = (
            self.db.query(KnowledgeItem)
            .filter(KnowledgeItem.status == "active")
            .all()
        )
        if not items:
            return []

        stopwords = {
            "the", "a", "an", "is", "are", "was", "were", "do", "does", "did",
            "can", "could", "will", "would", "how", "what", "when", "where",
            "who", "why", "i", "you", "my", "your", "to", "of", "in", "on",
            "for", "and", "or", "it", "this", "that", "please", "hi", "hello",
        }
        words = {w for w in re.findall(r"[\w']+", query.lower()) if len(w) > 2 and w not in stopwords}

        scored = []
        for k in items:
            haystack = f"{k.title} {k.content}".lower()
            score = sum(1 for w in words if w in haystack)
            scored.append((score, k))
        scored.sort(key=lambda pair: pair[0], reverse=True)

        # Prefer matching items; fall back to the most recent few so the AI
        # always has some context to work with.
        matched = [k for score, k in scored if score > 0][:5]
        if not matched:
            matched = [k for _, k in scored][:3]
        return [{"title": k.title, "content": k.content} for k in matched]

    def human_takeover(self, chat: Chat) -> None:
        """Agent takes over from AI — snooze the AI (auto-wakes after the
        configured quiet period)."""
        from datetime import datetime
        chat.ai_state = "SNOOZED"
        chat.ai_snoozed_at = datetime.utcnow()
        self.db.commit()
        logger.info("AI snoozed for chat %s after human takeover", chat.id)
