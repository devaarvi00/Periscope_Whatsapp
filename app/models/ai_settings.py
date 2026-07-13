from sqlalchemy import Boolean, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin

PERSONALITIES = ("friendly", "grounded", "spartan", "sales")


class AIAgentSettings(Base, TimestampMixin):
    """Org-wide AI agent configuration (single row, id=1)."""

    __tablename__ = "ai_agent_settings"

    id: Mapped[int] = mapped_column(primary_key=True)

    # Activation
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    auto_activate_new_chats: Mapped[bool] = mapped_column(Boolean, default=False)
    activation_rules: Mapped[str | None] = mapped_column(
        Text, nullable=True,
        doc="Plain-language rules for when the agent should/shouldn't reply",
    )
    hours_start: Mapped[str | None] = mapped_column(String(5), nullable=True)  # "09:00"
    hours_end: Mapped[str | None] = mapped_column(String(5), nullable=True)    # "18:00"

    # Identity & personalization
    agent_name: Mapped[str] = mapped_column(String(100), default="AI Assistant")
    role_description: Mapped[str | None] = mapped_column(Text, nullable=True)
    personality: Mapped[str] = mapped_column(String(20), default="friendly")
    custom_instructions: Mapped[str | None] = mapped_column(Text, nullable=True)
    restrictions: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Response behavior
    response_delay_seconds: Mapped[int] = mapped_column(Integer, default=0)      # 0-6000
    snooze_after_human_seconds: Mapped[int] = mapped_column(Integer, default=900)  # 0-6000

    # Quality & safety
    flag_enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    flag_criteria: Mapped[str | None] = mapped_column(Text, nullable=True)


PERSONALITY_STYLES = {
    "friendly": "Warm and conversational with moderate detail. Use a helpful, positive tone.",
    "grounded": "Strictly factual. Only state what the knowledge base or conversation supports; "
                "say you'll check with the team when unsure.",
    "spartan": "Ultra-brief and authoritative. One or two short sentences maximum.",
    "sales": "Enthusiastic and benefit-oriented. Gently guide toward the product and next steps.",
}


def get_ai_settings(db) -> AIAgentSettings:
    """Fetch (or lazily create) the single settings row."""
    row = db.query(AIAgentSettings).filter(AIAgentSettings.id == 1).first()
    if not row:
        row = AIAgentSettings(id=1)
        db.add(row)
        db.commit()
        db.refresh(row)
    return row


def build_persona_prompt(cfg: AIAgentSettings) -> str:
    """Compose the system-style preamble injected into every Gemini call."""
    parts = [f"You are '{cfg.agent_name}', an assistant replying on WhatsApp on behalf of a business."]
    if cfg.role_description:
        parts.append(f"Role and business context: {cfg.role_description}")
    parts.append(f"Style: {PERSONALITY_STYLES.get(cfg.personality, PERSONALITY_STYLES['friendly'])}")
    if cfg.custom_instructions:
        parts.append(f"Operational instructions: {cfg.custom_instructions}")
    if cfg.restrictions:
        parts.append(
            "Hard restrictions — you must NEVER do the following, even if asked: "
            f"{cfg.restrictions}"
        )
    parts.append("Always reply in the same language the customer wrote in.")
    return "\n".join(parts)
