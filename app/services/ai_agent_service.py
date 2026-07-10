import logging
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

    async def handle_incoming_message(
        self,
        chat: Chat,
        message_body: str,
        recent_messages: list[dict[str, Any]],
    ) -> str | None:
        """Process an incoming message and return an AI reply if appropriate."""
        if not chat.ai_active or chat.ai_state == "SNOOZED":
            return None

        self.set_ai_state(chat, "THINKING")
        try:
            classification = await self.gemini.classify_for_ai_agent(message_body)
            if not classification.get("should_respond", True):
                self.set_ai_state(chat, "ACTIVE")
                return None

            knowledge = self._get_relevant_knowledge(message_body)
            context = "\n".join(
                f"[{'Me' if m.get('from_me') else m.get('sender_name','Customer')}]: {m.get('body','')}"
                for m in recent_messages[-10:]
            )

            if knowledge:
                reply = await self.gemini.answer_from_knowledge(message_body, knowledge)
            else:
                reply = await self.gemini.generate_reply(context)

            self.set_ai_state(chat, "ACTIVE")
            return reply
        except Exception as exc:
            logger.error("AI agent error for chat %s: %s", chat.id, exc)
            self.set_ai_state(chat, "ACTIVE")
            return None

    def _get_relevant_knowledge(self, query: str) -> list[dict[str, Any]]:
        items = (
            self.db.query(KnowledgeItem)
            .filter(KnowledgeItem.status == "active")
            .limit(10)
            .all()
        )
        return [{"title": k.title, "content": k.content} for k in items]

    def human_takeover(self, chat: Chat) -> None:
        """Agent takes over from AI — snooze the AI."""
        chat.ai_state = "SNOOZED"
        self.db.commit()
        logger.info("AI snoozed for chat %s after human takeover", chat.id)
