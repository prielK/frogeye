"""Hiring aggregates over job_postings: the open-roles series and the summary."""

from datetime import UTC, date, datetime, timedelta

import pytest

from app.collectors.jobs.taxonomy import DEPARTMENTS, SENIORITIES
from app.intel.hiring import (
    BoardActivity,
    SeriesPoint,
    TagCoverage,
    board_activity,
    board_since,
    has_history,
    hiring_summary,
    open_roles_breakdowns,
    open_roles_series,
    tag_state,
)
from app.models import JobPosting

NOW = datetime(2026, 10, 20, 9, 0, tzinfo=UTC)  # 12:00 in Asia/Jerusalem (UTC+3)
TODAY = date(2026, 10, 20)


def utc(day: int, hour: int = 8, month: int = 10) -> datetime:
    return datetime(2026, month, day, hour, 0, tzinfo=UTC)


LISTED = datetime(2026, 8, 1, 6, 0, tzinfo=UTC)  # a board listed in full long ago


async def comp_with_board(
    session, make_source, competitor_factory, *, listed: datetime | None = LISTED, **fields
):
    """A competitor and its enabled board, first listed in full at `listed` (None: never)."""
    comp = competitor_factory(**fields)
    session.add(comp)
    await session.flush()
    src = await make_source(
        kind="job_board",
        competitor_id=comp.id,
        config={"ats": "lever"},
        first_complete_fetch_at=listed,
        last_complete_fetch_at=NOW if listed else None,  # and again today
    )
    return comp, src


def make(comp, src, n, **kw) -> JobPosting:
    return JobPosting(
        source_id=src.id,
        competitor_id=comp.id,
        ats="lever",
        external_id=f"p-{n}",
        title=kw.pop("title", f"Role {n}"),
        department=kw.pop("department", "Engineering"),
        seniority=kw.pop("seniority", "Mid"),
        content_hash=f"h{n}",
        tags=kw.pop("tags", []),
        **kw,
    )


def by_key(points: list[SeriesPoint]) -> dict[str, dict[date, int]]:
    out: dict[str, dict[date, int]] = {}
    for p in points:
        out.setdefault(p.key, {})[p.day] = p.count
    return out


def run(series: dict[date, int], first: int, last: int, value: int = 0) -> dict[int, int]:
    return {d: series.get(date(2026, 10, d), value) for d in range(first, last + 1)}


async def hand_built(session, make_source, competitor_factory):
    comp, src = await comp_with_board(session, make_source, competitor_factory)
    session.add_all(
        [
            make(comp, src, 1, first_seen_at=utc(11), tags=["sca"], tagged_at=utc(11)),
            make(
                comp,
                src,
                2,
                department="Sales",
                first_seen_at=utc(12),
                closed_at=utc(15),
                tags=["sca", "sbom"],
                tagged_at=utc(12),
            ),
            # 22:30 UTC on the 14th is 01:30 on the 15th in Jerusalem: it counts from the 15th
            make(comp, src, 3, first_seen_at=utc(14, 22) + timedelta(minutes=30)),
            make(
                comp,
                src,
                4,
                department="Security",
                first_seen_at=utc(5),
                closed_at=utc(13, 10),
                tags=["sbom"],
                tagged_at=utc(6),
            ),
            make(comp, src, 5, department="Sales", first_seen_at=utc(20, 5)),
        ]
    )
    await session.flush()
    return comp


