from datetime import datetime
from typing import Any

from pydantic import BaseModel


class ChatOut(BaseModel):
    id: int
    chat_wid: str
    phone_id: int
    name: str
    is_group: bool
    last_message: str | None
    last_message_at: datetime | None
    unread_count: int
    is_flagged: bool
    is_archived: bool
    is_pinned: bool
    ai_active: bool
    ai_state: str
    assigned_to: int | None
    labels: list[int] = []

    model_config = {"from_attributes": True}


class MessageOut(BaseModel):
    id: int
    chat_id: int
    message_wid: str
    from_me: bool
    sender_name: str
    sender_number: str
    sent_by_agent_id: int | None
    body: str
    message_type: str
    has_media: bool
    media_url: str | None
    is_read: bool
    is_flagged: bool
    timestamp: datetime

    model_config = {"from_attributes": True}


class SendMessageRequest(BaseModel):
    chat_id: int
    body: str
    phone_id: int | None = None
    message_type: str = "text"  # text|image|file
    media_url: str | None = None


class ChatUpdateRequest(BaseModel):
    is_flagged: bool | None = None
    is_archived: bool | None = None
    is_pinned: bool | None = None
    ai_active: bool | None = None
    assigned_to: int | None = None


class PhoneOut(BaseModel):
    id: int
    name: str
    phone_number: str
    session_name: str
    waha_status: str
    is_active: bool
    is_default: bool
    waha_base_url: str | None = None
    waha_api_key: str | None = None

    model_config = {"from_attributes": True}


class PhoneCreate(BaseModel):
    name: str
    phone_number: str
    session_name: str
    is_default: bool = False
    waha_base_url: str | None = None
    waha_api_key: str | None = None
