"""Hiring aggregates over `job_postings`: the open-roles series and a competitor's summary.

One rule everywhere: a posting is OPEN on local day D when `first_seen_at::date <= D`
and (`closed_at` is null or `closed_at::date > D`). Days are in `settings.timezone`
(Asia/Jerusalem). `open_now` is that rule on today, which equals "not closed"; a delta is the
count on today minus the count on today - 30 under the same rule, so a chart's last point
and the summary always agree.

Which boards count, one definition every reader uses (hiring, the
competitor postings and the product area). A competitor's CURRENT boards are its enabled
`job_board` sources (`current_board`): a board replaced by another (disabled) is no longer
read, so its postings could never close; they are history no reader counts. A
current board is measured once it has been listed in full (`listed_board`:
`sources.first_complete_fetch_at`, set by the first listing with `postings_complete`): a
partial listing, such as a Greenhouse page short of its `meta.total`, measures nothing, and until a
complete one readers serve no numbers. Postings count only through a listed board, so a second board
only ever listed in part adds nothing to a listed one.

A listed board is read only while it keeps being listed in full: its
latest complete listing is `sources.last_complete_fetch_at` (its first when none is
recorded). A board not listed in full for more than `settings.board_stale_days` local days
(an ATS switch, a persistent 403, a body over the cap, partial listings) is STALE: it serves
no current numbers ("Job board not readable since <day>"), the threat and the brief treat it
as no board, and its series end at its last complete listing, never carried flat through
days FrogEye did not read. A FRESH board is listed and not stale; the current counts read
fresh boards only, on the competitor's as-of day: the last complete listing of its fresh
boards (the oldest of them, so every board counted was read through that day). Before a
board's first run of the day, that is yesterday's listing.

A board's history starts at its first snapshot, `board_since`: the local day of its first
complete listing (the earliest of the competitor's current boards). Before 30 days of
history there is no snapshot 30 days back to compare with, so every 30-day change is
None (the total, per tag, and the threat's hiring trend, app.intel.profile), never the whole
count read as growth; and a competitor's series start at `board_since`, never zero-filled
over days before the board was read. A product area sums many boards of different ages: unfolded,
each competitor's series starts at its own `board_since` (`competitor_since`), and the area's total
at the earliest of them.

Keys: a posting's location key is its country when known, else "Remote" when it is remote,
else "Unknown"; its competitor key is its competitor's slug. Breakdowns by location or by
competitor show the top 8 keys and fold the rest into "Other".
The self competitor never has hiring data: its summary is empty with `has_board` False
and it is skipped by the series.

Tag coverage: every open posting is tagged (tags found),
untagged (tagged with none: the explicit "untagged" state), pending (not tagged
yet), or given up (`app.agents.retries`: its tagging failed on 3 days). `tag_state` names
a posting's state, and the summary counts them over the open postings, so a partly tagged
board's tag counts are never read as the whole board.

Query budget: `open_roles_series` is one statement, and so is `open_roles_breakdowns` for
any number of breakdowns (the deep dive reads its five series at once); `hiring_summary` is
three (board flag, the open postings' dimensions, the 20 newest rows). Counting is done on
thin projections, so a 600-posting board and 180 days stay quick (indexes on competitor +
closed/first-seen).
"""

from collections import Counter
from collections.abc import Collection, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Literal

from sqlalchemy import ColumnElement, and_, func, text
from sqlalchemy.dialects import postgresql
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.retries import MAX_FAILED_DAYS
from app.core.config import get_settings
from app.core.days import local_day
from app.models import Source
from app.models.enums import SourceKind

TOP_LOCATIONS = 8  # also the top competitors kept by a "competitor" breakdown
OTHER = "Other"
RECENT = 20
DELTA_DAYS = 30

By = Literal["total", "department", "seniority", "location", "tag", "competitor"]
TagState = Literal["pending", "tagged", "untagged", "gave_up"]
TAG_STATES: tuple[TagState, ...] = ("tagged", "pending", "untagged", "gave_up")


def current_board() -> ColumnElement[bool]:
    """A competitor's current job board: an enabled `job_board` source."""
    return and_(Source.kind == SourceKind.job_board.value, Source.enabled.is_(True))


def listed_board() -> ColumnElement[bool]:
    """A current board listed in full at least once: its postings are measured."""
    return and_(current_board(), Source.first_complete_fetch_at.is_not(None))