async def test_series_counts_open_on_each_day(session, make_source, competitor_factory):
    comp = await hand_built(session, make_source, competitor_factory)
    args = {"days": 10, "now": NOW}

    total = by_key(await open_roles_series(session, [comp.id], by="total", **args))
    assert set(total) == {"total"}
    assert list(total["total"]) == [date(2026, 10, d) for d in range(11, 21)]  # 10 days, to today
    assert run(total["total"], 11, 20) == {
        11: 2, 12: 3, 13: 2, 14: 2, 15: 2, 16: 2, 17: 2, 18: 2, 19: 2, 20: 3,
    }  # fmt: skip

    dept = by_key(await open_roles_series(session, [comp.id], by="department", **args))
    assert run(dept["Engineering"], 11, 20) == dict(
        zip(range(11, 21), [1] * 4 + [2] * 6, strict=True)
    )
    assert run(dept["Sales"], 11, 20) == {d: int(d in (12, 13, 14, 20)) for d in range(11, 21)}
    assert run(dept["Security"], 11, 20) == {d: int(d in (11, 12)) for d in range(11, 21)}

    tags = by_key(await open_roles_series(session, [comp.id], by="tag", **args))
    assert set(tags) == {"sca", "sbom"}  # a posting counts once per tag; untagged ones are absent
    assert run(tags["sca"], 11, 20) == {11: 1, 12: 2, 13: 2, 14: 2, **{d: 1 for d in range(15, 21)}}
    assert run(tags["sbom"], 11, 20) == {
        11: 1,
        12: 2,
        13: 1,
        14: 1,
        **{d: 0 for d in range(15, 21)},
    }

    seniority = by_key(await open_roles_series(session, [comp.id], by="seniority", **args))
    assert set(seniority) == {"Mid"} and seniority["Mid"] == total["total"]


async def test_series_location_key_and_top_eight(session, make_source, competitor_factory):
    comp, src = await comp_with_board(session, make_source, competitor_factory)
    rows = []
    for i in range(11):  # 11 countries; country i has i+1 postings (the latest day's rank)
        for j in range(i + 1):
            rows.append(make(comp, src, i * 100 + j, country=f"Land{i:02d}", first_seen_at=utc(18)))
    rows.append(make(comp, src, 9001, remote=True, first_seen_at=utc(18)))  # no country
    rows.append(make(comp, src, 9002, first_seen_at=utc(18)))  # no country, not remote
    session.add_all(rows)
    await session.flush()
    loc = by_key(await open_roles_series(session, [comp.id], days=4, by="location", now=NOW))
    assert len(loc) == 9 and "Other" in loc
    assert {k for k in loc if k != "Other"} == {f"Land{i:02d}" for i in range(3, 11)}
    # Other = the three smallest countries + Remote + Unknown (1+2+3 + 1 + 1), from the 18th on
    assert run(loc["Other"], 17, 20) == {17: 0, 18: 8, 19: 8, 20: 8}
    assert loc["Land10"][date(2026, 10, 17)] == 0 and loc["Land10"][date(2026, 10, 18)] == 11

    small = by_key(await open_roles_series(session, [comp.id], days=2, by="total", now=NOW))
    assert small["total"] == {date(2026, 10, 19): 68, date(2026, 10, 20): 68}


async def test_series_sums_competitors_and_skips_self(session, make_source, competitor_factory):
    a, sa = await comp_with_board(session, make_source, competitor_factory)
    b, sb = await comp_with_board(session, make_source, competitor_factory)
    me, sm = await comp_with_board(
        session, make_source, competitor_factory, is_self=True, tier=0, relationship="self"
    )
    session.add_all(
        [
            make(a, sa, 1, first_seen_at=utc(19)),
            make(b, sb, 2, first_seen_at=utc(20, 1)),
            make(me, sm, 3, first_seen_at=utc(19)),
        ]
    )
    await session.flush()
    both = by_key(
        await open_roles_series(session, [a.id, b.id, me.id], days=2, by="total", now=NOW)
    )
    assert both["total"] == {date(2026, 10, 19): 1, date(2026, 10, 20): 2}
    assert await open_roles_series(session, [], days=2, by="total", now=NOW) == []


def open_on(p: JobPosting, day: date) -> bool:
    return p.first_seen_at.date() <= day and (p.closed_at is None or p.closed_at.date() > day)


