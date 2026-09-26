"""Admin-defined custom HTTP tools for the AI agent.

A tool is a GET or POST to an admin-configured public URL. The model asks for
a tool by name with JSON arguments; only declared parameters are forwarded
(GET → query string, POST → JSON body). Every request goes through the SSRF
guard, redirects are not followed, the timeout is capped and the response is
truncated before it is shown to the model.
"""
from __future__ import annotations

import json
import logging
import re

import httpx

from app.models.ai_records import AICustomTool

logger = logging.getLogger(__name__)

TOOL_NAME_RE = re.compile(r"^[a-z][a-z0-9_]{1,40}$")
PARAM_TYPES = ("string", "number", "integer", "boolean")
MAX_RESPONSE_CHARS = 4000
MAX_TIMEOUT = 15


def describe_tools(tools: list[AICustomTool]) -> str:
    lines = []
    for t in tools:
        params = t.params or []
        sig = ", ".join(
            f"{p.get('name')}: {p.get('type', 'string')}{'' if p.get('required') else ' (optional)'}"
            + (f" — {p.get('description')}" if p.get("description") else "")
            for p in params
        )
        lines.append(f"- {t.name}({sig}): {t.description or 'no description'}")
    return "\n".join(lines)


def _coerce(value, ptype: str):
    try:
        if ptype == "integer":
            return int(value)
        if ptype == "number":
            return float(value)
        if ptype == "boolean":
            return value if isinstance(value, bool) else str(value).lower() in ("1", "true", "yes")
    except (TypeError, ValueError):
        return None
    return str(value)[:500]


def clean_args(tool: AICustomTool, raw) -> dict:
    if isinstance(raw, str):
        try:
            raw = json.loads(raw or "{}")
        except Exception:
            raw = {}
    raw = raw if isinstance(raw, dict) else {}
    out = {}
    for p in tool.params or []:
        name = p.get("name")
        if name in raw and raw[name] is not None:
            v = _coerce(raw[name], p.get("type", "string"))
            if v is not None:
                out[name] = v
    return out


async def run_custom_tool(tool: AICustomTool, raw_args) -> dict:
    """Returns {"ok": bool, "status": int|None, "result": str}; never raises."""
    from app.services.url_safety import UnsafeURLError, assert_public_url

    args = clean_args(tool, raw_args)
    missing = [p["name"] for p in (tool.params or []) if p.get("required") and p.get("name") not in args]
    if missing:
        return {"ok": False, "status": None, "args": args, "result": f"Missing required arguments: {', '.join(missing)}"}
    try:
        await assert_public_url(tool.url)
    except UnsafeURLError as exc:
        return {"ok": False, "status": None, "args": args, "result": f"Blocked URL: {exc}"}
    headers = {str(k)[:100]: str(v)[:2000] for k, v in (tool.headers or {}).items()}
    timeout = max(1, min(int(tool.timeout_seconds or 8), MAX_TIMEOUT))
    try:
        async with httpx.AsyncClient(timeout=timeout, follow_redirects=False) as client:
            if (tool.method or "GET").upper() == "POST":
                resp = await client.post(tool.url, json=args, headers=headers)
            else:
                resp = await client.get(tool.url, params=args, headers=headers)
        text = resp.text[:MAX_RESPONSE_CHARS]
        return {"ok": resp.is_success, "status": resp.status_code, "args": args, "result": text}
    except httpx.TimeoutException:
        return {"ok": False, "status": None, "args": args, "result": "The tool timed out"}
    except httpx.HTTPError as exc:
        logger.info("Custom tool %s failed: %s", tool.name, type(exc).__name__)
        return {"ok": False, "status": None, "args": args, "result": "The tool request failed"}
