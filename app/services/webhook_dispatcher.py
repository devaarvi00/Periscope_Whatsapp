import hashlib
import hmac
import json
import logging
from datetime import datetime
from typing import Any

logger = logging.getLogger(__name__)

# Events a webhook endpoint can subscribe to
WEBHOOK_EVENTS = [
    "message.received",
    "message.sent",
    "chat.created",
    "ticket.created",
    "ticket.updated",
]


async def dispatch_event(event: str, data: dict[str, Any]) -> None:
    """POST an event to every active outbound webhook subscribed to it.

    Failures are counted but never raised — outbound webhooks must not
    break inbound processing.
    """
    from app.core.http_client import get_http_client
    from app.db.session import SessionLocal
    from app.models.api_key import WebhookEndpoint

    db = SessionLocal()
    try:
        endpoints = (
            db.query(WebhookEndpoint)
            .filter(WebhookEndpoint.is_active == True)
            .all()
        )
        targets = [
            e for e in endpoints
            if not e.events or event in e.events
        ]
        if not targets:
            return

        payload = {
            "event": event,
            "timestamp": datetime.utcnow().isoformat() + "Z",
            "data": data,
        }
        body = json.dumps(payload)

        for endpoint in targets:
            headers = {"Content-Type": "application/json", "X-Event": event}
            if endpoint.secret:
                signature = hmac.new(
                    endpoint.secret.encode(), body.encode(), hashlib.sha256
                ).hexdigest()
                headers["X-Signature"] = f"sha256={signature}"
            try:
                resp = await get_http_client().post(
                    endpoint.url, content=body, headers=headers
                )
                if not resp.is_success:
                    endpoint.failure_count = (endpoint.failure_count or 0) + 1
                    logger.warning(
                        "Outbound webhook %s returned %s for %s",
                        endpoint.url, resp.status_code, event,
                    )
            except Exception as exc:
                endpoint.failure_count = (endpoint.failure_count or 0) + 1
                logger.warning("Outbound webhook %s failed: %s", endpoint.url, exc)
        db.commit()
    except Exception as exc:
        logger.exception("Webhook dispatch error for %s: %s", event, exc)
    finally:
        db.close()