async def test_hiring_summary_delta_and_top_n(
    session, make_source, competitor_factory, sql_counter
):
    """A 600-posting board, 13 countries, a bounded number of queries."""
    comp, src = await comp_with_board(session, make_source, competitor_factory)
    posts = []
    for i in range(600):
        first = datetime(2026, 10, 20, 6, 0, tzinfo=UTC) - timedelta(days=i % 60)
        closed = first + timedelta(days=1 + i % 10) if i % 7 == 0 else None
        if closed is not None and closed > NOW:
            closed = None
        posts.append(
            make(
                comp,
                src,
                i,
                department=DEPARTMENTS[i % len(DEPARTMENTS)],
                seniority=SENIORITIES[i % len(SENIORITIES)],
                country=f"Land{i % 13:02d}",
                location=f"City {i}",
                tags=[[], ["sca"], ["sca", "sbom"], ["sbom"]][i % 4],
                first_seen_at=first,
                closed_at=closed,
                url=f"https://jobs.example.test/{i}",
            )
        )
    session.add_all(posts)
    await session.flush()
    then = TODAY - timedelta(days=30)

    sql_counter.clear()
    s = await hiring_summary(session, comp.id, now=NOW)
    assert len(sql_counter) <= 6, sql_counter

    open_now = [p for p in posts if open_on(p, TODAY)]
    open_then = [p for p in posts if open_on(p, then)]
    assert s.has_board is True
    assert s.open_now == len(open_now) == sum(1 for p in posts if p.closed_at is None)
    assert s.delta_30d == len(open_now) - len(open_then) != 0

    assert len(s.by_department) == 12  # every department present
    assert dict(s.by_department) == {
        d: sum(1 for p in open_now if p.department == d) for d in DEPARTMENTS
    }
    assert [c for _, c in s.by_department] == sorted((c for _, c in s.by_department), reverse=True)
    assert {k for k, _ in s.by_seniority} == set(SENIORITIES)

    assert len(s.by_location) == 9 and s.by_location[-1][0] == "Other"  # top 8 + Other
    top = [c for _, c in s.by_location[:-1]]
    assert top == sorted(top, reverse=True)
    counts = {
        f"Land{n:02d}": sum(1 for p in open_now if p.country == f"Land{n:02d}") for n in range(13)
    }
    best = sorted(counts.values(), reverse=True)
    assert top == best[:8] and s.by_location[-1][1] == sum(best[8:])
    assert sum(c for _, c in s.by_location) == s.open_now

    tags = {slug: (n, d) for slug, n, d in s.tags}
    for slug in ("sca", "sbom"):
        now_n = sum(1 for p in open_now if slug in p.tags)
        then_n = sum(1 for p in open_then if slug in p.tags)
        assert tags[slug] == (now_n, now_n - then_n)

    assert len(s.recent) == 20
    expected = sorted(open_now, key=lambda p: (p.first_seen_at, p.id), reverse=True)[:20]
    assert [r.id for r in s.recent] == [p.id for p in expected]
    row = s.recent[0]
    assert (row.title, row.url, row.remote) == (expected[0].title, expected[0].url, False)
    assert row.first_seen_at == expected[0].first_seen_at and row.tags == expected[0].tags


async def test_has_board_rules(session, make_source, competitor_factory):
    # no source at all
    bare = competitor_factory()
    session.add(bare)
    # a disabled source (e.g. the discovery negative-cache placeholder) is not a board
    off = competitor_factory()
    session.add(off)
    await session.flush()
    await make_source(
        kind="job_board", competitor_id=off.id, enabled=False, config={"missing": True}
    )
    other_kind = competitor_factory()
    session.add(other_kind)
    await session.flush()
    await make_source(kind="rss", competitor_id=other_kind.id)
    on, _ = await comp_with_board(session, make_source, competitor_factory)
    me, _ = await comp_with_board(
        session, make_source, competitor_factory, is_self=True, tier=0, relationship="self"
    )
    flags = {
        c.id: (await hiring_summary(session, c.id, now=NOW)).has_board
        for c in (bare, off, other_kind, on, me)
    }
    assert flags == {bare.id: False, off.id: False, other_kind.id: False, on.id: True, me.id: False}
    empty = await hiring_summary(session, bare.id, now=NOW)
    # no posting ever seen is no board history, so no 30-day change either
    assert (empty.open_now, empty.delta_30d, empty.recent, empty.tags) == (0, None, [], [])
    assert empty.board_since is None


async def test_summary_ignores_closed_postings_in_breakdowns(
    session, make_source, competitor_factory
):
    comp, src = await comp_with_board(session, make_source, competitor_factory)
    session.add_all(
        [
            make(comp, src, 1, first_seen_at=utc(1, month=9), tags=["sca"]),
            make(comp, src, 2, first_seen_at=utc(1, month=9), closed_at=utc(10), tags=["sca"]),
        ]
    )
    await session.flush()
    s = await hiring_summary(session, comp.id, now=NOW)
    assert s.open_now == 1 and s.by_department == [("Engineering", 1)]
    assert s.delta_30d == -1  # two were open 30 days ago, one is now
    assert s.tags == [("sca", 1, -1)]
    assert len(s.recent) == 1


