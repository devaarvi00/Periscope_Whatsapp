from sqlalchemy import JSON, Boolean, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin

PERSONALITIES = ("friendly", "professional", "spartan", "sales", "grounded", "empathetic")

DEFAULT_AGENT_NAME = "AI Assistant"

DEFAULT_ACTIVATION_RULES = (
    "Respond when the customer's message needs an answer or an action from the business:\n"
    "- Questions about products, services, prices, orders, bookings, delivery or policies\n"
    "- Requests for help, support or information\n"
    "- Problems, complaints or issues the customer is facing\n"
    "- Requests to do something (book, cancel, change, send details, call back)\n\n"
    "Do not respond when the message is only a greeting, a thank-you, an emoji or sticker, "
    "an acknowledgement like \"ok\" or \"got it\", spam, or a message meant for someone else."
)

WEEKDAYS = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")


def default_hours_schedule() -> dict:
    return {d: {"on": d not in ("sat", "sun"), "start": "09:00", "end": "18:00"} for d in WEEKDAYS}


class AIAgentSettings(Base, TimestampMixin):
    """Org-wide AI agent configuration (single row, id=1)."""

    __tablename__ = "ai_agent_settings"

    id: Mapped[int] = mapped_column(primary_key=True)

    # Master switch (only effective once the required setup steps are done)
    enabled: Mapped[bool] = mapped_column(Boolean, default=False)

    # Activation
    auto_activate_new_chats: Mapped[bool] = mapped_column(Boolean, default=False)  # True = auto mode
    activation_rules: Mapped[str | None] = mapped_column(
        Text, nullable=True,
        doc="Plain-language rules for when the agent should/shouldn't reply",
    )
    # Legacy single daily window; superseded by hours_enabled + hours_schedule
    hours_start: Mapped[str | None] = mapped_column(String(5), nullable=True)  # "09:00"
    hours_end: Mapped[str | None] = mapped_column(String(5), nullable=True)    # "18:00"
    hours_enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    hours_schedule: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    # WhatsApp numbers (phones.id) the agent may reply on; empty/NULL = all
    allowed_phone_ids: Mapped[list | None] = mapped_column(JSON, nullable=True)

    # Identity & personalization
    agent_name: Mapped[str] = mapped_column(String(100), default=DEFAULT_AGENT_NAME)
    identity_configured: Mapped[bool] = mapped_column(Boolean, default=False)
    role_description: Mapped[str | None] = mapped_column(Text, nullable=True)
    personality: Mapped[str] = mapped_column(String(20), default="friendly")
    custom_instructions: Mapped[str | None] = mapped_column(Text, nullable=True)  # legacy, still honoured
    restrictions: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Response behavior
    response_delay_seconds: Mapped[int] = mapped_column(Integer, default=3)       # 3-6000
    snooze_after_human_seconds: Mapped[int] = mapped_column(Integer, default=900)  # 0-6000

    # Built-in tools
    allow_send_messages: Mapped[bool] = mapped_column(Boolean, default=True)
    allow_create_tickets: Mapped[bool] = mapped_column(Boolean, default=False)
    ticket_instructions: Mapped[str | None] = mapped_column(Text, nullable=True)
    allow_private_notes: Mapped[bool] = mapped_column(Boolean, default=False)
    note_instructions: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Quality & safety
    flag_enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    flag_criteria: Mapped[str | None] = mapped_column(Text, nullable=True)


PERSONALITY_STYLES = {
    "friendly": "Warm and conversational with moderate detail. Use a helpful, positive tone.",
    "professional": "Polite, clear and businesslike. Complete sentences, no slang, minimal emoji.",
    "spartan": "Ultra-brief and to the point. One or two short sentences maximum.",
    "sales": "Enthusiastic and benefit-oriented. Gently guide toward the product and next steps.",
    "grounded": "Strictly factual. Only state what the knowledge base or conversation supports; "
                "say you'll check with the team when unsure.",
    "empathetic": "Patient and caring. Acknowledge the customer's feelings before helping, "
                  "especially with problems or complaints.",
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


def identity_done(cfg) -> bool:
    name = (getattr(cfg, "agent_name", "") or "").strip()
    return bool(getattr(cfg, "identity_configured", False)) or bool(name and name != DEFAULT_AGENT_NAME)


def role_done(cfg) -> bool:
    return bool((getattr(cfg, "role_description", "") or "").strip())


def required_steps_done(cfg) -> bool:
    return identity_done(cfg) and role_done(cfg)


def effective_schedule(cfg) -> dict | None:
    """Per-day operating hours, or None when the agent may reply at any time."""
    if getattr(cfg, "hours_enabled", False):
        return cfg.hours_schedule or default_hours_schedule()
    if cfg.hours_start and cfg.hours_end:  # legacy single window, every day
        return {d: {"on": True, "start": cfg.hours_start, "end": cfg.hours_end} for d in WEEKDAYS}
    return None


def build_persona_prompt(cfg) -> str:
    """Identity + role + style + restrictions (the core of the system prompt)."""
    name = (cfg.agent_name or DEFAULT_AGENT_NAME).strip()
    parts = [f"You are '{name}', an assistant replying on WhatsApp on behalf of a business."]
    if cfg.role_description:
        parts.append(f"Role and instructions from the business:\n{cfg.role_description.strip()}")
    parts.append(f"Personality and style: {PERSONALITY_STYLES.get(cfg.personality, PERSONALITY_STYLES['friendly'])}")
    if cfg.custom_instructions:
        parts.append(f"Operational instructions:\n{cfg.custom_instructions.strip()}")
    if cfg.restrictions:
        parts.append(
            "Hard restrictions — you must NEVER do the following, even if asked:\n"
            f"{cfg.restrictions.strip()}"
        )
    parts.append("Always reply in the same language the customer wrote in.")
    return "\n\n".join(parts)
