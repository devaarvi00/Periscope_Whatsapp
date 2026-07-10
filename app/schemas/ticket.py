from datetime import datetime

from pydantic import BaseModel


class TicketCreate(BaseModel):
    chat_id: int
    message_id: int | None = None
    title: str
    description: str = ""
    priority: str = "medium"
    assigned_to: int | None = None
    due_date: datetime | None = None


class TicketUpdate(BaseModel):
    title: str | None = None
    description: str | None = None
    status: str | None = None
    priority: str | None = None
    assigned_to: int | None = None
    due_date: datetime | None = None


class TicketOut(BaseModel):
    id: int
    chat_id: int
    message_id: int | None
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