async def test_board_activity_batch_agrees_with_summary(
    session, make_source, competitor_factory, sql_counter
):
    """The threat's hiring inputs for many competitors in ONE
    statement, by the same open-on-day rule as hiring_summary (self absent)."""
    built = await hand_built(session, make_source, competitor_factory)
    shrinking, src = await comp_with_board(session, make_source, competitor_factory)
    session.add_all(
        [
            make(shrinking, src, 1, first_seen_at=utc(1, month=9)),
            make(shrinking, src, 2, first_seen_at=utc(1, month=9), closed_at=utc(10)),
        ]
    )
    bare = competitor_factory()
    session.add(bare)
    me, _ = await comp_with_board(
        session, make_source, competitor_factory, is_self=True, tier=0, relationship="self"
    )
    await session.flush()
    ids = [built.id, shrinking.id, bare.id, me.id]

    sql_counter.clear()
    got = await board_activity(session, ids, now=NOW)
    assert len(sql_counter) == 1
    assert set(got) == {built.id, shrinking.id, bare.id}
    for cid in (built.id, shrinking.id, bare.id):
        s = await hiring_summary(session, cid, now=NOW)
        a = got[cid]
        # the summary's change is the batch's under the same history rule
        since = a.board_since
        change = a.open_now - a.open_then if has_history(since, TODAY) else None
        assert (a.has_board, a.open_now, change, since) == (
            s.has_board,
            s.open_now,
            s.delta_30d,
            s.board_since,
        )
    # the board's history starts at its first complete listing, not its postings
    assert got[shrinking.id].first_listed_at == LISTED == got[built.id].first_listed_at
    assert got[bare.id] == BoardActivity(False, 0, 0, None)
    assert await board_activity(session, [], now=NOW) == {}


# ---- several breakdowns in one statement (the deep dive's hiring series) ----------


async def test_breakdowns_equal_the_single_series_in_one_statement(
    session, make_source, competitor_factory, sql_counter
):
    """The deep dive reads its total, department, seniority, location and tag
    series in ONE statement, by the same rule and folding as `open_roles_series`."""
    comp = await hand_built(session, make_source, competitor_factory)
    bys = ("total", "department", "seniority", "location", "tag")
    sql_counter.clear()
    every = await open_roles_breakdowns(session, [comp.id], days=10, by=bys, now=NOW)
    assert len(sql_counter) == 1
    assert set(every) == set(bys)
    for by in bys:
        assert every[by] == await open_roles_series(session, [comp.id], days=10, by=by, now=NOW)
    assert await open_roles_breakdowns(session, [], days=10, by=bys, now=NOW) == {
        by: [] for by in bys
    }


async def test_breakdowns_by_competitor_with_a_tag_filter(session, make_source, competitor_factory):
    """A product area: postings carrying any of the given tags, each counted once, keyed by
    competitor slug (top 8 by the latest day, the rest folded into "Other"); self skipped."""
    comps = []
    for n in range(10):  # competitor n has n+1 matching postings
        comp, src = await comp_with_board(
            session, make_source, competitor_factory, slug=f"area-{n:02d}"
        )
        comps.append(comp)
        session.add_all(
            make(comp, src, n * 100 + j, first_seen_at=utc(19), tags=["sca", "sbom"])
            for j in range(n + 1)
        )
        session.add(make(comp, src, n * 100 + 99, first_seen_at=utc(19), tags=["sast"]))
        session.add(make(comp, src, n * 100 + 98, first_seen_at=utc(19)))  # untagged
    me, sm = await comp_with_board(
        session, make_source, competitor_factory, is_self=True, tier=0, relationship="self"
    )
    session.add(make(me, sm, 1, first_seen_at=utc(19), tags=["sca"]))
    await session.flush()

    ids = [c.id for c in comps] + [me.id]
    got = await open_roles_breakdowns(
        session, ids, days=2, by=("total", "competitor"), tags=["sca", "sbom"], now=NOW
    )
    total = by_key(got["total"])
    assert total["total"] == {date(2026, 10, 19): 55, date(2026, 10, 20): 55}  # 1+2+...+10
    area = by_key(got["competitor"])
    assert set(area) == {f"area-{n:02d}" for n in range(2, 10)} | {"Other"}
    assert area["Other"][date(2026, 10, 20)] == 3  # area-00 (1) + area-01 (2)
    assert area["area-09"][date(2026, 10, 20)] == 10
    none = await open_roles_breakdowns(
        session, ids, days=2, by=("total",), tags=["model-registry"], now=NOW
    )
    assert by_key(none["total"]) == {"total": {date(2026, 10, 19): 0, date(2026, 10, 20): 0}}


