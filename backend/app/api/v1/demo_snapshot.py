"""Whether this database started from the bundled demo snapshot (`app.snapshot`), and when
that data was captured: the UI says so, since nothing newer arrives without a daily run."""

from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user
from app.core.db import get_session
from app.models import DemoSnapshot, User
from app.schemas.demo_snapshot import DemoSnapshotOut

router = APIRouter(prefix="/demo-snapshot", tags=["demo-snapshot"])


@router.get("", response_model=DemoSnapshotOut)
async def get_demo_snapshot(
    _: Annotated[User, Depends(get_current_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> DemoSnapshotOut:
    captured_at = await session.scalar(select(DemoSnapshot.captured_at).limit(1))
    return DemoSnapshotOut(captured_at=captured_at)
