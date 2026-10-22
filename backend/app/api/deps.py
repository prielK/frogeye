from collections.abc import Callable

import jwt
from fastapi import Depends, HTTPException, Request, status
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.agents.runtime.accounted import AccountedLLM
from app.core.config import get_settings
from app.core.db import get_session
from app.core.security import decode_session_token
from app.models import User
from app.models.enums import Role

COOKIE_NAME = "frogeye_session"


async def get_current_user(request: Request, session: AsyncSession = Depends(get_session)) -> User:
    unauthorized = HTTPException(status.HTTP_401_UNAUTHORIZED, "Not authenticated")
    token = request.cookies.get(COOKIE_NAME)
    if not token:
        raise unauthorized
    try:
        claims = decode_session_token(token)
    except (jwt.PyJWTError, ValueError):
        raise unauthorized from None
    user = await session.get(User, claims.sub)
    if user is None:
        raise unauthorized
    return user


def require_roles(*roles: Role | str) -> Callable[..., User]:
    allowed = {str(r) for r in roles}

    async def _guard(user: User = Depends(get_current_user)) -> User:
        if str(user.role) not in allowed:
            raise HTTPException(status.HTTP_403_FORBIDDEN, "Insufficient role")
        return user

    return _guard


def get_session_factory() -> async_sessionmaker[AsyncSession]:
    """Overridable in tests; functions that await LLM calls take a factory."""
    from app.core.db import SessionLocal

    return SessionLocal


_llm_cache: tuple[str, AccountedLLM] | None = None


def get_llm() -> AccountedLLM:
    """The process-wide AccountedLLM over OpenAI; 503 when no API key is configured."""
    global _llm_cache
    from app.agents.runtime.openai_backend import OpenAIBackend
    from app.core.db import SessionLocal

    settings = get_settings()
    key = settings.openai_api_key
    if not key:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "AI search needs OPENAI_API_KEY")
    if _llm_cache is None or _llm_cache[0] != key:
        _llm_cache = (key, AccountedLLM(OpenAIBackend(settings), SessionLocal, settings))
    return _llm_cache[1]


def get_optional_llm() -> AccountedLLM | None:
    """Like `get_llm`, but None instead of a 503 when no API key is configured (callers that
    can degrade, e.g. keyword-only signal search)."""
    if not get_settings().openai_api_key:
        return None
    return get_llm()