async def test_board_activity_says_whether_a_board_ever_completed_a_fetch(
    session, make_source, competitor_factory
):
    """A board found and enabled but never fetched has no measured open roles yet;
    nor has one whose fetches only ever listed part of it."""
    fresh, _ = await comp_with_board(session, make_source, competitor_factory, listed=None)
    partial, src = await comp_with_board(session, make_source, competitor_factory, listed=None)
    src.last_fetched_at = NOW  # a successful fetch, but a partial listing
    done, _ = await comp_with_board(session, make_source, competitor_factory, listed=NOW)
    off = competitor_factory()
    session.add(off)
    await session.flush()
    await make_source(
        kind="job_board",
        competitor_id=off.id,
        enabled=False,
        last_fetched_at=NOW,
        first_complete_fetch_at=NOW,
    )  # a disabled board is no board, listed or not
    await session.flush()
    got = await board_activity(session, [fresh.id, partial.id, done.id, off.id], now=NOW)
    assert (got[fresh.id].has_board, got[fresh.id].fetched) == (True, False)
    assert (got[partial.id].has_board, got[partial.id].fetched) == (True, False)
    assert (got[done.id].has_board, got[done.id].fetched) == (True, True)
    assert (got[off.id].has_board, got[off.id].fetched) == (False, False)
    for comp, measured in ((fresh, False), (partial, False), (done, True), (off, False)):
        s = await hiring_summary(session, comp.id, now=NOW)
        assert (s.measured, s.board_since) == (measured, TODAY if measured else None), comp.id


# ---- a board under 30 days old --------------------------------------


async def test_a_board_under_30_days_old_has_no_30_day_change(
    session, make_source, competitor_factory
):
    """Its first snapshot is its first complete listing's day. Before 30 days of history
    there is nothing to compare with 30 days ago: no 30-day change, per tag either, and its
    series start at that first snapshot, never zero-filled before the board existed."""
    comp, src = await comp_with_board(
        session, make_source, competitor_factory, listed=utc(10, hour=6)
    )
    session.add_all(
        [
            make(comp, src, 1, first_seen_at=utc(10), tags=["sca"]),
            make(comp, src, 2, department="Sales", first_seen_at=utc(10), tags=["sca", "sbom"]),
            make(comp, src, 3, first_seen_at=utc(15)),
        ]
    )
    await session.flush()

    s = await hiring_summary(session, comp.id, now=NOW)
    assert s.board_since == date(2026, 10, 10)
    assert s.open_now == 3
    assert s.delta_30d is None  # never "+3 in 30 days" for a board read 10 days
    assert s.tags == [("sca", 2, None), ("sbom", 1, None)]

    series = await open_roles_breakdowns(
        session, [comp.id], days=90, by=("total", "department"), now=NOW, since=s.board_since
    )
    total = series["total"]
    assert [p.day for p in total] == [date(2026, 10, 10) + timedelta(days=i) for i in range(11)]
    assert [p.count for p in total] == [2] * 5 + [3] * 6  # no zero day before the board
    assert min(p.day for p in series["department"]) == date(2026, 10, 10)
    # a later window start than the board's first day keeps the window
    late = await open_roles_breakdowns(
        session, [comp.id], days=3, by=("total",), now=NOW, since=s.board_since
    )
    assert [p.day for p in late["total"]] == [date(2026, 10, 18), date(2026, 10, 19), TODAY]


