from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy.exc import IntegrityError

from app.agents.analysis_schema import CitedText, SignalAnalysisContent
from app.agents.analysis_store import (
    analysis_content,
    latest_analyses,
    needs_analysis,
    persist_analysis,
    stored_analyses,
)
from app.models.gold import SignalAnalysis


def content(text="s", *, what_happened=True):
    return SignalAnalysisContent(
        summary=CitedText(text=text, cites=["doc:1"]),
        what_happened=[CitedText(text="w", cites=["doc:1"])] if what_happened else [],
    )


async def persist(session, signal_id, text="s", cost=0.0, *, what_happened=True):
    return await persist_analysis(
        session,
        signal_id,
        content(text, what_happened=what_happened),
        model="m",
        prompt_version="1",
        cost_usd=cost,
    )


async def test_persist_versions_increment(session, make_signal):
    sig = await make_signal(status="auto")
    assert await persist(session, sig.id, "a", 0.01) == 1
    assert await persist(session, sig.id, "b", 0.02) == 2
    latest = (await latest_analyses(session, [sig.id]))[sig.id]
    assert latest.version == 2
    assert latest.content["summary"]["text"] == "b"
    assert float(latest.cost_usd) == pytest.approx(0.02)

    # a concurrent writer that computed the same version loses on the unique constraint
    nested = await session.begin_nested()
    session.add(
        SignalAnalysis(signal_id=sig.id, version=2, content={}, model="m", prompt_version="1")
    )
    with pytest.raises(IntegrityError):
        await session.flush()
    await nested.rollback()
    assert (await latest_analyses(session, [sig.id]))[sig.id].version == 2
    assert await persist(session, sig.id, "c") == 3


async def test_latest_analyses_per_signal(session, make_signal):
    a = await make_signal(status="auto")
    b = await make_signal(status="auto")
    c = await make_signal(status="auto")
    await persist(session, a.id)
    await persist(session, a.id)
    await persist(session, b.id)
    got = await latest_analyses(session, {a.id, b.id, c.id})
    assert {k: v.version for k, v in got.items()} == {a.id: 2, b.id: 1}
    assert await latest_analyses(session, []) == {}


async def test_needs_analysis_tracks_stage_a(session, make_signal):
    sig = await make_signal(status="auto")
    assert await needs_analysis(session, [sig.id]) == {sig.id}
    await persist(session, sig.id)
    assert await needs_analysis(session, [sig.id]) == set()
    sig.analyzed_at = datetime.now(UTC) + timedelta(minutes=5)
    await session.flush()
    assert await needs_analysis(session, [sig.id]) == {sig.id}
    assert await needs_analysis(session, []) == set()


def _row(raw: dict) -> SignalAnalysis:
    return SignalAnalysis(signal_id=1, version=1, content=raw, model="m", prompt_version="1")


def test_analysis_content_is_the_one_safe_parse():
    """One parse shared by every reader. A row that does not parse is no
    analysis; neither is one without a summary or a what_happened item."""
    assert analysis_content(None) is None
    assert analysis_content(_row({"jfrog_impact": [{"effect": "huge"}]})) is None  # bad enum
    assert analysis_content(_row({"what_happened": [{"text": "w"}]})) is None  # no summary
    gutted = {"summary": {"text": "Only the summary survived.", "cites": ["doc:1"]}}
    assert analysis_content(_row(gutted)) is None
    blank = {**gutted, "what_happened": [{"text": "  ", "cites": ["doc:1"]}]}
    assert analysis_content(_row(blank)) is None
    full = analysis_content(_row(content().model_dump(mode="json")))
    assert full is not None and full.summary.text == "s"


async def test_summary_only_rows_are_not_current(session, make_signal):
    """The shape seen on live runs: a stored analysis whose what_happened was
    emptied by an incomplete fact-check is picked again by the next stage-B run, and every
    reader treats it as no analysis."""
    gutted = await make_signal(status="auto", analyzed_at=datetime.now(UTC) - timedelta(days=1))
    good = await make_signal(status="auto", analyzed_at=datetime.now(UTC) - timedelta(days=1))
    await persist(session, gutted.id, what_happened=False)
    await persist(session, good.id)

    assert await needs_analysis(session, [gutted.id, good.id]) == {gutted.id}
    stored = await stored_analyses(
        session, {gutted.id: gutted.analyzed_at, good.id: good.analyzed_at}
    )
    assert set(stored) == {good.id} and stored[good.id].current
    assert stored[good.id].content.what_happened[0].text == "w"


async def test_stored_analyses_say_current_or_stale(session, make_signal):
    sig = await make_signal(status="auto", analyzed_at=datetime.now(UTC) - timedelta(days=1))
    await persist(session, sig.id)
    later = datetime.now(UTC) + timedelta(minutes=5)
    assert (await stored_analyses(session, {sig.id: sig.analyzed_at}))[sig.id].current
    assert not (await stored_analyses(session, {sig.id: later}))[sig.id].current
    assert (await stored_analyses(session, {sig.id: None}))[sig.id].current
    assert await stored_analyses(session, {}) == {}