def first_listed() -> ColumnElement[datetime]:
    """The earliest first complete listing of the boards aggregated over: board_since."""
    return func.min(Source.first_complete_fetch_at)


def last_listing() -> ColumnElement[datetime]:
    """A board's latest complete listing; its first when none is recorded."""
    return func.coalesce(Source.last_complete_fetch_at, Source.first_complete_fetch_at)


def fresh_since(now: datetime, tz: str | None = None) -> date:
    """The earliest last-complete-listing day of a fresh board: a board last listed in
    full before it is stale."""
    return local_day(now, tz) - timedelta(days=get_settings().board_stale_days)


def fresh_board(now: datetime, tz: str | None = None) -> ColumnElement[bool]:
    """A listed board that is not stale on `now` (see the module docstring)."""
    zone = tz or get_settings().timezone
    day = func.date(func.timezone(zone, last_listing()))
    return and_(listed_board(), day >= fresh_since(now, zone))


def tag_state(tagged_at: datetime | None, tags: Sequence[str] | None, failures: int) -> TagState:
    """A posting's tagging state (see the module docstring)."""
    if tagged_at is not None:
        return "tagged" if tags else "untagged"
    return "gave_up" if (failures or 0) >= MAX_FAILED_DAYS else "pending"


def _sql(clause: ColumnElement) -> str:
    """The rule above as SQL text, for the raw statements below (one definition)."""
    compiled = clause.compile(dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True})
    return str(compiled)


_CURRENT, _LISTED = _sql(current_board()), _sql(listed_board())
_LAST = _sql(last_listing())
# a board is fresh when its latest complete listing's local day is on or after :fresh
_FRESH = f"(({_LAST}) AT TIME ZONE CAST(:tz AS text))::date >= CAST(:fresh AS date)"
_ON_BOARD = f"JOIN sources ON sources.id = jp.source_id AND {_LISTED}"
_ON_FRESH = f"{_ON_BOARD} AND {_FRESH}"
# the newest day discovery checked for a board and found none (its cached misses)
_CHECKED = """
    (SELECT max(x #>> '{}') FROM sources s2, jsonb_path_query(s2.config, '$.checked.*') x
     WHERE s2.competitor_id = c.id AND s2.kind = 'job_board'
       AND s2.config @> '{"missing": true}')
"""
# (has a current board, its first listing, its newest listing, the as-of of its fresh boards,
# discovery's last check for one)
_BOARD_FLAGS = f"""
    EXISTS (SELECT 1 FROM sources WHERE sources.competitor_id = c.id AND {_CURRENT}),
    (SELECT {_sql(first_listed())} FROM sources
     WHERE sources.competitor_id = c.id AND {_CURRENT}),
    (SELECT max({_LAST}) FROM sources WHERE sources.competitor_id = c.id AND {_LISTED}),
    (SELECT min({_LAST}) FROM sources
     WHERE sources.competitor_id = c.id AND {_LISTED} AND {_FRESH}),
    {_CHECKED}
"""


def checked_at(raw: str | None) -> datetime | None:
    """Discovery's stored check time (an ISO string), or None."""
    if not raw:
        return None
    try:
        return datetime.fromisoformat(raw)
    except ValueError:
        return None


_LOCATION_KEY = "COALESCE(jp.country, CASE WHEN jp.remote THEN 'Remote' ELSE 'Unknown' END)"
_KEY: dict[str, tuple[str, str]] = {  # by -> (key expression, extra FROM clause)
    "total": ("'total'", ""),
    "department": ("jp.department", ""),
    "seniority": ("jp.seniority", ""),
    "location": (_LOCATION_KEY, ""),
    "tag": ("t.tag", "CROSS JOIN LATERAL unnest(jp.tags) AS t(tag)"),
    "competitor": ("jp.competitor", ""),
}
_FOLDED = frozenset({"location", "competitor"})  # top TOP_LOCATIONS by the latest day + Other


@dataclass(frozen=True)
class SeriesPoint:
    day: date
    key: str
    count: int


@dataclass(frozen=True)
class PostingRow:
    id: int
    title: str
    department: str
    seniority: str
    location: str | None
    country: str | None
    remote: bool
    url: str | None
    first_seen_at: datetime
    tags: list[str]
    tag_state: TagState = "pending"