async def test_a_partial_first_listing_is_no_first_snapshot(
    session, make_source, competitor_factory
):
    """A first fetch that listed part of the board stores its postings but measures
    nothing; the board's history starts at its first complete listing, the next day, and the
    partial day's undercount is never a measured day of the series."""
    comp, src = await comp_with_board(session, make_source, competitor_factory, listed=None)
    session.add(make(comp, src, 1, first_seen_at=utc(9)))  # the partial listing, 9 Oct
    await session.flush()
    pending = await hiring_summary(session, comp.id, now=NOW)
    assert (pending.has_board, pending.measured, pending.board_since) == (True, False, None)

    src.first_complete_fetch_at = utc(10)  # the whole board, 10 Oct
    src.last_complete_fetch_at = NOW  # and every day since
    session.add(make(comp, src, 2, first_seen_at=utc(10)))
    await session.flush()
    s = await hiring_summary(session, comp.id, now=NOW)
    assert (s.measured, s.board_since, s.open_now) == (True, date(2026, 10, 10), 2)
    activity = (await board_activity(session, [comp.id], now=NOW))[comp.id]
    assert (activity.fetched, activity.board_since) == (True, date(2026, 10, 10))
    series = await open_roles_breakdowns(
        session, [comp.id], days=90, by=("total",), now=NOW, since=s.board_since
    )
    assert series["total"][0] == SeriesPoint(date(2026, 10, 10), "total", 2)


async def test_a_replaced_board_is_history(session, make_source, competitor_factory):
    """Only a competitor's current (enabled) boards count. A board replaced by
    another is no longer read, so its postings never close: none of them is an open role,
    a posting, a series point or the board's history (board_since)."""
    comp, old = await comp_with_board(session, make_source, competitor_factory)
    old.enabled = False  # replaced: its postings stay open in the table, unread
    new = await make_source(
        kind="job_board",
        competitor_id=comp.id,
        config={"ats": "ashby"},
        first_complete_fetch_at=utc(15),
        last_complete_fetch_at=NOW,
    )
    session.add_all(
        [
            *(make(comp, old, n, first_seen_at=utc(1, month=8)) for n in range(1, 6)),
            make(comp, new, 6, first_seen_at=utc(15), title="Current role"),
            make(comp, new, 7, first_seen_at=utc(16), department="Sales"),
        ]
    )
    await session.flush()

    s = await hiring_summary(session, comp.id, now=NOW)
    assert (s.has_board, s.open_now, s.delta_30d, s.board_since) == (
        True,
        2,
        None,  # the current board is 5 days old: no 30-day change
        date(2026, 10, 15),
    )
    assert dict(s.by_department) == {"Engineering": 1, "Sales": 1}
    assert {r.title for r in s.recent} == {"Current role", "Role 7"}
    activity = (await board_activity(session, [comp.id], now=NOW))[comp.id]
    assert (activity.open_now, activity.open_then, activity.board_since) == (
        2,
        0,
        date(2026, 10, 15),
    )
    series = await open_roles_breakdowns(session, [comp.id], days=90, by=("total",), now=NOW)
    assert max(p.count for p in series["total"]) == 2  # the old board's five are never counted


def test_history_needs_30_days_from_the_first_snapshot():
    assert has_history(TODAY - timedelta(days=30), TODAY) is True
    assert has_history(TODAY - timedelta(days=29), TODAY) is False
    assert has_history(None, TODAY) is False
    assert board_since(None) is None
    # 22:30 UTC is 01:30 the next day in Jerusalem: the board's first local day
    assert board_since(utc(9, 22) + timedelta(minutes=30)) == date(2026, 10, 10)


