from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

# Hex colour (#rgb, #rgba, #rrggbb, #rrggbbaa) — rendered straight into CSS
LABEL_COLOR_PATTERN = r"^#[0-9a-fA-F]{3,8}$"


class KnowledgeItemCreate(BaseModel):
    item_type: str = "faq"
    title: str = Field(min_length=1, max_length=500)
    content: str = Field(min_length=1, max_length=200_000)
    status: str = "active"


class KnowledgeItemUpdate(BaseModel):
    title: str | None = Field(None, min_length=1, max_length=500)
    content: str | None = Field(None, min_length=1, max_length=200_000)
    status: str | None = None


class KnowledgeItemOut(BaseModel):
    id: int
    item_type: str
    title: str
    content: str
    status: str
    is_self_learned: bool
    source: str | None = None
    origin_chat_id: int | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None

    model_config = {"from_attributes": True}


class ExternalSourceCreate(BaseModel):
    url: str = Field(min_length=8, max_length=1000)
    title: str | None = Field(None, max_length=500)


# ── AI agent settings ──
class DayHours(BaseModel):
    on: bool = False
    start: str = Field("09:00", pattern=r"^([01]\d|2[0-3]):[0-5]\d$")
    end: str = Field("18:00", pattern=r"^([01]\d|2[0-3]):[0-5]\d$")


class AISettingsUpdate(BaseModel):
    """Partial update: fields left out keep their value."""
    enabled: bool | None = None
    auto_activate_new_chats: bool | None = None
    activation_rules: str | None = Field(None, max_length=20_000)
    allowed_phone_ids: list[int] | None = None
    response_delay_seconds: int | None = None
    snooze_after_human_seconds: int | None = None
    hours_enabled: bool | None = None
    hours_schedule: dict[str, DayHours] | None = None
    hours_start: str | None = None
    hours_end: str | None = None
    agent_name: str | None = Field(None, max_length=100)
    personality: str | None = None
    role_description: str | None = Field(None, max_length=20_000)
    custom_instructions: str | None = Field(None, max_length=20_000)
    restrictions: str | None = Field(None, max_length=20_000)
    allow_send_messages: bool | None = None
    allow_create_tickets: bool | None = None
    ticket_instructions: str | None = Field(None, max_length=5_000)
    allow_private_notes: bool | None = None
    note_instructions: str | None = Field(None, max_length=5_000)
    flag_enabled: bool | None = None
    flag_criteria: str | None = Field(None, max_length=5_000)


class PlaygroundMessage(BaseModel):
    role: Literal["customer", "agent"] = "customer"
    text: str = Field(min_length=1, max_length=4000)


class PlaygroundRequest(BaseModel):
    settings: AISettingsUpdate | None = None
    messages: list[PlaygroundMessage] = Field(min_length=1, max_length=40)
    check_rules: bool = True


class InternalContactCreate(BaseModel):
    number: str = Field(min_length=5, max_length=40)
    label: str = Field("", max_length=255)


class CustomToolParam(BaseModel):
    name: str = Field(pattern=r"^[a-zA-Z_][a-zA-Z0-9_]{0,39}$")
    type: Literal["string", "number", "integer", "boolean"] = "string"
    description: str = Field("", max_length=300)
    required: bool = False


class CustomToolIn(BaseModel):
    name: str = Field(pattern=r"^[a-z][a-z0-9_]{1,40}$")
    description: str = Field("", max_length=1000)
    method: Literal["GET", "POST"] = "GET"
    url: str = Field(min_length=8, max_length=1000)
    headers: dict[str, str] | None = None  # omitted on update = keep existing
    params: list[CustomToolParam] = Field(default_factory=list, max_length=12)
    enabled: bool = True
    timeout_seconds: int = Field(8, ge=1, le=15)


class AutomationRuleCreate(BaseModel):
    name: str
    trigger_type: str
    criteria: dict | None = None
    actions: list | None = None
    is_active: bool = True


class AutomationRuleOut(BaseModel):
    id: int
    name: str
    trigger_type: str
    criteria: dict | None
    actions: list | None
    is_active: bool
    runs_count: int

    model_config = {"from_attributes": True}


class QuickReplyCreate(BaseModel):
    command: str
    message: str


class QuickReplyOut(BaseModel):
    id: int
    command: str
    message: str

    model_config = {"from_attributes": True}


LabelType = Literal["chat", "ticket", "phone"]


class LabelCreate(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    color: str = Field("#0D8C7C", pattern=LABEL_COLOR_PATTERN)
    type: LabelType = "chat"


class LabelUpdate(BaseModel):
    """Partial update — fields left out keep their value (so older clients
    that PATCH only name + color never reset a label's type)."""
    name: str | None = Field(None, min_length=1, max_length=100)
    color: str | None = Field(None, pattern=LABEL_COLOR_PATTERN)
    type: LabelType | None = None


class LabelOut(BaseModel):
    id: int
    name: str
    color: str
    type: str = "chat"

    model_config = {"from_attributes": True}


class NoteCreate(BaseModel):
    chat_id: int
    content: str


class BulkJobCreate(BaseModel):
    name: str
    message: str
    phone_id: int
    recipient_chat_ids: list[str]
    scheduled_at: str | None = None
    message_type: str = "text"  # text|image|file|poll
    media_url: str | None = None
    poll_options: list[str] | None = None
    delay_seconds: int = 1
    # Repeating broadcasts
    repeat: str = "none"  # none|daily|weekly|monthly
    interval: int = 1
    days_of_week: list[int] | None = None
    day_of_month: int | None = None
    end_date: str | None = None


class BulkJobOut(BaseModel):
    id: int
    name: str
    status: str
    message: str = ""
    message_type: str = "text"
    media_url: str | None = None
    poll_options: list | None = None
    error_message: str | None = None
    sent_count: int
    failed_count: int
    credits_used: int
    delay_seconds: int = 1
    repeat: str = "none"
    interval: int = 1
    days_of_week: list | None = None
    day_of_month: int | None = None
    end_date: datetime | None = None
    scheduled_at: datetime | None = None
    runs_count: int = 0
    recipient_chat_ids: list | None = None

    model_config = {"from_attributes": True}
