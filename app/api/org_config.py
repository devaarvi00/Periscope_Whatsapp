"""Settings → Config, Permissions, Tickets, Group Settings (+ media library).

Endpoints (all under /api/v1, JWT required):
  GET   /org/config            any agent — config, tickets, groups sections
  PATCH /org/config            admin (tickets: also agents with the Tickets
                               settings screen; groups: Group Templates screen)
  GET   /org/permissions       any agent — org switches + this agent's
                               effective permissions
  PUT   /org/permissions       admin
  GET   /org/group-templates   any agent
  POST/PATCH/DELETE /org/group-templates[/{id}]   admin or Group Templates screen
  /media-library/*             see app/api/media_library.py

Importing this module also installs the screen guards on routers owned by
other modules (see install_screen_guards) — main.py imports it before any
include_router call so the guards are part of the mounted routes.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy.orm import Session

from app.api.auth import get_current_agent
from app.api.media_library import router as media_library_router
from app.db.session import get_db
from app.models.agent import Agent
from app.models.org_config import (
    ACTION_KEYS, SCREEN_KEYS, GroupTemplate, get_org_config, update_org_config,
)
from app.services.access import (
    effective_permissions, is_admin, require_admin, require_screen, screen_guard,
)
from app.services.translation import LANGUAGES, gemini_configured

router = APIRouter(tags=["org-config"])
router.include_router(media_library_router)


# ── Config / Tickets / Group invites ─────────────────────────────────────── #

class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ConfigSection(_Strict):
    translation_enabled: bool | None = None
    translation_language: str | None = None
    auto_translate: bool | None = None
    show_sender_names: bool | None = None
    mask_phone_numbers: bool | None = None
    show_deleted_messages: bool | None = None
    active_phone_right: bool | None = None

    @field_validator("translation_language")
    @classmethod
    def _lang(cls, v: str | None) -> str | None:
        if v is not None and v not in LANGUAGES:
            raise ValueError("Unsupported language")
        return v


class TicketsSection(_Strict):
    prefix: str | None = Field(None, pattern=r"^([A-Za-z]{3})?$")
    auto_attach: bool | None = None
    emoji_ticketing: bool | None = None
    auto_message: bool | None = None
    auto_message_template: str | None = Field(None, max_length=1000)

    @field_validator("prefix")
    @classmethod
    def _upper(cls, v: str | None) -> str | None:
        return v.upper() if v else v

    @field_validator("auto_message_template")
    @classmethod
    def _needs_placeholder(cls, v: str | None) -> str | None:
        if v is not None:
            v = v.strip()
            if "{{ticket_id}}" not in v:
                raise ValueError("The template must include {{ticket_id}}")
        return v


class GroupsSection(_Strict):
    invite_message_enabled: bool | None = None
    invite_template: str | None = Field(None, max_length=1000)

    @field_validator("invite_template")
    @classmethod
    def _not_blank(cls, v: str | None) -> str | None:
        if v is not None and not v.strip():
            raise ValueError("The invite template can't be empty")
        return v.strip() if v else v


class OrgConfigUpdate(_Strict):
    config: ConfigSection | None = None
    tickets: TicketsSection | None = None
    groups: GroupsSection | None = None


def _config_out(cfg: dict) -> dict:
    return {
        "config": cfg["config"],
        "tickets": cfg["tickets"],
        "groups": cfg["groups"],
        "languages": [{"code": k, "name": v} for k, v in LANGUAGES.items()],
        "gemini_configured": gemini_configured(),
        # Media files are only ever served through the authenticated proxy
        # (/media/{id}/file, /media-library/{id}/file) — there is no public
        # link mode to switch to, so Media Privacy is always on.
        "media_privacy": {"enabled": True, "locked": True},
    }


@router.get("/org/config")
def get_config(db: Session = Depends(get_db)):
    return _config_out(get_org_config(db))


@router.patch("/org/config")
def patch_config(
    req: OrgConfigUpdate,
    db: Session = Depends(get_db),
    agent: Agent = Depends(get_current_agent),
):
    sections = {k: v for k, v in req.model_dump(exclude_none=True).items() if v}
    if not sections:
        raise HTTPException(400, "Nothing to update")
    if "config" in sections:
        require_admin(agent, "Only admins can change message settings")
    if "tickets" in sections:
        require_screen(db, agent, "tickets", "ticket settings")
    if "groups" in sections:
        require_screen(db, agent, "group_templates", "group settings")
    cfg = None
    for section, values in sections.items():
        cfg = update_org_config(db, section, values)
    return _config_out(cfg or get_org_config(db))


# ── Permissions ──────────────────────────────────────────────────────────── #

class PermissionsUpdate(_Strict):
    actions: dict[str, bool] | None = None
    screens: dict[str, bool] | None = None

    @field_validator("actions")
    @classmethod
    def _actions(cls, v: dict | None) -> dict | None:
        bad = set(v or {}) - set(ACTION_KEYS)
        if bad:
            raise ValueError("Unknown action(s): " + ", ".join(sorted(bad)))
        return v

    @field_validator("screens")
    @classmethod
    def _screens(cls, v: dict | None) -> dict | None:
        bad = set(v or {}) - set(SCREEN_KEYS)
        if bad:
            raise ValueError("Unknown screen(s): " + ", ".join(sorted(bad)))
        return v


def _permissions_out(db: Session, agent: Agent) -> dict:
    perms = get_org_config(db)["permissions"]
    return {
        "actions": perms["actions"],
        "screens": perms["screens"],
        "is_admin": is_admin(agent),
        # What the calling agent may actually do (admins: everything)
        "effective": effective_permissions(db, agent),
    }


@router.get("/org/permissions")
def get_permissions(db: Session = Depends(get_db), agent: Agent = Depends(get_current_agent)):
    return _permissions_out(db, agent)


@router.put("/org/permissions")
def put_permissions(
    req: PermissionsUpdate,
    db: Session = Depends(get_db),
    agent: Agent = Depends(get_current_agent),
):
    require_admin(agent, "Only admins can change permissions")
    values = req.model_dump(exclude_none=True)
    if values:
        update_org_config(db, "permissions", values)
    from app.services.activity_service import log_activity
    log_activity(db, "org_permissions_updated", entity_type="org", agent_id=agent.id,
                 description=f"{agent.name} updated organization permissions")
    return _permissions_out(db, agent)


# ── Group templates ──────────────────────────────────────────────────────── #

def _clean_numbers(values: list[str]) -> list[str]:
    """Digits only, 7–15 long, de-duplicated (default participants)."""
    import re
    out: list[str] = []
    for raw in values:
        d = re.sub(r"\D", "", raw or "")
        if not d:
            continue
        if not 7 <= len(d) <= 15:
            raise ValueError(f"Invalid phone number: {raw}")
        if d not in out:
            out.append(d)
    return out


class GroupTemplateIn(_Strict):
    name: str = Field(min_length=1, max_length=100)
    description: str = Field("", max_length=2048)
    participants: list[str] = Field(default_factory=list, max_length=256)
    messages_admin_only: bool = False
    info_admin_only: bool = False

    @field_validator("participants")
    @classmethod
    def _numbers(cls, v: list[str]) -> list[str]:
        return _clean_numbers(v)


class GroupTemplatePatch(_Strict):
    name: str | None = Field(None, min_length=1, max_length=100)
    description: str | None = Field(None, max_length=2048)
    participants: list[str] | None = Field(None, max_length=256)
    messages_admin_only: bool | None = None
    info_admin_only: bool | None = None

    @field_validator("participants")
    @classmethod
    def _numbers(cls, v: list[str] | None) -> list[str] | None:
        return _clean_numbers(v) if v is not None else v


def _template_out(t: GroupTemplate) -> dict:
    return {
        "id": t.id, "name": t.name, "description": t.description or "",
        "participants": list(t.participants or []),
        "messages_admin_only": bool(t.messages_admin_only),
        "info_admin_only": bool(t.info_admin_only),
        "created_at": t.created_at.isoformat() if t.created_at else None,
    }


@router.get("/org/group-templates")
def list_group_templates(db: Session = Depends(get_db)):
    return [_template_out(t) for t in db.query(GroupTemplate).order_by(GroupTemplate.name).all()]


@router.post("/org/group-templates", status_code=201,
             dependencies=[Depends(screen_guard("group_templates", "group templates"))])
def create_group_template(
    req: GroupTemplateIn,
    db: Session = Depends(get_db),
    agent: Agent = Depends(get_current_agent),
):
    t = GroupTemplate(**req.model_dump(), created_by=agent.id)
    t.name = t.name.strip()
    db.add(t)
    db.commit()
    db.refresh(t)
    return _template_out(t)


@router.patch("/org/group-templates/{template_id}",
              dependencies=[Depends(screen_guard("group_templates", "group templates"))])
def update_group_template(template_id: int, req: GroupTemplatePatch, db: Session = Depends(get_db)):
    t = db.get(GroupTemplate, template_id)
    if not t:
        raise HTTPException(404, "Template not found")
    for k, v in req.model_dump(exclude_none=True).items():
        setattr(t, k, v.strip() if k == "name" else v)
    db.commit()
    db.refresh(t)
    return _template_out(t)


@router.delete("/org/group-templates/{template_id}", status_code=204,
               dependencies=[Depends(screen_guard("group_templates", "group templates"))])
def delete_group_template(template_id: int, db: Session = Depends(get_db)):
    t = db.get(GroupTemplate, template_id)
    if not t:
        raise HTTPException(404, "Template not found")
    db.delete(t)
    db.commit()


# ── Screen guards on other modules' routers ──────────────────────────────── #

# (module, screen key, label, routes to guard as {(METHOD, path)} or None = all)
_GUARDS: list[tuple[str, str, str, set[tuple[str, str]] | None]] = [
    ("app.api.analytics", "analytics", "Analytics", None),
    ("app.api.automation", "automation", "Automation", None),
    ("app.api.logs", "logs", "Logs", None),
    # Only the Media page listing — /media/{id}/file also serves chat bubbles
    ("app.api.media", "media", "Media", {("GET", "/media")}),
    # The contacts list page and deletes; the chat panel's per-contact calls stay
    ("app.api.contacts", "contacts", "Contacts", {("GET", "/contacts"), ("DELETE", "/contacts/{contact_id}")}),
    # Settings pages: managing the lists, not using them in the composer
    ("app.api.quick_replies", "quick_replies", "quick reply management",
     {("POST", "/quick-replies"), ("PATCH", "/quick-replies/{qr_id}"), ("DELETE", "/quick-replies/{qr_id}")}),
    ("app.api.properties", "custom_properties", "custom property management",
     {("POST", "/properties/definitions"), ("PATCH", "/properties/definitions/{def_id}"),
      ("DELETE", "/properties/definitions/{def_id}")}),
]


def install_screen_guards() -> None:
    """Add a screen_guard dependency to routes of routers this module doesn't
    own. FastAPI copies route.dependencies when a router is included, so this
    must run before app.include_router — main.py imports this module first."""
    import importlib
    from fastapi.routing import APIRoute
    for module_name, screen, label, only in _GUARDS:
        mod = importlib.import_module(module_name)
        r = getattr(mod, "router", None)
        if r is None or getattr(r, "_org_screen_guarded", False):
            continue
        dep = Depends(screen_guard(screen, label))
        for route in r.routes:
            if not isinstance(route, APIRoute):
                continue
            if only is not None and not any(
                m in route.methods and route.path == p for m, p in only
            ):
                continue
            route.dependencies.append(dep)
        r._org_screen_guarded = True  # type: ignore[attr-defined]


install_screen_guards()