async def test_area_breakdown_starts_each_competitor_at_its_board_since(
    session, make_source, competitor_factory
):
    """A product area's hiring: with `competitor_since` a competitor key's points start
    at its own board's first snapshot, never zero-filled over the days before it was read, and
    `fold=False` keeps every competitor, for the chart that folds on the client (the
    client's Other starts once all its members are read). A server-side fold has no per-member
    start, so asking for both is an error."""
    comps = []
    since: dict[str, date] = {}
    for n in range(10):  # competitor n: first read on 10 + n Oct, n + 1 area roles
        comp, src = await comp_with_board(
            session, make_source, competitor_factory, slug=f"area-{n:02d}"
        )
        comps.append(comp)
        since[comp.slug] = date(2026, 10, 10 + n)
        session.add_all(
            make(comp, src, n * 100 + j, first_seen_at=utc(10 + n), tags=["sca"])
            for j in range(n + 1)
        )
    await session.flush()
    ids = [c.id for c in comps]

    def firsts(points: list[SeriesPoint]) -> dict[str, date]:
        return {k: min(days) for k, days in by_key(points).items()}

    kw = {"days": 90, "now": NOW, "tags": ["sca"], "since": date(2026, 10, 10)}
    every = await open_roles_breakdowns(
        session, ids, by=("total", "competitor"), competitor_since=since, fold=False, **kw
    )
    assert firsts(every["total"]) == {"total": date(2026, 10, 10)}  # the earliest board
    assert firsts(every["competitor"]) == since  # each from its own board; no "Other"
    newest = by_key(every["competitor"])["area-09"]
    assert newest == {date(2026, 10, 19): 10, date(2026, 10, 20): 10}  # no zero before 19 Oct

    with pytest.raises(ValueError, match="fold=False"):
        await open_roles_breakdowns(session, ids, by=("competitor",), competitor_since=since, **kw)

    plain = await open_roles_breakdowns(session, ids, by=("competitor",), **kw)
    assert set(firsts(plain["competitor"]).values()) == {date(2026, 10, 10)}  # unchanged default


# ---- one "current board" rule ------------------------------


async def test_a_partly_listed_board_is_not_measured_beside_a_listed_one(
    session, make_source, competitor_factory
):
    """A board counts once it has been listed in full, each board on its own: a
    competitor's second board that has only ever been listed in part adds nothing to the
    measured numbers of its first, listed one."""
    comp, listed = await comp_with_board(session, make_source, competitor_factory)
    partial = await make_source(
        kind="job_board", competitor_id=comp.id, config={"ats": "ashby"}, last_fetched_at=NOW
    )  # fetched, never listed in full
    session.add_all(
        [
            make(comp, listed, 1, first_seen_at=utc(1, month=9)),
            make(comp, listed, 2, first_seen_at=utc(5)),
            *(make(comp, partial, n, first_seen_at=utc(10)) for n in (3, 4, 5)),
        ]
    )
    await session.flush()
    s = await hiring_summary(session, comp.id, now=NOW)
    assert (s.measured, s.open_now, s.board_since) == (True, 2, date(2026, 8, 1))
    assert len(s.recent) == 2
    activity = (await board_activity(session, [comp.id], now=NOW))[comp.id]
    assert (activity.fetched, activity.open_now) == (True, 2)
    series = await open_roles_breakdowns(session, [comp.id], days=3, by=("total",), now=NOW)
    assert [p.count for p in series["total"]] == [2, 2, 2]


def test_the_current_board_rule_is_written_once():
    """Hiring, the competitor postings and the product area read one definition
    (`app.intel.hiring.current_board` / `listed_board`); no reader spells it again."""
    from pathlib import Path

    import app

    root = Path(app.__file__).parent
    readers = ("api/v1/competitors.py", "api/v1/products.py", "api/competitor_profile.py")
    for name in readers:
        text = (root / name).read_text(encoding="utf-8")
        assert "first_complete_fetch_at" not in text, name
        assert "last_complete_fetch_at" not in text, name  # `fresh_board`, once
        assert "SourceKind.job_board" not in text, name


# ---- a board that stops being listed -----------------------------------


