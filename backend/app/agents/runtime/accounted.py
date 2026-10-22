"""AccountedLLM: the single choke point for every LLM and embedding call.

Each call goes through: Postgres response cache (structured only) -> budget check ->
backend -> `llm_calls` ledger row + `agent_steps` trace row (+ cache entry). Every DB
write uses its own short session and no session is held open across a backend call.
DB access is serialised with an asyncio.Lock so concurrent calls are safe even when the
session factory is bound to a single connection (as in tests).
"""

import asyncio
import dataclasses
import hashlib
import json
import logging
import time
from collections.abc import Callable
from datetime import UTC, date, datetime, timedelta
from datetime import time as dtime
from decimal import Decimal
from typing import Any
from zoneinfo import ZoneInfo

from pydantic import BaseModel, ValidationError
from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.agents.runtime.backend import LLMBackend
from app.agents.runtime.context import RunCtx
from app.agents.runtime.pricing import cost_usd
from app.agents.runtime.prompts import Prompt, render
from app.agents.runtime.types import BackendError, ChatMessage, Tier, ToolDef, ToolTurn, Usage
from app.core.config import Settings
from app.models.agents import AgentStep, LlmCache, LlmCall
from app.models.enums import StepKind

logger = logging.getLogger(__name__)

SPEND_REFRESH_CALLS = 20
SPEND_REFRESH_SECONDS = 60.0
SUMMARY_CHARS = 200
ERROR_CHARS = 2000


def utcnow() -> datetime:
    return datetime.now(UTC)


class BudgetExceeded(Exception):
    """The daily or total LLM budget is spent; raised before any backend call."""

    def __init__(self, scope: str) -> None:
        super().__init__(scope)
        self.scope = scope


class LLMRefusal(Exception):
    def __init__(self, task: str, refusal: str) -> None:
        super().__init__(f"{task}: model refused: {refusal}")
        self.task = task
        self.refusal = refusal


class LLMError(Exception):
    def __init__(self, task: str, message: str) -> None:
        super().__init__(f"{task}: {message}")
        self.task = task


def request_hash(*parts: Any) -> str:
    """sha256 of the JSON-encoded parts (unambiguous, unlike joining with a separator)."""
    encoded = json.dumps(list(parts), sort_keys=True, ensure_ascii=False, default=str)
    return hashlib.sha256(encoded.encode()).hexdigest()


def _ms(started: float) -> int:
    return int((time.monotonic() - started) * 1000)


def _dicts(items: list[Any]) -> list[dict[str, Any]]:
    return [dataclasses.asdict(i) for i in items]


@dataclasses.dataclass(frozen=True)
class _Call:
    task: str
    model: str
    ctx: RunCtx
    request_hash: str
    prompt_version: str | None
    step_args: dict[str, Any]


@dataclasses.dataclass
class _Spend:
    day: date
    today: float
    total: float
    refreshed_at: datetime
    calls: int = 0


