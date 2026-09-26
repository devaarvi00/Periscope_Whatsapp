from sqlalchemy import Boolean, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin

# item_type: faq | self_learned | document | external
KB_TYPES = ("faq", "self_learned", "document", "external")
# status: active | inactive | review  ("archived" is the legacy name for inactive)
KB_STATUSES = ("active", "inactive", "review")


class KnowledgeItem(Base, TimestampMixin):
    __tablename__ = "knowledge_items"

    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    item_type: Mapped[str] = mapped_column(String(20), default="faq")
    title: Mapped[str] = mapped_column(String(500), nullable=False)  # FAQ question / document name
    content: Mapped[str] = mapped_column(Text, nullable=False)       # FAQ answer / extracted text
    status: Mapped[str] = mapped_column(String(20), default="active")
    created_by: Mapped[int | None] = mapped_column(ForeignKey("agents.id"), nullable=True)
    is_self_learned: Mapped[bool] = mapped_column(Boolean, default=False)
    # Where the text came from: uploaded file name or external URL
    source: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    # Self-learned suggestions remember the chat they were learned from
    origin_chat_id: Mapped[int | None] = mapped_column(Integer, nullable=True)


def kb_type_of(item: KnowledgeItem) -> str:
    if item.is_self_learned and item.item_type == "faq":
        return "self_learned"
    return item.item_type if item.item_type in KB_TYPES else "faq"


def kb_status_of(item: KnowledgeItem) -> str:
    if item.status == "archived":
        return "inactive"
    return item.status if item.status in KB_STATUSES else "active"
