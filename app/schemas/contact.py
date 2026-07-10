from typing import Any

from pydantic import BaseModel


class ContactCreate(BaseModel):
    phone_number: str
    name: str
    email: str | None = None
    company: str | None = None
    is_masked: bool = False
    custom_properties: dict[str, Any] | None = None


class ContactUpdate(BaseModel):
    name: str | None = None
    email: str | None = None
    company: str | None = None
    is_masked: bool | None = None
    custom_properties: dict[str, Any] | None = None


class ContactOut(BaseModel):
    id: int
    phone_number: str
    name: str
    email: str | None
    company: str | None
    is_masked: bool
    custom_properties: dict[str, Any] | None
    labels: list[int] = []

    model_config = {"from_attributes": True}
