"""The shared plumbing of FrogEye's verified LLM writers: stage-B signal analysis
(agents/nodes/signal_analysis.py), the weekly briefs (intel/briefs.py) and the newsroom's
checker (graphs/newsroom/check.py), plus the briefing reserve every budgeted batch keeps (the
daily graph's stage B, the facts extractor, the briefs) and the ledger reads of every
backfill CLI (cli/_backfill.py). One copy of each rule, so a fix lands once:

- `ref_cleaner(kinds)`: a text without the inline citation markers a model writes
  despite its prompt ("[doc:1]", "(kb:2; signal:3)", "[1]", "[1][2]", "[1, 2]", "[1–3]",
  "(see doc:4)", and "see doc:4" where it ends a clause, with its own full stop when it is
  a sentence of its own: "It ships. See doc:4." reads "It ships."); a bare ref used as a
  word ("as doc:12 reports", "See doc:4 for more") becomes "the source" (or "the sources"
  for a list, Oxford comma included), capitalised where it opens a sentence (an
  abbreviation's full stop, "e.g.", opens none). Each writer names its own ref kinds.
- `ask_until_judged`: the fast checker sometimes judges S1 only; the items
  a reply leaves out are asked again, alone and renumbered, once (the briefs ask each of
  them in a call of its own, as their checker can answer a multi-item ask with one
  verdict). `fact_check` (stage B, the briefs) raises `IncompleteFactCheck` when some are
  still missing, so a half-checked text is never stored; the newsroom fails each such
  sentence closed instead.
- `passes`, `unsupported`: fail closed. An item is kept only when every verdict
  for it passes it: supported, or advice in an item that may be advice (an action).
- `ledger_floor`, `calls_cost`, `ledger_cost_since`: a writer's own LLM cost, the ledger
  rows written after a floor with its request hashes (under a fan-out every caller shares
  the run's ctx), or every row after it (a backfill CLI, alone under the run lock).
- `reserve_reached`: what is left of today's (or the total) budget is under the
  newsroom's reserve, so a batch starts nothing more and the briefing can still be written.
"""

import re
from collections.abc import Awaitable, Callable, Iterable
from typing import Any, Protocol

from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.agents.runtime.accounted import AccountedLLM
from app.agents.runtime.context import RunCtx
from app.agents.runtime.prompts import Prompt
from app.agents.runtime.types import Tier
from app.core.config import Settings
from app.models.agents import LlmCall

# ---- inline citation markers ---------------------------------------------------------

_NUMS = r"\d+(?:\s*[-–—,;]\s*\d+)*"
_SENTENCE_START = re.compile(r"(?:^|[.!?][\"'”’)\]]*)\s*$")
_ABBREVIATION = re.compile(r"\b(?:e\.g|i\.e|etc|vs|cf|incl|approx|viz)\.\s*$", re.IGNORECASE)
_SPACE_BEFORE_PUNCT = re.compile(r"\s+([.,;:!?])")


def _flat(text: str) -> str:
    return " ".join(text.split())


def ref_cleaner(kinds: Iterable[str]) -> Callable[[str | None], str]:
    """The cleaner for refs of these kinds ("doc", "kb", "signal", ...): removes inline
    markers, turns a bare ref used as a word into "the source(s)", collapses blank runs and
    the space before punctuation."""
    ref = rf"(?:{'|'.join(kinds)}):\d+"
    refs = rf"{ref}(?:\s*[,;]?\s*(?:and\s+)?{ref})*"  # "a, b and c", "a, b, and c"
    marker = re.compile(
        r"\s*(?:"
        rf"[\[(]\s*{refs}\s*[\])]"
        rf"|(?:\[\s*{_NUMS}\s*\])+"
        rf"|\(\s*see\s+{refs}\s*\)"
        # a see-pointer only where it ends a clause: inside one, the ref is a word
        rf"|,?\s*\bsee\s+{refs}\b(?=\s*(?:[.,;:!?)\]]|$))"
        r")",
        re.IGNORECASE,
    )
    # a see-pointer that is a sentence of its own goes with its own stop: removed
    # first, so "It ships. See doc:4." reads "It ships.", never "It ships.."
    sentence = re.compile(rf"(?:(?<=[.!?])|^)\s*\bsee\s+{refs}\b\s*[.!?]", re.IGNORECASE)
    bare = re.compile(rf"\b{refs}\b", re.IGNORECASE)
    one = re.compile(ref, re.IGNORECASE)

    def noun(match: re.Match[str]) -> str:
        word = "the sources" if len(one.findall(match.group(0))) > 1 else "the source"
        opens = _SENTENCE_START.search(match.string, 0, match.start()) is not None
        after_abbreviation = _ABBREVIATION.search(match.string, 0, match.start()) is not None
        return word.capitalize() if opens and not after_abbreviation else word

    def clean(text: str | None) -> str:
        kept = marker.sub("", sentence.sub("", text or ""))
        return _SPACE_BEFORE_PUNCT.sub(r"\1", _flat(bare.sub(noun, kept)))

    return clean


# ---- the fact-check with one re-ask ---------------------------------------------------


