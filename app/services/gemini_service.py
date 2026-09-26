"""Google Gemini client (generateContent REST API).

- Timeouts, and retries with backoff on 429 / 5xx / transport errors
- Safety blocks surface as GeminiBlocked (never an empty "reply")
- JSON mode (responseMimeType + responseSchema) with a lenient fallback parser
- Every call records token usage (usageMetadata) tagged with a purpose
- Message bodies are never logged above DEBUG, and the API key never is
"""
from __future__ import annotations

import asyncio
import json
import logging
import random
import re
import time
from dataclasses import dataclass, field
from typing import Any

import httpx

from app.core.config import settings

logger = logging.getLogger(__name__)

_BASE = "https://generativelanguage.googleapis.com/v1beta/models/"
_RETRY_STATUSES = {429, 500, 502, 503, 504}
_BLOCK_FINISH = {"SAFETY", "PROHIBITED_CONTENT", "BLOCKLIST", "SPII", "RECITATION", "IMAGE_SAFETY"}
MAX_ATTEMPTS = 3
DEFAULT_TIMEOUT = 30.0


class GeminiError(Exception):
    pass


class GeminiBlocked(GeminiError):
    """The prompt or the answer was blocked by Gemini's safety filters."""


@dataclass
class GeminiResult:
    text: str = ""
    data: Any = None          # parsed JSON in JSON mode (None if unparseable)
    model: str = ""
    finish_reason: str = ""
    prompt_tokens: int = 0
    candidate_tokens: int = 0
    total_tokens: int = 0
    latency_ms: int = 0
    raw_content: dict = field(default_factory=dict)


def gemini_configured() -> bool:
    key = (settings.gemini_api_key or "").strip()
    return bool(key) and not key.lower().startswith(("replace", "change-me", "your-"))


def parse_json_loose(text: str) -> Any:
    """Parse model output as JSON: plain, fenced (```json … ```), or the first
    {...} / [...] block inside prose. Returns None when nothing parses."""
    if not text:
        return None
    s = text.strip()
    fence = re.match(r"^```(?:json)?\s*(.*?)\s*```$", s, re.S | re.I)
    if fence:
        s = fence.group(1)
    try:
        return json.loads(s)
    except Exception:
        pass
    for open_c, close_c in (("{", "}"), ("[", "]")):
        start, end = s.find(open_c), s.rfind(close_c)
        if start != -1 and end > start:
            try:
                return json.loads(s[start:end + 1])
            except Exception:
                continue
    return None


# ── Usage accounting ─────────────────────────────────────────────────────────
def _record_usage_db(row: dict) -> None:
    from app.db.session import SessionLocal
    from app.models.ai_records import AIUsage

    db = SessionLocal()
    try:
        db.add(AIUsage(**row))
        db.commit()
    except Exception as exc:  # accounting must never break a reply
        db.rollback()
        logger.warning("Could not record Gemini usage: %s", exc)
    finally:
        db.close()


# Swappable in tests
usage_recorder = _record_usage_db


async def _record(row: dict) -> None:
    try:
        await asyncio.to_thread(usage_recorder, row)
    except Exception as exc:
        logger.warning("Gemini usage recorder failed: %s", exc)


