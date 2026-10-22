"""Posting tagger: the smart model labels each open job posting with capability slugs.

The tags show which JFrog product areas a competitor is hiring for. Per batch:

- Selection: open postings of non-self competitors with `tagged_at IS NULL` that are not
  given up (`app.agents.retries`), newest first, at most `limit` (default
  `settings.posting_tag_cap`), so the cap counts only postings still eligible. A changed
  `content_hash` already cleared a posting's tags (and its failures) in
  `collectors/jobs/store.py`, so it is picked up again here.
- One call per distinct `content_hash` (the same title and description is asked once and
  written to every posting that has it), `TAG_CONCURRENCY` at a time. The LLM-call cache key is the
  request: the prompt carries the capability list and the schema's Literal enum is the slug
  list, so a taxonomy change is a miss.
- The answer is a strict `{focus, product_role, tags: list[Literal[capability slugs]]}`,
  the role's own focus first so the tags follow from it; its tags are kept only when
  `product_role` is true. `[]` is valid: the posting is stored as tagged with no tags, the
  explicit "untagged" state. An `LLMError`
  (a slug outside the taxonomy included) or a refusal leaves the posting untagged, logged, and
  counts in `failed`; the failure is recorded with its reason and the posting is tried again
  next run, until its third failed day gives it up (`given_up`).
- What the model reads is the role, not the company (`role_text`): the title, the department
  and team, and the description without the company's standard text. A line is standard
  text when at least `BOILERPLATE_MIN` and `BOILERPLATE_SHARE` of the company's open postings
  carry it, in two departments or more (`boilerplate_lines`; a role's text shared by its
  own openings in several cities stays), and a line that introduces the company ("About
  GitLab", "Harness is the AI software delivery platform ...") is dropped too. A description
  that would be left empty is read whole.
- A role in a department whose work is no product area (`GENERAL_DEPARTMENTS`: sales,
  marketing, G&A, from the rule-based department of `collectors/jobs/taxonomy.py`) is stored
  as tagged with no tags, without a call.
- The scraped title and description reach the model only through `untrusted()`.
- Short sessions: the posting rows are read and closed before the first call, and every
  result is written in its own short transaction. A budget stop is reported, never raised; the
  postings not reached stay untagged.
- no call is started once what is left of the day's (or the total) budget is
  under the newsroom's reserve (`reserve_reached`): the briefing keeps it. The postings not
  started are counted in `reserved`, stay pending and record no failed day.
"""

import asyncio
import logging
import math
import re
from collections import Counter, defaultdict
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime
from functools import lru_cache
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, create_model
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.agents.retries import RetryColumns, cleared, eligible, record_failure
from app.agents.runtime.accounted import AccountedLLM, BudgetExceeded, LLMError, LLMRefusal
from app.agents.runtime.context import RunCtx
from app.agents.runtime.prompts import load_prompt, untrusted
from app.agents.runtime.types import Tier
from app.agents.verified_llm import reserve_reached
from app.core.config import Settings
from app.models import Capability, Competitor, JobPosting

logger = logging.getLogger(__name__)

TASK = "posting_tagger"
# the smart tier: the fast one listed areas for nearly every role, office managers and lawyers
# included; with the cached capability list a call costs about $0.0001
TAG_TIER: Tier = "smart"
TAG_CONCURRENCY = 4
MAX_WORDS = 400  # of the description the model reads
MAX_TAGS = 5  # kept per posting (the prompt asks for at most 5)
# a line in at least this many, and this share, of a company's open postings, across two
# departments or more, is the company's standard text, not the role's
BOILERPLATE_MIN = 3
BOILERPLATE_SHARE = 0.3
_HEADINGS = ("about us", "who we are", "about the company", "our company")
# roles whose work is no product area, whatever the company sells: tagged [] without a call
GENERAL_DEPARTMENTS = frozenset({"Sales", "Marketing", "G&A"})


RETRIES = RetryColumns(JobPosting.tag_failures, JobPosting.tag_failed_at, JobPosting.tag_error)


@dataclass(frozen=True)
class TagBatch:
    tagged: int  # postings stored as tagged (an empty tag list counts)
    failed: int  # postings left untagged by an LLM error or refusal
    budget_stopped: bool
    given_up: int = 0  # of the failed, those whose third failed day it was
    reserved: int = 0  # postings not started, to keep the newsroom's reserve


@lru_cache(maxsize=16)
def make_tag_model(slugs: tuple[str, ...]) -> type[BaseModel]:
    """The strict output schema: the role's focus, whether that focus is a product area,
    then `tags`, a list of the capability slugs (in that order, so the tags follow the
    reasoning)."""
    tag = Literal[slugs]  # type: ignore[valid-type]
    return create_model(
        "PostingTags",
        __config__=ConfigDict(extra="forbid"),
        focus=(str, ...),
        product_role=(bool, ...),
        tags=(list[tag], ...),  # type: ignore[valid-type]
    )


