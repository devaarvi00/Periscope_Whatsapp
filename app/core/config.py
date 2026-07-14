from functools import lru_cache

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    app_name: str = "Hyperscope WhatsApp CRM"
    app_version: str = "1.0.0"
    environment: str = "development"
    api_prefix: str = "/api/v1"
    debug: bool = False
    secret_key: str = Field("change-me-in-production-32chars!!", min_length=16)
    access_token_expire_minutes: int = 1440  # 24 hours

    database_url: str = Field(
        "mysql+pymysql://root:password@localhost:3306/whatsapp_periscope",
    )

    # WAHA WhatsApp provider
    waha_base_url: str = "http://localhost:3000"
    waha_api_key: str = ""
    waha_session_name: str = "default"
    waha_session_prefix: str = "hyperscope"  # auto-generated sessions: hyperscope_1, hyperscope_2 …
    waha_webhook_secret: str = Field("replace-webhook-secret", min_length=8)
    waha_human_simulation_enabled: bool = True
    waha_typing_min_seconds: float = 1.0
    waha_typing_max_seconds: float = 4.0
    waha_typing_chars_per_second: float = 14.0
    public_webhook_base_url: str | None = None

    # Gemini AI
    gemini_api_key: str = Field("replace-gemini-api-key", min_length=8)
    gemini_model: str = "gemini-3.1-flash-lite"

    # Platform limits
    request_timeout_seconds: int = 30
    bulk_message_credits_per_month: int = 3000
    max_automation_rules_per_license: int = 2

    # Tickets: reacting to a message with one of these emojis creates a ticket
    ticket_emoji_reactions: list[str] = ["🎫", "📌", "🚩", "⚠️"]
    # AI auto-flag: flag important inbound messages matching this custom prompt
    ai_auto_flag_enabled: bool = False
    ai_auto_flag_criteria: str = (
        "urgent requests, complaints, refund or cancellation requests, "
        "angry or frustrated customers, payment issues"
    )

    log_level: str = "INFO"
    allowed_origins: list[str] = ["*"]

    @property
    def waha_webhook_url(self) -> str:
        base = (self.public_webhook_base_url or "http://localhost:8000").rstrip("/")
        return f"{base}{self.api_prefix}/webhooks/waha"

    @field_validator("public_webhook_base_url", mode="before")
    @classmethod
    def _empty_to_none(cls, v: object) -> object:
        if isinstance(v, str) and not v.strip():
            return None
        return v

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