@dataclass(frozen=True)
class TagCoverage:
    """The open postings by tagging state: what the tag counts are a share of."""

    tagged: int = 0
    pending: int = 0
    untagged: int = 0
    gave_up: int = 0

    @property
    def total(self) -> int:
        return self.tagged + self.pending + self.untagged + self.gave_up

    @property
    def partial(self) -> bool:
        """Some open postings are not tagged yet, or were given up: the tags are a subset."""
        return bool(self.pending or self.gave_up)


@dataclass
class HiringSummary:
    has_board: bool  # an enabled job board was found (listed in full or not)
    open_now: int = 0  # open on the as-of day (`as_of`)
    delta_30d: int | None = None  # None before DELTA_DAYS of board history
    board_since: date | None = None  # its first complete listing's day; None before one
    # the fresh boards' last complete listing, the oldest of them: what the counts are
    # as of. None when no listed board is fresh (stale) or none is listed.
    as_of: datetime | None = None
    last_listed_at: datetime | None = None  # its newest complete listing, fresh or stale
    board_checked_at: datetime | None = None  # discovery's last check, found none
    by_department: list[tuple[str, int]] = field(default_factory=list)
    by_seniority: list[tuple[str, int]] = field(default_factory=list)
    by_location: list[tuple[str, int]] = field(default_factory=list)  # top 8 + "Other"
    tags: list[tuple[str, int, int | None]] = field(default_factory=list)  # (slug, open, delta)
    coverage: TagCoverage = field(default_factory=TagCoverage)  # the open postings
    recent: list[PostingRow] = field(default_factory=list)

    @property
    def listed(self) -> bool:
        """An enabled board has been listed in full at least once."""
        return self.has_board and self.board_since is not None

    @property
    def stale(self) -> bool:
        """Listed, but no board was listed in full for `board_stale_days`."""
        return self.listed and self.as_of is None

    @property
    def measured(self) -> bool:
        """A fresh board: its numbers are current."""
        return self.listed and not self.stale

    @property
    def as_of_day(self) -> date | None:
        return local_day(self.as_of) if self.as_of is not None else None

    @property
    def last_listed_day(self) -> date | None:
        return local_day(self.last_listed_at) if self.last_listed_at is not None else None


def not_readable_note(last_listed: date) -> str:
    """The coverage note of a stale board."""
    return f"Job board not readable since {last_listed.day} {last_listed:%b} {last_listed.year}"


def board_since(first_listed_at: datetime | None, tz: str | None = None) -> date | None:
    """A board's first snapshot day: the local day of its first complete listing."""
    return local_day(first_listed_at, tz) if first_listed_at is not None else None


def has_history(since: date | None, today: date) -> bool:
    """Whether a board has been read for DELTA_DAYS days, so a 30-day change compares two
    snapshots. Without a first snapshot there is no history at all."""
    return since is not None and (today - since).days >= DELTA_DAYS


def _ranked(counts: Counter[str]) -> list[tuple[str, int]]:
    return sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))


def _top_with_other(counts: Counter[str], n: int = TOP_LOCATIONS) -> list[tuple[str, int]]:
    ranked = _ranked(counts)
    if len(ranked) <= n:
        return ranked
    return [*ranked[:n], (OTHER, sum(c for _, c in ranked[n:]))]


async def open_roles_series(
    session: AsyncSession,
    competitor_ids: Sequence[int],
    *,
    days: int,
    by: By,
    now: datetime,
) -> list[SeriesPoint]:
    """Open postings per local day for the last `days` days, today included, summed over the
    competitors (self is skipped). Ordered by day, then key. Every key that occurs in the
    window has a point on every day (0 when none), so lines are continuous; `total` has a
    point per day even when nothing is open. `location` keeps its top 8 keys by the latest
    day's count and folds the rest into "Other". `tag` counts a posting once per tag and has
    no point for untagged postings."""
    return (await open_roles_breakdowns(session, competitor_ids, days=days, by=(by,), now=now))[by]


