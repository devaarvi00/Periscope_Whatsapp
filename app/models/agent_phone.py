from sqlalchemy import ForeignKey
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class AgentPhone(Base):
    """Number-level permission: which WhatsApp numbers an agent may access.

    An agent with no rows has access to all numbers (admins always do).
    """

    __tablename__ = "agent_phones"

    agent_id: Mapped[int] = mapped_column(ForeignKey("agents.id"), primary_key=True)
    phone_id: Mapped[int] = mapped_column(ForeignKey("phones.id"), primary_key=True)
