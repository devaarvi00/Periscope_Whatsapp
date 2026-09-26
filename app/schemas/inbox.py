from typing import Literal

from pydantic import BaseModel, Field, computed_field


class SendMessageRequest(BaseModel):
    chat_id: int
    body: str
    phone_id: int | None = None
    message_type: str = "text"  # text|image|file
    media_url: str | None = None


class ChatUpdateRequest(BaseModel):
    """Partial update. Only fields present in the request body are applied
    (routes use exclude_unset), so `"assigned_to": null` unassigns."""

    is_flagged: bool | None = None
    is_archived: bool | None = None
    is_pinned: bool | None = None
    ai_active: bool | None = None
    # Per-chat opt-out of AI auto-flagging (missing on a chat = allowed)
    ai_flagging: bool | None = None
    assigned_to: int | None = None
    status: Literal["open", "resolved"] | None = None


class PhoneOut(BaseModel):
    id: int
    name: str
    phone_number: str
    session_name: str
    waha_status: str
    is_active: bool
    waha_base_url: str | None = None
    # Never serialised — only used to compute has_waha_key
    waha_api_key: str | None = Field(default=None, exclude=True)

    @computed_field  # type: ignore[prop-decorator]
    @property
    def has_waha_key(self) -> bool:
        return bool(self.waha_api_key)

    model_config = {"from_attributes": True}