async def open_roles_breakdowns(
    session: AsyncSession,
    competitor_ids: Sequence[int],
    *,
    days: int,
    by: Sequence[By],
    now: datetime,
    tags: Collection[str] | None = None,
    since: date | None = None,
    competitor_since: Mapping[str, date] | None = None,
    fold: bool = True,
    until: date | None = None,
    stale: bool = False,
) -> dict[By, list[SeriesPoint]]:
    """`open_roles_series` for several breakdowns in ONE statement, each by the same rules.
    `competitor` keys a posting by its competitor's slug and, like `location`, keeps the top 8
    keys by the latest day's count, folding the rest into "Other". With `tags`, only postings
    carrying at least one of them count (once each, outside the `tag` breakdown): the hiring
    in a product area. With `since` (a board's first snapshot) the window starts no
    earlier than that day: no day before the board was read is drawn, let alone as zero.
    With `fold` False, `location` and `competitor` keep every key (a chart that folds on the
    client), and then `competitor_since` (each competitor's board_since, by slug) starts
    a `competitor` key's points at its own board's first snapshot. A server-side fold has no
    per-member start (the client's Other starts once all its members are read), so the
    two together are a ValueError.

    Only fresh boards count, and the window ends at `until` (default today): a
    reader passes the as-of day, the last day every board counted was listed in full, so no
    day after it is carried flat. With `stale` every listed board counts, fresh or not: the
    history of a stale board, ending at its last complete listing."""
    if competitor_since is not None and fold:
        raise ValueError("competitor_since needs fold=False: a folded Other has no one first day")
    wanted = list(dict.fromkeys(by))
    if not competitor_ids or not wanted:
        return {b: [] for b in wanted}
    tz = get_settings().timezone
    end = min(until, local_day(now, tz)) if until is not None else local_day(now, tz)
    start = end - timedelta(days=max(days, 1) - 1)
    if since is not None:
        start = min(max(start, since), end)
    day_list = [start + timedelta(days=i) for i in range((end - start).days + 1)]
    branches = " UNION ALL ".join(
        f"SELECT CAST(:by{i} AS text) AS breakdown, {key} AS key, jp.fs, jp.cl FROM jp {extra}"
        for i, (key, extra) in enumerate(_KEY[b] for b in wanted)
    )
    params: dict[str, object] = {
        "start": start,
        "end": end,
        "tz": tz,
        "ids": list(competitor_ids),
        "fresh": fresh_since(now, tz),
    }
    params |= {f"by{i}": b for i, b in enumerate(wanted)}
    tag_filter = ""
    if tags is not None:
        tag_filter = "AND jp.tags && CAST(:tags AS text[])"
        params["tags"] = sorted(set(tags))
    rows = await session.execute(
        text(
            f"""
            WITH days AS (
                SELECT generate_series(CAST(:start AS date), CAST(:end AS date),
                                       interval '1 day')::date AS day
            ), jp AS (
                SELECT jp.department, jp.seniority, jp.country, jp.remote, jp.tags,
                       c.slug AS competitor,
                       (jp.first_seen_at AT TIME ZONE CAST(:tz AS text))::date AS fs,
                       (jp.closed_at AT TIME ZONE CAST(:tz AS text))::date AS cl
                FROM job_postings jp
                JOIN competitors c ON c.id = jp.competitor_id AND NOT c.is_self
                {_ON_BOARD if stale else _ON_FRESH}
                WHERE jp.competitor_id = ANY(CAST(:ids AS int[])) {tag_filter}
            ), p AS ({branches})
            SELECT p.breakdown, days.day, p.key, count(*)
            FROM days JOIN p ON p.fs <= days.day AND (p.cl IS NULL OR p.cl > days.day)
            GROUP BY p.breakdown, days.day, p.key
            """
        ),
        params,
    )
    counts: dict[str, dict[tuple[date, str], int]] = {b: {} for b in wanted}
    for b, d, k, n in rows.all():
        counts[b][(d, k)] = n
    return {
        b: _series(
            b,
            counts[b],
            day_list,
            end,
            starts=competitor_since if b == "competitor" else None,
            fold=fold,
        )
        for b in wanted
    }


def _series(
    by: By,
    counts: dict[tuple[date, str], int],
    day_list: list[date],
    end: date,
    *,
    starts: Mapping[str, date] | None = None,
    fold: bool = True,
) -> list[SeriesPoint]:
    """Every key on every day from its first (0 when none); `total` always; a folded
    breakdown's top keys. `starts` (unfolded only) gives a key its own first day: a
    competitor's board_since."""
    keys = {k for _, k in counts}
    first = {k: starts[k] for k in keys if starts and k in starts}
    if by == "total":
        keys = {"total"}
    elif fold and by in _FOLDED and len(keys) > TOP_LOCATIONS:
        latest = sorted(keys, key=lambda k: (-counts.get((end, k), 0), k))
        keep = set(latest[:TOP_LOCATIONS])
        folded: dict[tuple[date, str], int] = {}
        for (d, k), n in counts.items():
            key = (d, k if k in keep else OTHER)
            folded[key] = folded.get(key, 0) + n
        counts, keys = folded, {*keep, OTHER}
    return [
        SeriesPoint(d, k, counts.get((d, k), 0))
        for d in day_list
        for k in sorted(keys)
        if d >= first.get(k, day_list[0])
    ]


