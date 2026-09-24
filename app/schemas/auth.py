from typing import Literal

from pydantic import BaseModel, EmailStr, Field, StrictBool

# bcrypt only hashes the first 72 bytes (bcrypt>=5 raises beyond that);
# hash_password() also enforces the byte limit for multi-byte characters.
PASSWORD_MIN_LENGTH = 8
PASSWORD_MAX_LENGTH = 72


class LoginRequest(BaseModel):
    # Plain str: login only looks the address up. EmailStr rejects reserved
    # domains such as .local, which locked out admin@hyperscope.local.
    email: str = Field(max_length=254)
    password: str = Field(max_length=256)


class ChangePasswordRequest(BaseModel):
    current_password: str = Field(max_length=256)
    new_password: str = Field(min_length=PASSWORD_MIN_LENGTH, max_length=PASSWORD_MAX_LENGTH)


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    agent_id: int
    name: str
    email: str
    role: str


class AgentCreate(BaseModel):
    email: EmailStr
    name: str
    password: str = Field(min_length=PASSWORD_MIN_LENGTH, max_length=PASSWORD_MAX_LENGTH)
    role: str = "agent"


class AgentOut(BaseModel):
    id: int
    email: str
    name: str
    role: str
    is_active: bool
    avatar_color: str

    model_config = {"from_attributes": True}


# ── Notification preferences ──────────────────────────────────────── #
# StrictBool: reject "yes"/1 so a typo'd client can't silently flip a setting.

class NotificationTypes(BaseModel):
    new_messages: StrictBool = True
    new_note: StrictBool = True
    ticket_assign: StrictBool = True
    task_assign: StrictBool = True
    chat_assign: StrictBool = True
    ticket_overdue: StrictBool = True
    task_overdue: StrictBool = True

    model_config = {"extra": "forbid"}


class NotificationPrefs(BaseModel):
    in_app: StrictBool = True
    desktop: StrictBool = False
    sound: StrictBool = False
    types: NotificationTypes = Field(default_factory=NotificationTypes)

    model_config = {"extra": "forbid"}


class NotificationTypesUpdate(BaseModel):
    new_messages: StrictBool | None = None
    new_note: StrictBool | None = None
    ticket_assign: StrictBool | None = None
    task_assign: StrictBool | None = None
    chat_assign: StrictBool | None = None
    ticket_overdue: StrictBool | None = None
    task_overdue: StrictBool | None = None

    model_config = {"extra": "forbid"}


class NotificationPrefsUpdate(BaseModel):
    """Partial update: omitted keys keep their stored value."""

    in_app: StrictBool | None = None
    desktop: StrictBool | None = None
    sound: StrictBool | None = None
    types: NotificationTypesUpdate | None = None

    model_config = {"extra": "forbid"}


# ── Profile / team management ─────────────────────────────────────── #

AVATAR_COLOR_PATTERN = r"^#[0-9a-fA-F]{6}$"


class ProfileUpdate(BaseModel):
    """PATCH /auth/me — an agent editing their own profile."""

    name: str | None = Field(None, min_length=1, max_length=255)
    avatar_color: str | None = Field(None, pattern=AVATAR_COLOR_PATTERN)

    model_config = {"extra": "forbid"}


class AgentAdminUpdate(BaseModel):
    """PATCH /auth/agents/{id} — admin-only role / status / name change."""

    name: str | None = Field(None, min_length=1, max_length=255)
    role: Literal["admin", "agent", "viewer"] | None = None
    is_active: StrictBool | None = None

    model_config = {"extra": "forbid"}


class AgentListOut(AgentOut):
    online: bool = False
    phone_ids: list[int] = Field(default_factory=list)  # empty = every number


# ── Interface preferences (per agent) ─────────────────────────────── #
# Every key is optional: None = "not chosen yet", so the browser keeps its
# local value. unread_sync decides what opening a chat does:
#   shared   – clear the team-wide unread count in Hyperscope (default)
#   phone    – also send a read receipt to WhatsApp (sendSeen)
#   personal – touch neither; only this agent's read marker is recorded

UnreadSync = Literal["shared", "phone", "personal"]


class UiPrefs(BaseModel):
    sidebar_expanded: StrictBool | None = None
    detail_panel_open: StrictBool | None = None
    theme: Literal["light", "dark"] | None = None
    ask_ai_visible: StrictBool | None = None
    unread_sync: UnreadSync | None = None

    model_config = {"extra": "forbid"}


class UiPrefsUpdate(UiPrefs):
    """Partial update: omitted keys keep their stored value."""
