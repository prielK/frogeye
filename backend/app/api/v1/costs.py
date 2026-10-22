"""LLM spend from the `llm_calls` ledger against the budgets. Days are calendar days in
`settings.timezone` (Asia/Jerusalem), the same days the AccountedLLM daily budget uses."""

from datetime import UTC, datetime, timedelta
from datetime import time as dtime
from typing import Annotated
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user
from app.core.config import get_settings
from app.core.db import get_session
from app.models import LlmCall, User
from app.schemas.costs import CostsOut, DayCost, ModelCost, TaskCost

router = APIRouter(prefix="/costs", tags=["costs"])

DAYS = 7


def utcnow() -> datetime:
    return datetime.now(UTC)


@router.get("", response_model=CostsOut)
async def get_costs(
    _: Annotated[User, Depends(get_current_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> CostsOut:
    settings = get_settings()
    tz = ZoneInfo(settings.timezone)
    today = utcnow().astimezone(tz).date()
    first = today - timedelta(days=DAYS - 1)
    cost = func.sum(LlmCall.cost_usd)

    # Group over a subquery: the bound time zone would otherwise be rendered twice (SELECT
    # and GROUP BY as different parameters), which Postgres rejects.
    recent = (
        select(
            func.date(func.timezone(settings.timezone, LlmCall.created_at)).label("day"),
            LlmCall.cost_usd,
        )
        .where(LlmCall.created_at >= datetime.combine(first, dtime.min, tzinfo=tz))
        .subquery()
    )
    per_day = dict(
        (
            await session.execute(
                select(recent.c.day, func.sum(recent.c.cost_usd)).group_by(recent.c.day)
            )
        ).all()
    )
    total = await session.scalar(select(func.coalesce(cost, 0)))
    by_task = (
        await session.execute(
            select(LlmCall.task, func.count(), cost)
            .group_by(LlmCall.task)
            .order_by(cost.desc(), LlmCall.task)
        )
    ).all()
    by_model = (
        await session.execute(
            select(
                LlmCall.model,
                func.count(),
                cost,
                func.count().filter(LlmCall.status == "cached"),
            )
            .group_by(LlmCall.model)
            .order_by(cost.desc(), LlmCall.model)
        )
    ).all()

    days = [first + timedelta(days=i) for i in range(DAYS)]
    return CostsOut(
        today_usd=float(per_day.get(today, 0)),
        total_usd=float(total or 0),
        daily_budget_usd=settings.llm_daily_budget_usd,
        total_budget_usd=settings.llm_total_budget_usd,
        by_task=[TaskCost(task=t, calls=n, cost_usd=float(c)) for t, n, c in by_task],
        by_model=[
            ModelCost(model=m, calls=n, cost_usd=float(c), cached_calls=k)
            for m, n, c, k in by_model
        ],
        last_7_days=[DayCost(date=d, cost_usd=float(per_day.get(d, 0))) for d in days],
    )
