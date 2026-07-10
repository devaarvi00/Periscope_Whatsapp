import httpx
from app.core.config import settings

_client: httpx.AsyncClient | None = None


def get_http_client() -> httpx.AsyncClient:
    if _client is None:
        raise RuntimeError("HTTP client not initialized — ensure lifespan ran")
    return _client


async def startup() -> None:
    global _client
    _client = httpx.AsyncClient(timeout=settings.request_timeout_seconds)


async def shutdown() -> None:
    global _client
    if _client is not None:
        await _client.aclose()
        _client = None