_OPEN_ON = "(fs <= {d} AND (cl IS NULL OR cl > {d}))"


async def hiring_summary(
    session: AsyncSession, competitor_id: int, *, now: datetime
) -> HiringSummary:
    """One competitor's hiring picture as of its fresh boards' last complete listing (at
    most three statements; one when no board is fresh). Counted whether or not a board has
    been listed in full: a reader serves the numbers only when `measured`."""
    tz = get_settings().timezone
    fresh = fresh_since(now, tz)
    flags = (
        await session.execute(
            text(f"SELECT c.is_self, {_BOARD_FLAGS} FROM competitors c WHERE c.id = :id"),
            {"id": competitor_id, "tz": tz, "fresh": fresh},
        )
    ).first()
    if flags is None or flags[0]:  # unknown or the self competitor: nothing, ever
        return HiringSummary(has_board=False)
    summary = HiringSummary(
        has_board=bool(flags[1]),
        board_since=board_since(flags[2], tz),
        last_listed_at=flags[3],
        as_of=flags[4],
        board_checked_at=checked_at(flags[5]),
    )
    if not summary.measured:  # no fresh board: nothing is current
        return summary
    day = local_day(flags[4], tz)
    history = has_history(summary.board_since, day)
    then = day - timedelta(days=DELTA_DAYS)
    params = {"id": competitor_id, "tz": tz, "today": day, "then": then, "fresh": fresh}
    local = (
        "(jp.first_seen_at AT TIME ZONE CAST(:tz AS text))::date AS fs, "
        "(jp.closed_at AT TIME ZONE CAST(:tz AS text))::date AS cl"
    )
    rows = (
        await session.execute(
            text(
                f"""
                SELECT department, seniority, country, remote, tags, tagged_at, tag_failures,
                       {_OPEN_ON.format(d=":today")} AS open_now,
                       {_OPEN_ON.format(d=":then")} AS open_then
                FROM (SELECT jp.department, jp.seniority, jp.country, jp.remote, jp.tags,
                             jp.tagged_at, jp.tag_failures, {local}
                      FROM job_postings jp {_ON_FRESH} WHERE jp.competitor_id = :id) p
                WHERE {_OPEN_ON.format(d=":today")} OR {_OPEN_ON.format(d=":then")}
                """
            ),
            params,
        )
    ).all()

    departments: Counter[str] = Counter()
    seniorities: Counter[str] = Counter()
    locations: Counter[str] = Counter()
    tags_now: Counter[str] = Counter()
    tags_then: Counter[str] = Counter()
    states: Counter[str] = Counter()
    then_total = 0
    for dept, senior, country, remote, tags, tagged_at, failures, open_now, open_then in rows:
        if open_then:
            then_total += 1
            tags_then.update(tags or [])
        if open_now:
            summary.open_now += 1
            departments[dept] += 1
            seniorities[senior] += 1
            locations[country or ("Remote" if remote else "Unknown")] += 1
            tags_now.update(tags or [])
            states[tag_state(tagged_at, tags, failures)] += 1
    summary.delta_30d = summary.open_now - then_total if history else None
    summary.by_department = _ranked(departments)
    summary.by_seniority = _ranked(seniorities)
    summary.by_location = _top_with_other(locations)
    summary.tags = [
        (slug, n, n - tags_then.get(slug, 0) if history else None)
        for slug, n in sorted(tags_now.items(), key=lambda kv: (-kv[1], kv[0]))
    ]
    summary.coverage = TagCoverage(**{state: states[state] for state in TAG_STATES})

    recent = (
        await session.execute(
            text(
                f"""
                SELECT id, title, department, seniority, location, country, remote, url,
                       first_seen_at, tags, tagged_at, tag_failures
                FROM (SELECT jp.*, {local} FROM job_postings jp {_ON_FRESH}
                      WHERE jp.competitor_id = :id) p
                WHERE {_OPEN_ON.format(d=":today")}
                ORDER BY first_seen_at DESC, id DESC
                LIMIT {RECENT}
                """
            ),
            params,
        )
    ).all()
    summary.recent = [
        PostingRow(
            id=r[0],
            title=r[1],
            department=r[2],
            seniority=r[3],
            location=r[4],
            country=r[5],
            remote=bool(r[6]),
            url=r[7],
            first_seen_at=r[8],
            tags=list(r[9] or []),
            tag_state=tag_state(r[10], r[9], r[11]),
        )
        for r in recent
    ]
    return summary


