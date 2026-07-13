from datetime import datetime

from pydantic import BaseModel


class KnowledgeItemCreate(BaseModel):
    item_type: str = "faq"
    title: str
    content: str
    status: str = "active"


class KnowledgeItemOut(BaseModel):
    id: int
    item_type: str
    title: str
    content: str
    status: str
    is_self_learned: bool

    model_config = {"from_attributes": True}


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


class LabelCreate(BaseModel):
    name: str
    color: str = "#0D8C7C"


class LabelOut(BaseModel):
    id: int
    name: str
    color: str

    model_config = {"from_attributes": True}


class NoteCreate(BaseModel):
    chat_id: int
    content: str


class NoteOut(BaseModel):
    id: int
    chat_id: int
    agent_id: int
    content: str

    model_config = {"from_attributes": True}


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
