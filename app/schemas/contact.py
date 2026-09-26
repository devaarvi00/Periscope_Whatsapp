from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field


class ContactCreate(BaseModel):
    phone_number: str = Field(..., max_length=30)
    name: str = Field("", max_length=255)
    email: str | None = Field(None, max_length=255)
    company: str | None = Field(None, max_length=255)
    notes: str | None = Field(None, max_length=5000)
    is_masked: bool = False
    custom_properties: dict[str, Any] | None = None


class ContactUpdate(BaseModel):
    name: str | None = Field(None, max_length=255)
    email: str | None = Field(None, max_length=255)
    company: str | None = Field(None, max_length=255)
    username: str | None = Field(None, max_length=100)
    notes: str | None = Field(None, max_length=5000)
    is_masked: bool | None = None
    custom_properties: dict[str, Any] | None = None


class ContactOut(BaseModel):
    id: int
    phone_number: str | None
    name: str
    email: str | None
    company: str | None
    is_masked: bool
    custom_properties: dict[str, Any] | None
    labels: list[int] = []
    wid: str | None = None
    lid: str | None = None
    pushname: str | None = None
    username: str | None = None
    is_business: bool | None = False
    is_my_contact: bool | None = False
    notes: str | None = None
    source: str | None = None
    synced_at: datetime | None = None
    created_at: datetime | None = None

    model_config = {"from_attributes": True}


class ContactBulkIds(BaseModel):
    ids: list[int] = Field(..., max_length=1000)


class ContactBulkLabel(ContactBulkIds):
    label_id: int