async def test_a_board_failing_for_days_after_a_complete_listing_serves_no_current_count(
    session, make_source, competitor_factory
):
    """A board listed in full on 15 Oct, then failing every day (an ATS switch: a 404,
    so no listing closes anything). Up to `board_stale_days` (3) days later its counts are
    served as of 15 Oct, never as today's; after that it is stale: no current count, the
    threat reads it as no board, and its series end on 15 Oct, never carried flat to today."""
    last = utc(15, hour=3)  # 06:00 in Jerusalem
    comp, src = await comp_with_board(session, make_source, competitor_factory)
    src.last_complete_fetch_at = last
    session.add_all(
        [
            make(comp, src, 1, first_seen_at=utc(1, month=9)),
            make(comp, src, 2, first_seen_at=utc(10)),
            # first seen on the 17th by a partial listing: never in a count as of the 15th
            make(comp, src, 3, first_seen_at=utc(17)),
        ]
    )
    await session.flush()

    three_days = datetime(2026, 10, 18, 9, tzinfo=UTC)
    s = await hiring_summary(session, comp.id, now=three_days)
    assert (s.measured, s.stale, s.as_of, s.open_now) == (True, False, last, 2)
    activity = (await board_activity(session, [comp.id], now=three_days))[comp.id]
    assert (activity.measured, activity.as_of_day, activity.open_now) == (
        True,
        date(2026, 10, 15),
        2,
    )

    s = await hiring_summary(session, comp.id, now=NOW)  # five days on: stale
    assert (s.listed, s.stale, s.measured, s.as_of) == (True, True, False, None)
    assert (s.open_now, s.recent, s.tags) == (0, [], [])
    assert s.last_listed_day == date(2026, 10, 15)
    activity = (await board_activity(session, [comp.id], now=NOW))[comp.id]
    assert (activity.fetched, activity.stale, activity.measured) == (True, True, False)
    assert activity.last_listed_day == date(2026, 10, 15)

    # current series count fresh boards only; a stale board's history ends at its last day
    current = await open_roles_breakdowns(session, [comp.id], days=10, by=("total",), now=NOW)
    assert [p.count for p in current["total"]] == [0] * 10
    history = await open_roles_breakdowns(
        session, [comp.id], days=10, by=("total",), now=NOW, until=s.last_listed_day, stale=True
    )
    assert history["total"][-1] == SeriesPoint(date(2026, 10, 15), "total", 2)
    assert len(history["total"]) == 10  # 6 Oct to 15 Oct: no day after its last listing


async def test_counts_are_as_of_the_last_complete_listing(session, make_source, competitor_factory):
    """Before the day's run the counts are yesterday's listing's: a posting first seen today
    by a partial listing is not in them, and the 30-day change compares the as-of day with
    30 days before it."""
    comp, src = await comp_with_board(session, make_source, competitor_factory)
    src.last_complete_fetch_at = utc(19, hour=3)
    session.add_all(
        [
            make(comp, src, 1, first_seen_at=utc(1, month=9)),
            make(comp, src, 2, first_seen_at=utc(18)),
            make(comp, src, 3, first_seen_at=utc(20, hour=4)),  # today, partial listing
        ]
    )
    await session.flush()
    s = await hiring_summary(session, comp.id, now=NOW)
    assert (s.as_of_day, s.open_now, s.delta_30d) == (date(2026, 10, 19), 2, 1)
    assert {r.title for r in s.recent} == {"Role 1", "Role 2"}
    activity = (await board_activity(session, [comp.id], now=NOW))[comp.id]
    assert (activity.open_now, activity.open_then) == (2, 1)


# ---- the untagged state ----------------------------------


async def test_every_open_posting_has_a_tagging_state_and_the_summary_counts_them(
    session, make_source, competitor_factory
):
    comp, src = await comp_with_board(session, make_source, competitor_factory)
    session.add_all(
        [
            make(comp, src, 1, first_seen_at=utc(5), tags=["sca"], tagged_at=utc(5)),
            make(comp, src, 2, first_seen_at=utc(6), tags=[], tagged_at=utc(6), tag_version="1"),
            make(comp, src, 3, first_seen_at=utc(7)),  # not tagged yet
            make(comp, src, 4, first_seen_at=utc(8), tag_failures=3, tag_error="LLMError"),
            make(comp, src, 5, first_seen_at=utc(9), tag_failures=2),  # still tried
            # closed: not an open posting, not in the coverage
            make(comp, src, 6, first_seen_at=utc(1), closed_at=utc(3), tags=["sca"]),
        ]
    )
    await session.flush()
    s = await hiring_summary(session, comp.id, now=NOW)
    assert s.coverage == TagCoverage(tagged=1, pending=2, untagged=1, gave_up=1)
    assert (s.coverage.total, s.coverage.partial) == (5, True)
    assert {r.title: r.tag_state for r in s.recent} == {
        "Role 1": "tagged",
        "Role 2": "untagged",
        "Role 3": "pending",
        "Role 4": "gave_up",
        "Role 5": "pending",
    }
    assert tag_state(None, [], 0) == "pending" and tag_state(None, [], 3) == "gave_up"
    assert tag_state(utc(5), [], 3) == "untagged" and tag_state(utc(5), ["sca"], 0) == "tagged"
