from datetime import datetime

from pydantic import BaseModel

from app.schemas.common import NaiveUTCDatetime


class TicketCreate(BaseModel):
    chat_id: int
    message_wid: str | None = None
    title: str
    description: str = ""
    status: str = "open"
    priority: str = "medium"
    assigned_to: int | None = None
    due_date: NaiveUTCDatetime | None = None  # aware input → naive UTC


class TicketUpdate(BaseModel):
    """Partial update (exclude_unset): `"assigned_to": null` unassigns."""

    title: str | None = None
    description: str | None = None
    status: str | None = None
    priority: str | None = None
    assigned_to: int | None = None
    due_date: NaiveUTCDatetime | None = None


class TicketOut(BaseModel):
    id: int
    chat_id: int
    message_wid: str | None
    title: str
    description: str
    status: str
    priority: str
    assigned_to: int | None
    created_by: int | None
    due_date: datetime | None
    resolved_at: datetime | None
    sla_breached: bool
    created_at: datetime
    updated_at: datetime
    labels: list[int] = []

    model_config = {"from_attributes": True}
