from sqlalchemy import Boolean, ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin


class KnowledgeItem(Base, TimestampMixin):
    __tablename__ = "knowledge_items"

    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    item_type: Mapped[str] = mapped_column(String(20), default="faq")  # faq | document
    title: Mapped[str] = mapped_column(String(500), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(String(20), default="active")  # active | review | archived
    created_by: Mapped[int | None] = mapped_column(ForeignKey("agents.id"), nullable=True)
    is_self_learned: Mapped[bool] = mapped_column(Boolean, default=False)