class GeminiService:
    def __init__(self, purpose: str | None = None, chat_id: int | None = None,
                 model: str | None = None) -> None:
        self.model = (model or settings.gemini_model or "").strip()
        self.purpose = purpose
        self.chat_id = chat_id
        self.url = f"{_BASE}{self.model}:generateContent"
        self.headers = {
            "x-goog-api-key": settings.gemini_api_key,
            "Content-Type": "application/json",
        }

    # ── Core call ───────────────────────────────────────────────────────── #
    async def generate(
        self,
        prompt: str | None = None,
        *,
        contents: list[dict] | None = None,
        system: str | None = None,
        schema: dict | None = None,
        json_mode: bool = False,
        temperature: float | None = None,
        max_tokens: int = 1024,
        purpose: str | None = None,
        chat_id: int | None = None,
        timeout: float = DEFAULT_TIMEOUT,
    ) -> GeminiResult:
        if contents is None:
            contents = [{"role": "user", "parts": [{"text": prompt or ""}]}]
        gen: dict[str, Any] = {"maxOutputTokens": max_tokens}
        # Gemini 3 models are tuned for their default temperature; lowering it
        # can cause loops, so only older models get an explicit value.
        if temperature is not None and not self.model.startswith("gemini-3"):
            gen["temperature"] = temperature
        if schema is not None or json_mode:
            gen["responseMimeType"] = "application/json"
            if schema is not None:
                gen["responseSchema"] = schema
        payload: dict[str, Any] = {"contents": contents, "generationConfig": gen}
        if system:
            payload["systemInstruction"] = {"parts": [{"text": system}]}

        purpose = purpose or self.purpose or "other"
        chat_id = chat_id if chat_id is not None else self.chat_id
        started = time.monotonic()
        usage_row = {"model": self.model, "purpose": purpose[:30], "chat_id": chat_id,
                     "prompt_tokens": 0, "candidate_tokens": 0, "total_tokens": 0,
                     "latency_ms": 0, "ok": False}
        try:
            data = await self._call(payload, timeout)
            result = self._parse(data, json_expected=bool(schema is not None or json_mode))
            usage_row.update(prompt_tokens=result.prompt_tokens, candidate_tokens=result.candidate_tokens,
                             total_tokens=result.total_tokens, ok=True)
            result.latency_ms = int((time.monotonic() - started) * 1000)
            return result
        except GeminiBlocked as exc:
            usage = getattr(exc, "usage", None) or {}
            usage_row.update(prompt_tokens=usage.get("promptTokenCount", 0) or 0,
                             total_tokens=usage.get("totalTokenCount", 0) or 0)
            raise
        finally:
            usage_row["latency_ms"] = int((time.monotonic() - started) * 1000)
            logger.debug("Gemini %s purpose=%s ok=%s tokens=%s %dms", self.model, purpose,
                         usage_row["ok"], usage_row["total_tokens"], usage_row["latency_ms"])
            await _record(usage_row)

    def _parse(self, data: dict, json_expected: bool) -> GeminiResult:
        usage = data.get("usageMetadata") or {}
        feedback = data.get("promptFeedback") or {}
        if feedback.get("blockReason"):
            exc = GeminiBlocked(f"Prompt blocked ({feedback.get('blockReason')})")
            exc.usage = usage  # type: ignore[attr-defined]
            raise exc
        candidates = data.get("candidates") or []
        cand = candidates[0] if candidates else {}
        content = cand.get("content") or {}
        parts = content.get("parts") or []
        text = "".join(p.get("text", "") for p in parts if isinstance(p, dict) and not p.get("thought")).strip()
        finish = str(cand.get("finishReason") or "")
        if finish in _BLOCK_FINISH and not text:
            exc = GeminiBlocked(f"Response blocked ({finish})")
            exc.usage = usage  # type: ignore[attr-defined]
            raise exc
        if not candidates:
            raise GeminiError("Gemini returned no candidates")
        cand_tokens = int(usage.get("candidatesTokenCount", 0) or 0) + int(usage.get("thoughtsTokenCount", 0) or 0)
        return GeminiResult(
            text=text,
            data=parse_json_loose(text) if json_expected else None,
            model=str(data.get("modelVersion") or self.model),
            finish_reason=finish,
            prompt_tokens=int(usage.get("promptTokenCount", 0) or 0),
            candidate_tokens=cand_tokens,
            total_tokens=int(usage.get("totalTokenCount", 0) or 0),
            raw_content=content,
        )

    async def _call(self, payload: dict, timeout: float = DEFAULT_TIMEOUT) -> dict:
        if not gemini_configured():
            raise GeminiError("Gemini API key is not configured")
        from app.core.http_client import get_http_client
        try:
            client = get_http_client()
            own_client = None
        except RuntimeError:  # outside the app lifespan (scripts, tests)
            own_client = client = httpx.AsyncClient(timeout=timeout)
        try:
            last_exc: Exception | None = None
            for attempt in range(1, MAX_ATTEMPTS + 1):
                wait = 0.0
                try:
                    resp = await client.post(self.url, json=payload, headers=self.headers, timeout=timeout)
                except httpx.TimeoutException as exc:
                    last_exc = GeminiError("Gemini timeout")
                    last_exc.__cause__ = exc
                except httpx.HTTPError as exc:
                    last_exc = GeminiError("Gemini transport error")
                    last_exc.__cause__ = exc
                else:
                    if resp.is_success:
                        try:
                            return resp.json()
                        except Exception as exc:
                            raise GeminiError("Gemini non-JSON response") from exc
                    msg = self._error_message(resp)
                    if resp.status_code not in _RETRY_STATUSES:
                        raise GeminiError(f"Gemini error {resp.status_code}: {msg}")
                    last_exc = GeminiError(
                        "Gemini rate limit" if resp.status_code == 429 else f"Gemini error {resp.status_code}: {msg}"
                    )
                    try:
                        wait = min(float(resp.headers.get("retry-after") or 0), 8.0)
                    except ValueError:
                        wait = 0.0
                if attempt < MAX_ATTEMPTS:
                    backoff = wait or (0.8 * (2.5 ** (attempt - 1)) + random.uniform(0, 0.3))
                    logger.info("Gemini call failed (%s); retry %d/%d in %.1fs",
                                last_exc, attempt, MAX_ATTEMPTS - 1, backoff)
                    await asyncio.sleep(backoff)
            raise last_exc or GeminiError("Gemini call failed")
        finally:
            if own_client is not None:
                await own_client.aclose()

    @staticmethod
    def _error_message(resp: httpx.Response) -> str:
        try:
            return str((resp.json().get("error") or {}).get("message") or "")[:200]
        except Exception:
            return ""

    async def _text(self, prompt: str, temperature: float = 0.5, purpose: str | None = None,
                    max_tokens: int = 1024) -> str:
        # Bare _text() calls come from translation.py; everything else passes a purpose.
        res = await self.generate(prompt, temperature=temperature, max_tokens=max_tokens,
                                  purpose=purpose or self.purpose or "translate")
        return res.text

    # ── Task helpers (kept for the existing endpoints) ───────────────────── #
    async def summarize_chat(self, messages: list[dict]) -> str:
        convo = "\n".join(
            f"[{m.get('sender_name','?')}]: {m.get('body','')}"
            for m in messages[-40:]
        )
        prompt = f"Summarize this WhatsApp conversation in 3-5 bullet points. Be concise.\n\n{convo}"
        return await self._text(prompt, temperature=0.3, purpose="summary")

    async def generate_reply(self, context: str, knowledge: str = "", persona: str = "") -> str:
        head = persona or "You are a helpful customer support agent on WhatsApp."
        kb = f"\nKnowledge base context:\n{knowledge}" if knowledge else ""
        prompt = (
            f"{head}{kb}\n\n"
            f"Recent conversation:\n{context}\n\n"
            "Write a short, natural reply to the last customer message. WhatsApp style, no markdown."
        )
        return await self._text(prompt, temperature=0.7, purpose="suggest")

    async def polish_reply(self, text: str, tone: str = "professional") -> str:
        prompt = (
            f"Polish this WhatsApp reply draft. Fix grammar and spelling, keep it short and "
            f"natural for WhatsApp (no markdown), and use a {tone} tone. "
            f"Keep the same language and meaning. Return only the polished message.\n\n{text}"
        )
        return await self._text(prompt, temperature=0.3, purpose="polish")

    async def translate(self, text: str, target_language: str) -> str:
        prompt = f"Translate this WhatsApp message to {target_language}. Return only the translation.\n\n{text}"
        return await self._text(prompt, temperature=0.1, purpose="translate")

    async def classify_for_ai_agent(self, message: str, rules: str = "", history: str = "",
                                    chat_id: int | None = None, purpose: str = "classify") -> dict:
        """Decide whether the agent should answer `message` under the business's
        activation rules. Fails open (should_respond=True) on unparseable output."""
        system = (
            "You are the gatekeeper for a WhatsApp customer-support AI agent. Decide whether the "
            "agent should respond to the customer's latest message, following the business's "
            "activation rules exactly.\n\nActivation rules:\n" + (rules or "Respond to questions and requests for help.")
        )
        prompt = (
            (f"Recent conversation (oldest first):\n{history}\n\n" if history else "")
            + f"Latest customer message:\n{message}\n\n"
            "Return JSON: should_respond (boolean) and reason (under 12 words)."
        )
        schema = {
            "type": "OBJECT",
            "properties": {
                "should_respond": {"type": "BOOLEAN"},
                "reason": {"type": "STRING"},
            },
            "required": ["should_respond", "reason"],
        }
        res = await self.generate(prompt, system=system, schema=schema, temperature=0.1,
                                  max_tokens=512, purpose=purpose, chat_id=chat_id)
        data = res.data if isinstance(res.data, dict) else {}
        should = data.get("should_respond")
        if not isinstance(should, bool):
            should = True
        return {"should_respond": should, "reason": str(data.get("reason") or "")[:200],
                "tokens": res.total_tokens}

    async def answer_from_knowledge(self, question: str, knowledge_items: list[dict], persona: str = "") -> str:
        kb = "\n\n".join(f"Q: {k.get('title')}\nA: {k.get('content')}" for k in knowledge_items)
        head = (persona + "\n") if persona else ""
        prompt = (
            f"{head}Answer this customer question based only on the knowledge base below.\n"
            f"If the answer is not in the knowledge base, say you'll escalate to a human.\n\n"
            f"Knowledge Base:\n{kb}\n\nQuestion: {question}"
        )
        return await self._text(prompt, temperature=0.3, purpose="reply")

    async def assistant_answer(self, question: str, context_pack: str) -> str:
        """Org/Chat assistant: answer a workspace question from real data only."""
        prompt = (
            "You are the workspace assistant for a WhatsApp CRM. Answer the team member's "
            "question using ONLY the workspace data below. Be concise and specific — use "
            "names and numbers from the data. If the data doesn't contain the answer, say "
            "so plainly. Format with short lines suitable for a side panel; no markdown tables.\n\n"
            f"=== WORKSPACE DATA ===\n{context_pack}\n\n"
            f"=== QUESTION ===\n{question}"
        )
        return await self._text(prompt, temperature=0.3, purpose="assistant")

    async def flag_message(self, message: str, criteria: str, chat_id: int | None = None) -> bool:
        prompt = (
            f"Should this WhatsApp message be flagged based on these criteria: {criteria}\n"
            f"Message: {message}\n"
            f"Reply with only 'yes' or 'no'."
        )
        res = await self.generate(prompt, temperature=0.1, max_tokens=256, purpose="flag", chat_id=chat_id)
        return res.text.strip().lower().startswith("yes")