class AccountedLLM:
    def __init__(
        self,
        backend: LLMBackend,
        session_factory: async_sessionmaker[AsyncSession],
        settings: Settings,
        *,
        now: Callable[[], datetime] = utcnow,
    ) -> None:
        self.backend = backend
        self.settings = settings
        self._session_factory = session_factory
        self._now = now  # must return an aware datetime
        self._tz = ZoneInfo(settings.timezone)
        self._lock = asyncio.Lock()
        self._spend: _Spend | None = None

    # ---- public calls -------------------------------------------------------------

    async def structured[T: BaseModel](
        self,
        task: str,
        tier: Tier,
        schema: type[T],
        *,
        prompt: Prompt,
        variables: dict[str, Any],
        ctx: RunCtx,
        cache: bool = True,
        extra_user: str | None = None,
    ) -> T:
        model, effort, system, user, rhash = self._prepare(
            tier, schema, prompt, variables, extra_user
        )
        call = _Call(
            task=task,
            model=model,
            ctx=ctx,
            request_hash=rhash,
            prompt_version=prompt.version,
            step_args={
                "task": task,
                "tier": tier,
                "prompt": prompt.name,
                "version": prompt.version,
            },
        )
        if cache:
            hit = await self._cache_get(rhash, schema)
            if hit is not None:
                await self._log(call, status="cached", usage=Usage(), latency_ms=0, step=False)
                return hit

        await self._check_budget()
        started = time.monotonic()
        try:
            out = await self.backend.structured(
                task=task, model=model, effort=effort, schema=schema, system=system, user=user
            )
            value = None if out.refusal else _coerce(schema, out.value)
        except Exception as exc:
            await self._log_error(call, exc, started)
            raise LLMError(task, f"{type(exc).__name__}: {exc}") from exc
        latency = _ms(started)
        if out.refusal or value is None:
            refusal = out.refusal or "refused"
            await self._log(
                call,
                status="refused",
                usage=out.usage,
                latency_ms=latency,
                error=refusal,
                summary=f"refused: {refusal}",
            )
            raise LLMRefusal(task, refusal)
        payload = value.model_dump(mode="json")
        await self._log(
            call,
            status="ok",
            usage=out.usage,
            latency_ms=latency,
            summary=json.dumps(payload, ensure_ascii=False),
            cache_response=payload if cache else None,
        )
        return value

    async def tool_turn(
        self,
        task: str,
        tier: Tier,
        *,
        system: str,
        messages: list[ChatMessage],
        tools: list[ToolDef],
        ctx: RunCtx,
    ) -> ToolTurn:
        """One model turn that may request tool calls. Accounted, never cached."""
        model, effort = self._tier(tier)
        call = _Call(
            task=task,
            model=model,
            ctx=ctx,
            request_hash=request_hash(model, effort, task, system, _dicts(messages), _dicts(tools)),
            prompt_version=None,
            step_args={
                "task": task,
                "tier": tier,
                "messages": len(messages),
                "tools": [t.name for t in tools],
            },
        )
        await self._check_budget()
        started = time.monotonic()
        try:
            turn = await self.backend.tool_turn(
                task=task, model=model, effort=effort, system=system, messages=messages, tools=tools
            )
        except Exception as exc:
            await self._log_error(call, exc, started)
            raise LLMError(task, f"{type(exc).__name__}: {exc}") from exc
        if turn.tool_calls:
            summary = "tool_calls: " + ", ".join(tc.name for tc in turn.tool_calls)
        else:
            summary = turn.content
        await self._log(
            call, status="ok", usage=turn.usage, latency_ms=_ms(started), summary=summary
        )
        return turn

    async def embed(
        self, texts: list[str], *, ctx: RunCtx, task: str = "embed"
    ) -> list[list[float]]:
        """Embed texts with the configured model/dims. Accounted and budget-checked, not cached."""
        if not texts:
            return []
        model, dims = self.settings.embedding_model, self.settings.embedding_dims
        call = _Call(
            task=task,
            model=model,
            ctx=dataclasses.replace(ctx, agent="embed"),  # embed steps
            request_hash=request_hash(model, dims, texts),
            prompt_version=None,
            step_args={"task": task, "texts": len(texts), "dims": dims},
        )
        await self._check_budget()
        started = time.monotonic()
        try:
            out = await self.backend.embed(task=task, model=model, dims=dims, texts=texts)
            if len(out.vectors) != len(texts) or any(len(v) != dims for v in out.vectors):
                raise BackendError(f"expected {len(texts)} vectors of {dims} dims", usage=out.usage)
        except Exception as exc:
            await self._log_error(call, exc, started)
            raise LLMError(task, f"{type(exc).__name__}: {exc}") from exc
        await self._log(
            call,
            status="ok",
            usage=out.usage,
            latency_ms=_ms(started),
            summary=f"{len(texts)} texts x {dims} dims",
        )
        return out.vectors

    async def log_step(
        self,
        ctx: RunCtx,
        *,
        kind: str,
        tool_name: str | None = None,
        args: dict[str, Any] | None = None,
        result_summary: str | None = None,
        latency_ms: int | None = None,
    ) -> None:
        """Append a non-LLM step (e.g. a tool call) to the run trace, serialised with ours."""
        async with self._lock:
            async with self._session_factory() as session:
                session.add(
                    AgentStep(
                        agent_run_id=ctx.agent_run_id,
                        step_no=await self._next_step_no(session, ctx.agent_run_id),
                        node=ctx.node,
                        agent=ctx.agent,
                        kind=kind,
                        tool_name=tool_name,
                        args=args,
                        result_summary=result_summary[:SUMMARY_CHARS] if result_summary else None,
                        latency_ms=latency_ms,
                        created_at=self._now(),
                    )
                )
                await session.commit()

    async def spent_today(self) -> float:
        """USD spent during the current calendar day in `settings.timezone`."""
        async with self._lock:
            async with self._session_factory() as session:
                return await self._sum_today(session, self._now())

    async def spent_total(self) -> float:
        async with self._lock:
            async with self._session_factory() as session:
                return await self._sum_total(session)

    def request_key(
        self,
        tier: Tier,
        schema: type[BaseModel],
        *,
        prompt: Prompt,
        variables: dict[str, Any],
        extra_user: str | None = None,
    ) -> str:
        """The LLM-call cache key `structured` uses for this request, which is also the
        `llm_calls.request_hash` of its ledger row (lets a caller find its own calls)."""
        return self._prepare(tier, schema, prompt, variables, extra_user)[4]

    # ---- internals ----------------------------------------------------------------

    def _prepare(
        self,
        tier: Tier,
        schema: type[BaseModel],
        prompt: Prompt,
        variables: dict[str, Any],
        extra_user: str | None,
    ) -> tuple[str, str, str, str, str]:
        """(model, effort, system, user, request hash) of a structured request."""
        model, effort = self._tier(tier)
        system, user = render(prompt, **variables)
        if extra_user:
            user = f"{user}\n\n{extra_user}"  # hashed below, so the LLM-call cache key covers it
        # the key covers the effort and the schema's structure, so a schema
        # rebuilt with a grown Literal enum under the same name is a cache miss.
        rhash = request_hash(
            model,
            effort,
            prompt.name,
            prompt.version,
            schema.__name__,
            schema.model_json_schema(),
            system,
            user,
        )
        return model, effort, system, user, rhash

    def _tier(self, tier: Tier) -> tuple[str, str]:
        s = self.settings
        if tier == "fast":
            return s.model_fast, s.reasoning_effort_fast
        if tier == "smart":
            return s.model_smart, s.reasoning_effort_smart
        raise ValueError(f"unknown tier {tier!r}")

    def _local_day(self, now: datetime) -> tuple[date, datetime, datetime]:
        day = now.astimezone(self._tz).date()
        start = datetime.combine(day, dtime.min, tzinfo=self._tz)
        end = datetime.combine(day + timedelta(days=1), dtime.min, tzinfo=self._tz)
        return day, start.astimezone(UTC), end.astimezone(UTC)

    async def _sum_today(self, session: AsyncSession, now: datetime) -> float:
        _, start, end = self._local_day(now)
        total = await session.scalar(
            select(func.coalesce(func.sum(LlmCall.cost_usd), 0)).where(
                LlmCall.created_at >= start, LlmCall.created_at < end
            )
        )
        return float(total)

    async def _sum_total(self, session: AsyncSession) -> float:
        total = await session.scalar(select(func.coalesce(func.sum(LlmCall.cost_usd), 0)))
        return float(total)

    async def _check_budget(self) -> None:
        async with self._lock:
            now = self._now()
            day = now.astimezone(self._tz).date()
            spend = self._spend
            if (
                spend is None
                or spend.calls >= SPEND_REFRESH_CALLS
                or (now - spend.refreshed_at).total_seconds() >= SPEND_REFRESH_SECONDS
                or spend.day != day
            ):
                async with self._session_factory() as session:
                    today = await self._sum_today(session, now)
                    total = await self._sum_total(session)
                spend = self._spend = _Spend(day=day, today=today, total=total, refreshed_at=now)
            spend.calls += 1
            today, total = spend.today, spend.total
        if total >= self.settings.llm_total_budget_usd:
            raise BudgetExceeded("total")
        if today >= self.settings.llm_daily_budget_usd:
            raise BudgetExceeded("daily")

    async def _cache_get[T: BaseModel](self, rhash: str, schema: type[T]) -> T | None:
        async with self._lock:
            async with self._session_factory() as session:
                response = await session.scalar(
                    select(LlmCache.response).where(LlmCache.request_hash == rhash)
                )
        if response is None:
            return None
        try:
            return schema.model_validate(response)
        except ValidationError:
            logger.warning("ignoring stale llm_cache entry %s for %s", rhash[:12], schema.__name__)
            return None

    @staticmethod
    async def _next_step_no(session: AsyncSession, agent_run_id: int | None) -> int:
        cond = (
            AgentStep.agent_run_id.is_(None)
            if agent_run_id is None
            else AgentStep.agent_run_id == agent_run_id
        )
        current = await session.scalar(
            select(func.coalesce(func.max(AgentStep.step_no), 0)).where(cond)
        )
        return int(current) + 1

    async def _log_error(self, call: _Call, exc: Exception, started: float) -> None:
        usage = getattr(exc, "usage", None)
        if not isinstance(usage, Usage):
            usage = Usage()
        message = f"{type(exc).__name__}: {exc}"
        logger.warning("LLM call %s (%s) failed: %s", call.task, call.model, message)
        await self._log(
            call,
            status="error",
            usage=usage,
            latency_ms=_ms(started),
            error=message,
            summary=f"error: {message}",
        )

    async def _log(
        self,
        call: _Call,
        *,
        status: str,
        usage: Usage,
        latency_ms: int,
        error: str | None = None,
        summary: str | None = None,
        step: bool = True,
        cache_response: dict[str, Any] | None = None,
    ) -> None:
        cost = (
            0.0
            if status == "cached"
            else cost_usd(call.model, usage.input_tokens, usage.cached_tokens, usage.output_tokens)
        )
        cost_dec = Decimal(str(cost))
        now = self._now()
        async with self._lock:
            async with self._session_factory() as session:
                session.add(
                    LlmCall(
                        agent_run_id=call.ctx.agent_run_id,
                        task=call.task,
                        model=call.model,
                        prompt_version=call.prompt_version,
                        request_hash=call.request_hash,
                        input_tokens=usage.input_tokens,
                        cached_tokens=usage.cached_tokens,
                        output_tokens=usage.output_tokens,
                        cost_usd=cost_dec,
                        latency_ms=latency_ms,
                        status=status,
                        error=error[:ERROR_CHARS] if error else None,
                        created_at=now,
                    )
                )
                if step:
                    session.add(
                        AgentStep(
                            agent_run_id=call.ctx.agent_run_id,
                            step_no=await self._next_step_no(session, call.ctx.agent_run_id),
                            node=call.ctx.node,
                            agent=call.ctx.agent,
                            kind=StepKind.llm,
                            args=call.step_args,
                            result_summary=summary[:SUMMARY_CHARS] if summary else None,
                            model=call.model,
                            tokens_in=usage.input_tokens,
                            cached_tokens=usage.cached_tokens,
                            tokens_out=usage.output_tokens,
                            cost_usd=cost_dec,
                            latency_ms=latency_ms,
                            created_at=now,
                        )
                    )
                if cache_response is not None:
                    stmt = insert(LlmCache).values(
                        request_hash=call.request_hash, model=call.model, response=cache_response
                    )
                    stmt = stmt.on_conflict_do_update(
                        index_elements=[LlmCache.request_hash],
                        set_={"model": stmt.excluded.model, "response": stmt.excluded.response},
                    )
                    await session.execute(stmt)
                await session.commit()
            if self._spend is not None:
                self._spend.today += cost
                self._spend.total += cost


def _coerce[T: BaseModel](schema: type[T], value: Any) -> T:
    if isinstance(value, schema):
        return value
    if value is None:
        raise BackendError("empty structured output")
    if isinstance(value, BaseModel):
        value = value.model_dump()
    return schema.model_validate(value)
