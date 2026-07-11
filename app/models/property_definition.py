from sqlalchemy import Boolean, Integer, JSON, String
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin


class PropertyDefinition(Base, TimestampMixin):
    """Definition of a custom property for chats or tickets.

    Values are stored per-entity in the chats/tickets `custom_properties`
    JSON column, keyed by this definition's id (as a string).
    """

    __tablename__ = "property_definitions"

    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    entity: Mapped[str] = mapped_column(String(20), nullable=False)  # chat|ticket
    section: Mapped[str] = mapped_column(String(100), default="General")
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    prop_type: Mapped[str] = mapped_column(String(20), default="text")  # text|number|date|single_select|multi_select
    options: Mapped[list | None] = mapped_column(JSON, nullable=True)   # for select types
    required: Mapped[bool] = mapped_column(Boolean, default=False)
    sort_order: Mapped[int] = mapped_column(Integer, default=0)
