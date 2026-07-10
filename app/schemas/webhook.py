from typing import Any

from pydantic import BaseModel


class WahaWebhookPayload(BaseModel):
    event: str
    session: str
    payload: dict[str, Any] | None = None
    metadata: dict[str, Any] | None = None

    model_config = {"extra": "allow"}
