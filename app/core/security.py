from datetime import datetime, timedelta, timezone
from typing import Any

import bcrypt
import jwt

from app.core.config import settings

# Pinned: never accept the algorithm from the token header.
ALGORITHM = "HS256"
JWT_ISSUER = "hyperscope-crm"
JWT_AUDIENCE = "hyperscope-crm-api"

# bcrypt only uses the first 72 bytes and bcrypt>=5 raises on longer input.
BCRYPT_MAX_BYTES = 72


def hash_password(password: str) -> str:
    """Hash a password with bcrypt.

    Raises ValueError when the password exceeds bcrypt's 72-byte limit;
    callers should surface that as a 400.
    """
    encoded = password.encode()
    if len(encoded) > BCRYPT_MAX_BYTES:
        raise ValueError(f"Password must be at most {BCRYPT_MAX_BYTES} bytes")
    return bcrypt.hashpw(encoded, bcrypt.gensalt()).decode()


def verify_password(plain: str, hashed: str) -> bool:
    try:
        return bcrypt.checkpw(plain.encode(), hashed.encode())
    except Exception:
        return False


def create_access_token(data: dict[str, Any], expires_delta: timedelta | None = None) -> str:
    to_encode = data.copy()
    now = datetime.now(timezone.utc)
    expire = now + (expires_delta or timedelta(minutes=settings.access_token_expire_minutes))
    to_encode.update({"exp": expire, "iat": now, "iss": JWT_ISSUER, "aud": JWT_AUDIENCE})
    return jwt.encode(to_encode, settings.secret_key, algorithm=ALGORITHM)


def decode_access_token(token: str) -> dict[str, Any] | None:
    """Return the verified claims, or None for any invalid/expired token.

    Tokens issued before iss/aud were added fail verification here, so those
    users simply have to log in again.
    """
    try:
        return jwt.decode(
            token,
            settings.secret_key,
            algorithms=[ALGORITHM],
            issuer=JWT_ISSUER,
            audience=JWT_AUDIENCE,
            options={"require": ["exp", "iss", "aud", "sub"]},
        )
    except jwt.PyJWTError:
        return None
