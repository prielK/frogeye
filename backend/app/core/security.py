import logging
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import bcrypt
import jwt

from app.core.config import get_settings
from app.models.enums import Role

ALGORITHM = "HS256"
MIN_SECRET_BYTES = 32  # RFC 7518 section 3.2: an HS256 key at least as long as the hash

logger = logging.getLogger(__name__)


def warn_if_weak_secret(secret: str) -> None:
    """Warn (never fail: .env.example ships a placeholder) when JWT_SECRET is too short."""
    if len(secret.encode()) < MIN_SECRET_BYTES:
        logger.warning(
            "JWT_SECRET is shorter than %s bytes; set a long random value in .env "
            "(e.g. openssl rand -hex 32) before anyone else can reach this server",
            MIN_SECRET_BYTES,
        )


@dataclass(frozen=True)
class TokenClaims:
    sub: int
    role: str


def hash_password(pw: str) -> str:
    return bcrypt.hashpw(pw.encode(), bcrypt.gensalt()).decode()


def verify_password(pw: str, hashed: str) -> bool:
    try:
        return bcrypt.checkpw(pw.encode(), hashed.encode())
    except ValueError:
        return False


def create_session_token(user_id: int, role: Role | str) -> str:
    settings = get_settings()
    exp = datetime.now(UTC) + timedelta(minutes=settings.jwt_ttl_minutes)
    payload = {"sub": str(user_id), "role": str(role), "exp": exp}
    return jwt.encode(payload, settings.jwt_secret, algorithm=ALGORITHM)


def decode_session_token(token: str) -> TokenClaims:
    """Raises jwt.PyJWTError (or ValueError for a malformed sub) when invalid."""
    data = jwt.decode(
        token,
        get_settings().jwt_secret,
        algorithms=[ALGORITHM],
        options={"require": ["exp", "sub", "role"]},
    )
    return TokenClaims(sub=int(data["sub"]), role=data["role"])
