from datetime import datetime

from pydantic import BaseModel


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
    waha_base_url: str | None = None
    waha_api_key: str | None = None

    model_config = {"from_attributes": True}
