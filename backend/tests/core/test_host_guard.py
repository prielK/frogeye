from datetime import UTC, datetime, timedelta

from app.core.host_guard import DbHostGuard
from app.models import HostBlock


class _Clock:
    def __init__(self) -> None:
        self.now = datetime(2026, 10, 17, 12, 0, tzinfo=UTC)

    def __call__(self) -> datetime:
        return self.now


async def test_db_guard_round_trip(session_factory):
    clk = _Clock()
    g = DbHostGuard(session_factory, clk)
    assert await g.paused_until("a.test") is None
    await g.record("a.test", 403)
    assert await g.paused_until("a.test") == clk.now + timedelta(hours=23)
    # a fresh guard (new run) reads the persisted state
    g2 = DbHostGuard(session_factory, clk)
    assert await g2.paused_until("a.test") == clk.now + timedelta(hours=23)
    assert await g2.last_status("a.test") == 403
    clk.now += timedelta(hours=25)
    g3 = DbHostGuard(session_factory, clk)
    assert await g3.paused_until("a.test") is None
    await g3.record("a.test", 429)
    async with session_factory() as s:
        row = await s.get(HostBlock, "a.test")
        assert row.strikes == 2 and row.last_status == 429
        assert row.blocked_until == clk.now + timedelta(days=7)
    await g3.record("a.test", 200)
    async with session_factory() as s:
        assert await s.get(HostBlock, "a.test") is None  # reset deletes the row


async def test_db_guard_api_host_and_other_status_noop(session_factory):
    clk = _Clock()
    g = DbHostGuard(session_factory, clk)
    await g.record("hn.algolia.com", 429)
    assert await g.paused_until("hn.algolia.com") == clk.now + timedelta(hours=1)
    await g.record("b.test", 500)
    async with session_factory() as s:
        assert await s.get(HostBlock, "b.test") is None


async def test_host_blocks_cli_helpers(session, session_factory):
    from app.cli.host_blocks import clear_blocks, list_blocks

    g = DbHostGuard(session_factory)
    await g.record("a.test", 403)
    await g.record("b.test", 403)
    assert [r.host for r in await list_blocks(session)] == ["a.test", "b.test"]
    assert await clear_blocks(session, "a.test") == 1
    assert [r.host for r in await list_blocks(session)] == ["b.test"]
    assert await clear_blocks(session, None) == 1
    assert await list_blocks(session) == []


async def test_db_guard_writes_only_real_changes(session_factory, session):
    from app.cli.host_blocks import list_blocks

    g = DbHostGuard(session_factory, _Clock())
    await g.record("ok.test", 200)
    await g.record("ok.test", 304)
    await g.record("ok.test", 200)
    assert await list_blocks(session) == []  # healthy hosts never get a row
    await g.record("bad.test", 403)
    assert [r.host for r in await list_blocks(session)] == ["bad.test"]
    async with session_factory() as s:
        before = (await s.get(HostBlock, "bad.test")).updated_at
    await g.record("bad.test", 403)  # already paused: no escalation, no write
    async with session_factory() as s:
        assert (await s.get(HostBlock, "bad.test")).updated_at == before
    await g.record("bad.test", 500)  # other statuses: no change
    g.__dict__["_cache"]["bad.test"].blocked_until = None  # pause elapsed
    await g.record("bad.test", 200)  # success clears the struck host
    assert await list_blocks(session) == []


async def test_db_guard_first_insert_race_does_not_fail(session_factory):
    clk = _Clock()
    a, b = DbHostGuard(session_factory, clk), DbHostGuard(session_factory, clk)
    await b.paused_until("race.test")  # b caches "no row" ...
    await a.record("race.test", 403)  # ... then a inserts it
    await b.record("race.test", 403)  # b's insert hits the existing row: must upsert
    async with session_factory() as s:
        row = await s.get(HostBlock, "race.test")
        assert row.strikes == 1 and row.last_status == 403