class IncompleteFactCheck(Exception):
    """The checker left items without a verdict, also when asked again."""

    def __init__(self, missing: int, total: int) -> None:
        super().__init__(f"no verdict for {missing} of {total} items after a re-ask")


Request = Callable[[list[int]], tuple[type[BaseModel], dict[str, Any]]]


async def ask_until_judged(
    count: int, ask: Callable[[list[int]], Awaitable[Iterable[Any]]], *, each: bool = False
) -> tuple[dict[int, list[Any]], list[int]]:
    """The verdicts of `count` items, by item index, and the items still without one.
    `ask(pending)` makes one checker call over the pending items, numbered S1..S<len> in
    order, and returns its verdicts; the items a reply leaves out are asked again, alone
    and renumbered, once: together, or with `each` one call per item (S1 alone), for a
    checker that answers a multi-item ask with one verdict. It always asks at least once."""
    found: dict[int, list[Any]] = {}

    async def judge(asked: list[int]) -> None:
        for v in await ask(asked):  # the schema's Literal ids are S1..S<len(asked)>
            found.setdefault(asked[int(v.sentence_id[1:]) - 1], []).append(v)

    await judge(list(range(count)))
    left = [i for i in range(count) if i not in found]
    batches = [[i] for i in left] if each else ([left] if left else [])
    for batch in batches:  # the re-ask, once
        await judge(batch)
    return found, [i for i in left if i not in found]


async def fact_check(
    llm: AccountedLLM,
    count: int,
    request: Request,
    *,
    task: str,
    prompt: Prompt,
    keys: list[str],
    ctx: RunCtx,
    tier: Tier = "fast",
    each: bool = False,
) -> dict[int, list[Any]]:
    """The verdicts of `count` items, by item index. `request(pending)` builds the schema
    (verdict ids S1..S<len(pending)>) and the variables for the pending items, in order;
    the items a reply leaves out are asked again, alone and renumbered, once (with `each`,
    one call per item: `ask_until_judged`). Each request's key joins `keys` (the caller's
    cost). Raises IncompleteFactCheck."""

    async def ask(pending: list[int]) -> Iterable[Any]:
        schema, variables = request(pending)
        keys.append(llm.request_key(tier, schema, prompt=prompt, variables=variables))
        out = await llm.structured(task, tier, schema, prompt=prompt, variables=variables, ctx=ctx)
        return out.verdicts  # type: ignore[attr-defined]  # Literal ids S1..S<pending>

    found, missing = await ask_until_judged(count, ask, each=each)
    if missing:
        raise IncompleteFactCheck(len(missing), count)
    return found


class CheckedItem(Protocol):
    @property
    def section(self) -> str: ...

    @property
    def index(self) -> int | None: ...

    @property
    def advice_ok(self) -> bool: ...


def passes(verdict: Any, *, advice_ok: bool) -> bool:
    """Supported passes; advice passes unsupported only where it may be advice."""
    return bool(verdict.supported or (advice_ok and verdict.kind == "advice"))


def unsupported(
    items: Iterable[CheckedItem], verdicts: dict[int, list[Any]]
) -> set[tuple[str, int | None]]:
    """Fail closed: the (section, index) of every item without a verdict, or with one
    verdict that does not pass it."""
    return {
        (it.section, it.index)
        for i, it in enumerate(items)
        if not verdicts.get(i) or not all(passes(v, advice_ok=it.advice_ok) for v in verdicts[i])
    }


# ---- the ledger and the briefing reserve --------------------------------------------------------


async def ledger_floor(session_factory: async_sessionmaker[AsyncSession]) -> int:
    """The highest ledger id now: calls booked later have a higher one."""
    async with session_factory() as session:
        return int(await session.scalar(select(func.coalesce(func.max(LlmCall.id), 0))) or 0)


async def calls_cost(
    session_factory: async_sessionmaker[AsyncSession], floor: int, keys: list[str]
) -> float:
    """A caller's own calls: ledger rows written after `floor` with these request hashes."""
    if not keys:
        return 0.0
    async with session_factory() as session:
        total = await session.scalar(
            select(func.coalesce(func.sum(LlmCall.cost_usd), 0)).where(
                LlmCall.id > floor, LlmCall.request_hash.in_(keys)
            )
        )
    return float(total or 0)


async def ledger_cost_since(session_factory: async_sessionmaker[AsyncSession], floor: int) -> float:
    """Every ledger row written after `floor`: a backfill's spend while it held the run lock."""
    async with session_factory() as session:
        total = await session.scalar(
            select(func.coalesce(func.sum(LlmCall.cost_usd), 0)).where(LlmCall.id > floor)
        )
    return float(total or 0)


async def reserve_reached(llm: AccountedLLM, settings: Settings) -> bool:
    """What is left of today's (or the total) LLM budget is under the newsroom's
    reserve, so nothing more is started (counted as reserved, not a budget stop)."""
    left = min(
        settings.llm_daily_budget_usd - await llm.spent_today(),
        settings.llm_total_budget_usd - await llm.spent_total(),
    )
    return left < settings.newsroom_reserve_usd
