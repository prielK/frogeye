"""The eval suites. Each module defines `SUITE`: its name, title, what it measures, its
dataset and an async `run(env, rows) -> EvalSuite` that calls the real app functions."""

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel
from sqlalchemy import select

from app.agents.nodes.triage import apply_triage
from app.agents.runtime.accounted import BudgetExceeded, LLMError, LLMRefusal
from app.models import Competitor, Document, DocumentCompetitor
from app.schemas.evals import EvalFailure, EvalGroup, EvalMetric, EvalSuite
from evals.harness import Env
from evals.recorder import case_scope, suite_scope

CONCURRENCY = 4
Row = dict[str, Any]


@dataclass(frozen=True)
class Suite:
    name: str
    title: str
    description: str
    dataset: str  # file under evals/datasets
    label_source: str
    run: Callable[[Env, list[Row]], Awaitable[EvalSuite]]


@dataclass
class Outcome:
    row: Row
    value: Any = None  # None also when the app raised an LLM error it does not catch
    stale: str | None = None  # why the case could not be scored
    error: str | None = None


async def run_cases(
    env: Env, rows: list[Row], fn: Callable[[Row], Awaitable[Any]], *, suite: str
) -> list[Outcome]:
    """`fn(row)` for every row, a few at a time, each in its own case scope: a case that
    asked for anything the recordings lack (or hit the budget) comes back stale, never
    scored on a degraded answer. A genuine LLM error the app does not catch itself comes
    back as `value=None` with the error, scored as the app's failure."""
    gate = asyncio.Semaphore(CONCURRENCY)

    async def one(row: Row) -> Outcome:
        async with gate:
            with suite_scope(suite), case_scope() as log:
                error = None
                try:
                    value = await fn(row)
                except BudgetExceeded as exc:
                    return Outcome(row, stale=f"budget stop ({exc.scope})")
                except (LLMError, LLMRefusal) as exc:
                    value, error = None, str(exc)
            if log.misses:
                return Outcome(row, stale="not recorded: " + "; ".join(dict.fromkeys(log.misses)))
            return Outcome(row, value, error=error)

    return list(await asyncio.gather(*(one(r) for r in rows)))


def build(
    suite: Suite,
    outcomes: list[Outcome],
    *,
    metrics: list[EvalMetric],
    groups: list[EvalGroup] | None = None,
    failures: list[EvalFailure] | None = None,
    cases: int | None = None,
) -> EvalSuite:
    stale = [o.row["id"] for o in outcomes if o.stale]
    total = len(outcomes) if cases is None else cases
    return EvalSuite(
        name=suite.name,
        title=suite.title,
        description=suite.description,
        label_source=suite.label_source,
        cases=total,
        scored=total - len(stale),
        stale=len(stale),
        metrics=metrics,
        groups=groups or [],
        failures=failures or [],
        stale_cases=stale,
    )


def slugs(values: list[str]) -> str:
    return "[" + ", ".join(values) + "]"


async def stored_view(env: Env, doc_id: int, out: BaseModel) -> tuple[list[str], bool]:
    """What production would store for this decision: the competitor links `apply_triage`
    writes (the model's slugs, plus the document's own-feed competitor, never JFrog), and
    whether the keyword screen passed the document (so it would reach triage at all).
    `apply_triage` runs in a transaction that is rolled back: the fixture stays as loaded."""
    async with env.session_factory() as session:
        screened = (await session.get(Document, doc_id)).scope_hint is not None
        await apply_triage(session, doc_id, out)
        links = sorted(
            await session.scalars(
                select(Competitor.slug)
                .join(DocumentCompetitor, DocumentCompetitor.competitor_id == Competitor.id)
                .where(DocumentCompetitor.document_id == doc_id)
            )
        )
        await session.rollback()
    return links, screened
