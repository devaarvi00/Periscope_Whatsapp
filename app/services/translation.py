"""Message translation for Settings → Config → Messages.

Used by the per-message "Translate" action (cached on the message doc) and
by auto-translation of new inbound messages. Only runs when a real Gemini key
is configured, and auto-translation is rate-limited so a busy group can't
burn the Gemini quota.
"""
from __future__ import annotations

import logging
import time
from collections import deque
from datetime import datetime

from app.core.config import settings

logger = logging.getLogger(__name__)

LANGUAGES: dict[str, str] = {
    "en": "English", "hi": "Hindi", "gu": "Gujarati", "mr": "Marathi", "bn": "Bengali",
    "ta": "Tamil", "te": "Telugu", "kn": "Kannada", "ml": "Malayalam", "pa": "Punjabi",
    "ur": "Urdu", "es": "Spanish", "pt": "Portuguese", "fr": "French", "de": "German",
    "it": "Italian", "ar": "Arabic", "id": "Indonesian", "ru": "Russian", "tr": "Turkish",
    "zh": "Chinese", "ja": "Japanese",
}

_SAME = "__SAME__"
AUTO_LIMIT_PER_MINUTE = 20
_auto_calls: deque[float] = deque()


def gemini_configured() -> bool:
    key = (settings.gemini_api_key or "").strip()
    return bool(key) and not key.lower().startswith(("replace", "change-me", "your-"))


def _auto_allowed() -> bool:
    now = time.monotonic()
    while _auto_calls and now - _auto_calls[0] > 60:
        _auto_calls.popleft()
    if len(_auto_calls) >= AUTO_LIMIT_PER_MINUTE:
        return False
    _auto_calls.append(now)
    return True


async def translate_if_foreign(text: str, lang: str, *, auto: bool = True) -> dict | None:
    """{"lang", "text", "auto", "at"} — or None when the text is already in
    `lang`, Gemini isn't configured, the auto rate limit is hit, or the call
    fails."""
    text = (text or "").strip()
    language = LANGUAGES.get(lang)
    if not text or not language or not gemini_configured():
        return None
    if auto and not _auto_allowed():
        logger.info("Auto-translation skipped: rate limit reached")
        return None
    from app.services.gemini_service import GeminiService
    prompt = (
        f"If the WhatsApp message below is already written in {language}, reply with exactly {_SAME}. "
        f"Otherwise translate it to {language} and return only the translation.\n\n{text[:4000]}"
    )
    try:
        out = (await GeminiService()._text(prompt, temperature=0.1)).strip()
    except Exception as exc:
        logger.warning("Translation failed: %s", exc)
        return None
    if not out or out == _SAME or _SAME in out:
        return None
    return {"lang": lang, "text": out[:8000], "auto": auto, "at": datetime.utcnow().isoformat()}
