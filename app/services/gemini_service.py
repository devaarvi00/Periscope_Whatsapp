import json
import logging

import httpx

from app.core.config import settings

logger = logging.getLogger(__name__)

_BASE = "https://generativelanguage.googleapis.com/v1beta/models/"


class GeminiError(Exception):
    pass


class GeminiService:
    def __init__(self) -> None:
        self.url = f"{_BASE}{settings.gemini_model}:generateContent"
        self.headers = {
            "x-goog-api-key": settings.gemini_api_key,
            "Content-Type": "application/json",
        }

    async def summarize_chat(self, messages: list[dict]) -> str:
        convo = "\n".join(
            f"[{m.get('sender_name','?')}]: {m.get('body','')}"
            for m in messages[-40:]
        )
        prompt = f"Summarize this WhatsApp conversation in 3-5 bullet points. Be concise.\n\n{convo}"
        return await self._text(prompt, temperature=0.3)

    async def generate_reply(self, context: str, knowledge: str = "") -> str:
        kb = f"\nKnowledge base context:\n{knowledge}" if knowledge else ""
        prompt = (
            f"You are a helpful customer support agent on WhatsApp.{kb}\n\n"
            f"Recent conversation:\n{context}\n\n"
            "Write a short, natural reply to the last customer message. WhatsApp style, no markdown."
        )
        return await self._text(prompt, temperature=0.7)

    async def translate(self, text: str, target_language: str) -> str:
        prompt = f"Translate this WhatsApp message to {target_language}. Return only the translation.\n\n{text}"
        return await self._text(prompt, temperature=0.1)

    async def classify_for_ai_agent(self, message: str, rules: str = "") -> dict:
        prompt = f"""You are an AI agent decision engine for WhatsApp customer support.
{rules}
Classify this incoming message and return ONLY valid JSON:
{{"should_respond": true, "is_question": true, "sentiment": "neutral", "suggested_intent": "billing_query"}}

Message: {message}"""
        raw = await self._text(prompt, temperature=0.1)
        try:
            return json.loads(raw)
        except Exception:
            return {"should_respond": True, "is_question": True, "sentiment": "neutral", "suggested_intent": "general"}

    async def answer_from_knowledge(self, question: str, knowledge_items: list[dict]) -> str:
        kb = "\n\n".join(f"Q: {k.get('title')}\nA: {k.get('content')}" for k in knowledge_items)
        prompt = (
            f"Answer this customer question based only on the knowledge base below.\n"
            f"If the answer is not in the knowledge base, say you'll escalate to a human.\n\n"
            f"Knowledge Base:\n{kb}\n\nQuestion: {question}"
        )
        return await self._text(prompt, temperature=0.3)

    async def flag_message(self, message: str, criteria: str) -> bool:
        prompt = (
            f"Should this WhatsApp message be flagged based on these criteria: {criteria}\n"
            f"Message: {message}\n"
            f"Reply with only 'yes' or 'no'."
        )
        result = await self._text(prompt, temperature=0.1)
        return result.strip().lower().startswith("yes")

    async def _text(self, prompt: str, temperature: float = 0.5) -> str:
        payload = {
            "contents": [{"parts": [{"text": prompt}]}],
            "generationConfig": {"temperature": temperature},
        }
        data = await self._call(payload)
        return (
            data.get("candidates", [{}])[0]
            .get("content", {})
            .get("parts", [{}])[0]
            .get("text", "")
            .strip()
        )

    async def _call(self, payload: dict) -> dict:
        from app.core.http_client import get_http_client
        try:
            resp = await get_http_client().post(self.url, json=payload, headers=self.headers)
        except httpx.TimeoutException as exc:
            raise GeminiError("Gemini timeout") from exc
        except httpx.HTTPError as exc:
            raise GeminiError("Gemini transport error") from exc
        if resp.status_code == 429:
            raise GeminiError("Gemini rate limit")
        if not resp.is_success:
            raise GeminiError(f"Gemini error {resp.status_code}")
        try:
            return resp.json()
        except Exception as exc:
            raise GeminiError("Gemini non-JSON response") from exc