@dataclass(frozen=True)
class _Posting:
    id: int
    competitor_id: int
    title: str
    description: str | None
    content_hash: str
    department: str  # the rule-based one (collectors/jobs/taxonomy.py)
    board_department: str | None  # as the board names it, else the rule-based one
    team: str | None


def _line_key(line: str) -> str:
    return " ".join(line.casefold().split())


def boilerplate_lines(postings: Iterable[tuple[str | None, str | None]]) -> frozenset[str]:
    """The normalised lines that are a company's standard text, from the (description,
    department) of each of its open postings (see the module docstring)."""
    counts: Counter[str] = Counter()
    departments: defaultdict[str, set[str]] = defaultdict(set)
    total = 0
    for description, department in postings:
        total += 1
        keys = {k for line in (description or "").splitlines() if (k := _line_key(line))}
        counts.update(keys)
        for k in keys:
            departments[k].add(department or "")
    need = max(BOILERPLATE_MIN, math.ceil(BOILERPLATE_SHARE * total))
    return frozenset(k for k, n in counts.items() if n >= need and len(departments[k]) >= 2)


def _intro(names: Iterable[str]) -> re.Pattern[str]:
    """A line that introduces the company: a heading ("About GitLab", "About us") or a
    sentence that defines it ("Harness is the ...", "Wiz is a ...")."""
    found = sorted({n for n in names if n}, key=len, reverse=True)
    company = "(?:" + "|".join(re.escape(n) for n in found) + ")" if found else "(?!)"
    headings = "|".join(re.escape(h) for h in _HEADINGS)
    return re.compile(
        rf"^(?:(?:about\s+{company}|{headings})\s*:?\s*$|{company}\s+is\s+(?:the|a|an)\b)",
        re.I,
    )


def role_text(description: str | None, boilerplate: frozenset[str], names: Iterable[str]) -> str:
    """The role's own lines of a description: without the company's standard text and the
    lines that introduce the company. All of it when nothing would be left."""
    intro = _intro(names)
    kept = [
        line
        for line in (description or "").splitlines()
        if (k := _line_key(line)) and k not in boilerplate and not intro.match(line.strip())
    ]
    return "\n".join(kept) if kept else (description or "")


def posting_view(
    title: str,
    description: str | None,
    content_hash: str,
    *,
    department: str | None = None,
    team: str | None = None,
) -> str:
    """What the model reads: the title, the department and team, and the first ~400 words of
    the role's description, as one untrusted block whose id is the content hash."""
    words = (description or "").split()
    body = " ".join(words[:MAX_WORDS])
    where = "; ".join(
        f"{label}: {value.strip()}"
        for label, value in (("Department", department), ("Team", team))
        if value and value.strip()
    )
    head = "\n".join(part for part in (title.strip(), where) if part)
    text = f"{head}\n\n{body}" if body else head
    return untrusted(content_hash[:12], text, max_chars=4000)


async def _select(session_factory: async_sessionmaker[AsyncSession], limit: int) -> list[_Posting]:
    async with session_factory() as session:
        rows = await session.execute(
            select(
                JobPosting.id,
                JobPosting.competitor_id,
                JobPosting.title,
                JobPosting.description_text,
                JobPosting.content_hash,
                JobPosting.department,
                func.coalesce(JobPosting.department_raw, JobPosting.department),
                JobPosting.team,
            )
            .join(Competitor, Competitor.id == JobPosting.competitor_id)
            .where(
                JobPosting.closed_at.is_(None),
                JobPosting.tagged_at.is_(None),
                eligible(RETRIES),
                ~Competitor.is_self,
            )
            .order_by(JobPosting.first_seen_at.desc(), JobPosting.id.desc())
            .limit(limit)
        )
        return [_Posting(*r) for r in rows.all()]


async def _companies(
    session_factory: async_sessionmaker[AsyncSession], competitor_ids: set[int]
) -> dict[int, tuple[frozenset[str], tuple[str, ...]]]:
    """competitor id -> (its standard text lines, the names it goes by), read from all its
    open postings, tagged or not."""
    async with session_factory() as session:
        rows = await session.execute(
            select(
                JobPosting.competitor_id, JobPosting.description_text, JobPosting.department
            ).where(JobPosting.closed_at.is_(None), JobPosting.competitor_id.in_(competitor_ids))
        )
        by_company: defaultdict[int, list[tuple[str | None, str | None]]] = defaultdict(list)
        for cid, description, department in rows.all():
            by_company[cid].append((description, department))
        names = await session.execute(
            select(Competitor.id, Competitor.name, Competitor.aliases).where(
                Competitor.id.in_(competitor_ids)
            )
        )
        known = {cid: (name, *(aliases or ())) for cid, name, aliases in names.all()}
    return {
        cid: (boilerplate_lines(by_company.get(cid, [])), known.get(cid, ()))
        for cid in competitor_ids
    }


