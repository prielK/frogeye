from fastapi import APIRouter, Depends, HTTPException, Response, status
from sqlalchemy import case, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import COOKIE_NAME, get_current_user
from app.core.config import get_settings
from app.core.db import get_session
from app.core.security import create_session_token, verify_password
from app.models import User
from app.models.enums import Role
from app.schemas.auth import LoginIn, PersonaOut, UserOut

router = APIRouter(prefix="/auth", tags=["auth"])

DEMO_DOMAIN = "@frogeye.local"
_ROLE_ORDER = [Role.analyst, Role.sales, Role.pm, Role.exec, Role.pmm_devrel]
# Valid bcrypt hash compared against when the email is unknown, to equalize timing.
_DUMMY_HASH = "$2b$12$keUfV0h7p8ZrSNTyQanWTuoKZbfTxwVrR7Wap3BMu4Z3I3g3al0d6"


@router.post("/login", response_model=UserOut)
async def login(
    body: LoginIn, response: Response, session: AsyncSession = Depends(get_session)
) -> User:
    user = await session.scalar(select(User).where(User.email == body.email))
    ok = verify_password(body.password, user.password_hash if user else _DUMMY_HASH)
    if user is None or not ok:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid email or password")
    settings = get_settings()
    response.set_cookie(
        COOKIE_NAME,
        create_session_token(user.id, user.role),
        httponly=True,
        samesite="lax",
        secure=settings.cookie_secure,
        max_age=settings.jwt_ttl_minutes * 60,
    )
    return user


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
async def logout() -> Response:
    resp = Response(status_code=status.HTTP_204_NO_CONTENT)
    resp.delete_cookie(COOKIE_NAME, httponly=True, samesite="lax")
    return resp


@router.get("/me", response_model=UserOut)
async def me(user: User = Depends(get_current_user)) -> User:
    return user


@router.get("/personas", response_model=list[PersonaOut])
async def personas(session: AsyncSession = Depends(get_session)) -> list[User]:
    if not get_settings().demo_mode:
        real = await session.scalar(
            select(func.count()).select_from(User).where(~User.email.like(f"%{DEMO_DOMAIN}"))
        )
        if real:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Not found")
    order = case({r.value: i for i, r in enumerate(_ROLE_ORDER)}, value=User.role)
    return list(await session.scalars(select(User).order_by(order, User.id)))