@dataclass(frozen=True)
class BoardActivity:
    """A competitor's job-board inputs to the threat level (app.intel.profile) and the
    overview's open roles."""

    has_board: bool
    open_now: int  # open on the as-of day (`as_of`), on its fresh boards
    open_then: int  # open on as-of - DELTA_DAYS, by the same rule (delta_30d = now - then)
    # Its current boards' first complete listing: the board's history starts there. None
    # until one: its open roles are not measured, readers serve them as null, never 0.
    first_listed_at: datetime | None = None
    # its fresh boards' last complete listing (the oldest of them), what the counts are
    # as of; None when no listed board is fresh (stale). And its newest complete listing.
    as_of: datetime | None = None
    last_listed_at: datetime | None = None
    board_checked_at: datetime | None = None  # discovery's last check, found none

    @property
    def fetched(self) -> bool:
        """An enabled board has been listed in full (a first snapshot)."""
        return self.has_board and self.first_listed_at is not None

    @property
    def stale(self) -> bool:
        """Listed, but no board was listed in full for `board_stale_days`: no current
        numbers; the threat and the brief read it as no board."""
        return self.fetched and self.as_of is None

    @property
    def measured(self) -> bool:
        """A fresh board: its open roles are current."""
        return self.fetched and not self.stale

    @property
    def board_since(self) -> date | None:
        return board_since(self.first_listed_at)

    @property
    def as_of_day(self) -> date | None:
        return local_day(self.as_of) if self.as_of is not None else None

    @property
    def last_listed_day(self) -> date | None:
        return local_day(self.last_listed_at) if self.last_listed_at is not None else None


async def board_activity(
    session: AsyncSession, competitor_ids: Sequence[int], *, now: datetime
) -> dict[int, BoardActivity]:
    """`hiring_summary`'s board flags, open_now and delta_30d for many competitors in ONE
    statement, by the same rules (current boards only, fresh boards counted on each one's
    as-of day, the open-on-day rule), plus each one's first and last complete listings. The
    self competitor and unknown ids are absent."""
    if not competitor_ids:
        return {}
    tz = get_settings().timezone
    params = {"ids": list(competitor_ids), "tz": tz, "fresh": fresh_since(now, tz)}
    as_of = f"(min({_LAST}) AT TIME ZONE CAST(:tz AS text))::date"
    rows = await session.execute(
        text(
            f"""
            WITH boards AS (
                SELECT sources.competitor_id, {as_of} AS day
                FROM sources
                WHERE sources.competitor_id = ANY(CAST(:ids AS int[]))
                  AND {_LISTED} AND {_FRESH}
                GROUP BY sources.competitor_id
            )
            SELECT c.id,
                   count(p.fs) FILTER (WHERE {_OPEN_ON.format(d="p.day")}),
                   count(p.fs) FILTER (WHERE {_OPEN_ON.format(d=f"p.day - {DELTA_DAYS}")}),
                   {_BOARD_FLAGS}
            FROM competitors c
            LEFT JOIN (
                SELECT jp.competitor_id, boards.day,
                       (jp.first_seen_at AT TIME ZONE CAST(:tz AS text))::date AS fs,
                       (jp.closed_at AT TIME ZONE CAST(:tz AS text))::date AS cl
                FROM job_postings jp {_ON_FRESH}
                JOIN boards ON boards.competitor_id = jp.competitor_id
                WHERE jp.competitor_id = ANY(CAST(:ids AS int[]))
            ) p ON p.competitor_id = c.id
            WHERE c.id = ANY(CAST(:ids AS int[])) AND NOT c.is_self
            GROUP BY c.id
            """
        ),
        params,
    )
    return {
        cid: BoardActivity(
            bool(board),
            int(now_n),
            int(then_n),
            listed,
            as_of=fresh_at,
            last_listed_at=last_at,
            board_checked_at=checked_at(checked),
        )
        for cid, now_n, then_n, board, listed, last_at, fresh_at, checked in rows.all()
    }