async def _capabilities(
    session_factory: async_sessionmaker[AsyncSession],
) -> list[dict[str, Any]]:
    async with session_factory() as session:
        rows = await session.execute(
            select(Capability.slug, Capability.name, Capability.description).order_by(Capability.id)
        )
        return [{"slug": s, "name": n, "description": d} for s, n, d in rows.all()]


async def tag_postings_batch(
    session_factory: async_sessionmaker[AsyncSession],
    llm: AccountedLLM,
    settings: Settings,
    ctx: RunCtx,
    *,
    limit: int | None = None,
    now: datetime | None = None,
) -> TagBatch:
    """Tag up to `limit` (default `posting_tag_cap`) untagged open postings, newest first.
    `now` (default the clock) dates a failure's day."""
    moment = now or datetime.now(UTC)
    wanted = settings.posting_tag_cap if limit is None else limit
    capabilities = await _capabilities(session_factory)
    if wanted < 1 or not capabilities:
        return TagBatch(0, 0, False)
    picked = await _select(session_factory, wanted)
    if not picked:
        return TagBatch(0, 0, False)

    schema = make_tag_model(tuple(c["slug"] for c in capabilities))
    prompt = load_prompt(TASK)
    general = [p.id for p in picked if p.department in GENERAL_DEPARTMENTS]
    if general:  # no product area by department: tagged [] without a call
        async with session_factory() as session:
            await session.execute(
                update(JobPosting)
                .where(JobPosting.id.in_(general))
                .values(
                    tags=[],
                    tag_version=prompt.version,
                    tagged_at=datetime.now(UTC),
                    **cleared(RETRIES),
                )
            )
            await session.commit()
    picked = [p for p in picked if p.department not in GENERAL_DEPARTMENTS]
    companies = await _companies(session_factory, {p.competitor_id for p in picked})
    groups: dict[str, tuple[str, list[int]]] = {}  # content hash -> (what the model reads, ids)
    for p in picked:  # one call per distinct content
        if p.content_hash not in groups:
            boilerplate, names = companies[p.competitor_id]
            view = posting_view(
                p.title,
                role_text(p.description, boilerplate, names),
                p.content_hash,
                department=p.board_department,
                team=p.team,
            )
            groups[p.content_hash] = (view, [])
        groups[p.content_hash][1].append(p.id)

    sem = asyncio.Semaphore(TAG_CONCURRENCY)
    counts = {"tagged": len(general), "failed": 0, "given_up": 0, "reserved": 0}
    budget = {"stopped": False}

    async def one(chash: str, view: str, ids: list[int]) -> None:
        async with sem:
            if await reserve_reached(llm, settings):  # the briefing keeps its reserve
                counts["reserved"] += len(ids)
                return
            try:
                out = await llm.structured(
                    TASK,
                    TAG_TIER,
                    schema,
                    prompt=prompt,
                    variables={
                        "capabilities": capabilities,
                        "posting": view,
                    },
                    ctx=ctx,
                )
            except BudgetExceeded:
                budget["stopped"] = True
                return
            except (LLMError, LLMRefusal) as exc:
                logger.warning("posting tags: %s postings left untagged: %s", len(ids), exc)
                counts["failed"] += len(ids)
                async with session_factory() as session:
                    gave_up = await record_failure(
                        session,
                        JobPosting,
                        RETRIES,
                        JobPosting.id.in_(ids),
                        JobPosting.content_hash == chash,
                        reason=f"{type(exc).__name__}: {exc}",
                        now=moment,
                    )
                    await session.commit()
                if gave_up:
                    logger.warning(
                        "posting tags: %s postings given up after their third failed day: %s",
                        gave_up,
                        exc,
                    )
                counts["given_up"] += gave_up
                return
            found = out.tags if out.product_role else []  # type: ignore[attr-defined]
            tags = list(dict.fromkeys(found))[:MAX_TAGS]
            async with session_factory() as session:
                # The hash guard: a posting rewritten meanwhile keeps its cleared state.
                await session.execute(
                    update(JobPosting)
                    .where(JobPosting.id.in_(ids), JobPosting.content_hash == chash)
                    .values(
                        tags=tags,
                        tag_version=prompt.version,
                        tagged_at=datetime.now(UTC),
                        **cleared(RETRIES),  # no stale failures once tagged
                    )
                )
                await session.commit()
            counts["tagged"] += len(ids)

    results = await asyncio.gather(
        *(one(h, view, ids) for h, (view, ids) in groups.items()), return_exceptions=True
    )
    for res in results:
        if isinstance(res, Exception):  # a database error: log it, the rest of the batch stands
            logger.error("posting tags: a posting group failed: %r", res)
            counts["failed"] += 1
        elif isinstance(res, BaseException):
            raise res
    return TagBatch(
        counts["tagged"],
        counts["failed"],
        budget["stopped"],
        counts["given_up"],
        counts["reserved"],
    )
