"""The shared plumbing of FrogEye's verified LLM writers (stage B, the weekly briefs) and the
briefing reserve every budgeted batch keeps: one copy of each."""

import re
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.agents import verified_llm as v
from app.agents.briefing_schema import make_fact_check_model
from app.agents.graphs import daily_intel
from app.agents.nodes import signal_analysis
from app.agents.runtime.context import RunCtx
from app.agents.runtime.prompts import load_prompt
from app.intel import briefs, facts
from app.models.agents import LlmCall

CTX = RunCtx(agent_run_id=None, node="t", agent="t")
ECHO = load_prompt(
    "echo", directory=Path(__file__).resolve().parent.parent / "fixtures" / "prompts"
)


def test_ref_cleaner_keeps_each_vocabulary():
    """Inline markers go, a bare ref used as a word reads "the source"; each writer
    names its own ref kinds."""
    stage_b = v.ref_cleaner(("doc", "kb", "signal"))
    brief = v.ref_cleaner(("signal", "fact", "posting", "page"))
    assert stage_b("Sonatype shipped it [doc:12] (see kb:3).") == "Sonatype shipped it."
    assert stage_b("As doc:4 reports, it ships.") == "As the source reports, it ships."
    assert stage_b("Raised [1][2] money .") == "Raised money."
    assert stage_b("fact:3 says so") == "fact:3 says so"  # not a stage-B ref
    assert brief("Acme raised $40M [fact:3].") == "Acme raised $40M."
    assert brief("posting:5 and posting:6 show it.") == "The sources show it."
    assert signal_analysis._clean("It ships [doc:1].") == "It ships."  # stage B uses it


def checker(*, first_call_answers: int | None):
    """A fake checker that supports every item id in the request (S1, S2, ...); on its first
    call it answers only the first `first_call_answers` of them."""
    calls: list[str] = []

    def _check(**req):
        calls.append(req["user"])
        ids = re.findall(r"S\d+", req["user"])
        if first_call_answers is not None and len(calls) == 1:
            ids = ids[:first_call_answers]
        return {
            "verdicts": [
                {"sentence_id": i, "kind": "claim", "supported": True, "reason": "supported"}
                for i in ids
            ]
        }

    return _check, calls


def request_for(seen: list[list[int]], topic: str = "t"):
    def _request(pending: list[int]):
        seen.append(list(pending))
        ids = " ".join(f"S{n}" for n in range(1, len(pending) + 1))
        return make_fact_check_model(len(pending)), {"topic": topic, "doc": ids}

    return _request


async def test_fact_check_re_asks_the_missing_items_once(accounted_llm):
    """The items a reply leaves out are asked again, alone and renumbered, once; still
    missing then raises IncompleteFactCheck. Every request's key is kept (the cost)."""
    check, calls = checker(first_call_answers=1)
    llm = accounted_llm({"t_check": check})
    keys: list[str] = []
    seen: list[list[int]] = []
    found = await v.fact_check(
        llm, 3, request_for(seen), task="t_check", prompt=ECHO, keys=keys, ctx=CTX
    )
    assert sorted(found) == [0, 1, 2] and seen == [[0, 1, 2], [1, 2]]
    assert len(keys) == 2 and len(calls) == 2

    # never answers (another topic: the first scenario's replies sit in the LLM-call cache)
    llm = accounted_llm({"t_check": lambda **req: {"verdicts": []}})
    with pytest.raises(v.IncompleteFactCheck, match="no verdict for 2 of 2 items after a re-ask"):
        await v.fact_check(
            llm, 2, request_for([], topic="u"), task="t_check", prompt=ECHO, keys=[], ctx=CTX
        )


async def test_fact_check_runs_on_the_tier_it_is_given(accounted_llm, settings):
    check, _ = checker(first_call_answers=3)
    llm = accounted_llm({"t_check": check})
    await v.fact_check(
        llm,
        3,
        request_for([], topic="w"),
        task="t_check",
        prompt=ECHO,
        keys=[],
        ctx=CTX,
        tier="smart",
    )
    assert llm.backend.requests[0]["model"] == settings.model_smart


def test_fact_check_model_can_put_the_reason_first():
    def fields(model):
        verdict = next(iter(model.model_json_schema()["$defs"].values()))
        return list(verdict["properties"])

    assert fields(make_fact_check_model(2)) == ["sentence_id", "kind", "supported", "reason"]
    reason_first = make_fact_check_model(2, reason_first=True)
    assert fields(reason_first) == ["sentence_id", "reason", "kind", "supported"]


def test_unsupported_fails_closed():
    """Kept only when every verdict passes; advice passes only in an action."""
    items = [
        SimpleNamespace(section="bottom_line", index=0, advice_ok=False),
        SimpleNamespace(section="bottom_line", index=1, advice_ok=False),
        SimpleNamespace(section="actions.sales", index=0, advice_ok=True),
        SimpleNamespace(section="watch", index=0, advice_ok=False),
        SimpleNamespace(section="summary", index=None, advice_ok=False),
    ]

    def verdict(supported, kind="claim"):
        return SimpleNamespace(supported=supported, kind=kind)

    got = v.unsupported(
        items,
        {
            0: [verdict(True)],
            1: [verdict(False, "advice")],  # advice, but not an action
            2: [verdict(False, "advice")],  # advice in an action passes
            4: [verdict(True), verdict(False)],  # one failing duplicate verdict sinks it
        },  # 3 has no verdict at all
    )
    assert got == {("bottom_line", 1), ("watch", 0), ("summary", None)}
    assert v.passes(verdict(False, "advice"), advice_ok=True)
    assert not v.passes(verdict(False, "advice"), advice_ok=False)


async def test_ledger_cost_and_the_reserve(session, session_factory, accounted_llm, settings):
    def booked(cost: str, rhash: str) -> LlmCall:
        return LlmCall(
            task="t",
            model="m",
            request_hash=rhash,
            cost_usd=Decimal(cost),
            latency_ms=1,
            status="ok",
        )

    session.add(booked("0.5", "old"))
    await session.commit()
    floor = await v.ledger_floor(session_factory)
    session.add_all([booked("0.25", "mine"), booked("0.125", "theirs"), booked("0.0625", "mine")])
    await session.commit()
    assert await v.calls_cost(session_factory, floor, ["mine"]) == pytest.approx(0.3125)
    assert await v.calls_cost(session_factory, floor, []) == 0.0

    llm = accounted_llm({})
    spent = await llm.spent_today()
    roomy = settings.model_copy(
        update={"llm_daily_budget_usd": spent + 1, "llm_total_budget_usd": 1e6}
    )
    tight = roomy.model_copy(update={"llm_daily_budget_usd": spent + 0.01})
    assert not await v.reserve_reached(llm, roomy)
    assert await v.reserve_reached(llm, tight)  # 0.01 left < the 0.05 reserve


def test_one_copy_of_each_helper():
    """The copies are gone: every writer and batch uses the shared helpers."""
    copies = {
        signal_analysis: (
            "_ledger_floor",
            "_calls_cost",
            "_IncompleteFactCheck",
            "_passes",
            "_unsupported",
            "_MARKER",
            "_source_noun",
        ),
        briefs: (
            "_ledger_floor",
            "_calls_cost",
            "_reserve_reached",
            "_IncompleteFactCheck",
            "_unsupported",
            "_MARKER",
            "_source_noun",
        ),
        facts: ("_reserve_reached",),
        daily_intel: ("_reserve_reached",),
    }
    for module, names in copies.items():
        assert [n for n in names if hasattr(module, n)] == [], module.__name__


# ---- one copy each -----------------------------------------------


async def test_the_re_ask_loop_asks_the_left_out_items_once_more(accounted_llm):
    """One loop for stage B, the briefs and the newsroom. `ask(pending)`
    makes one call; the items a reply leaves out are asked again, alone and renumbered,
    once; the loop returns the verdicts found and the items still without one (each caller
    then fails closed in its own way)."""
    asked: list[list[int]] = []

    async def ask(pending: list[int]):
        asked.append(list(pending))
        ids = list(range(1, len(pending) + 1))[:1]  # a reply judging S1 only, every time
        return [SimpleNamespace(sentence_id=f"S{i}", supported=True) for i in ids]

    found, missing = await v.ask_until_judged(3, ask)
    assert asked == [[0, 1, 2], [1, 2]]  # the re-ask is the left-out items, renumbered
    assert sorted(found) == [0, 1] and missing == [2]
    asked.clear()
    found, missing = await v.ask_until_judged(0, ask)  # as before: asked once even for none
    assert asked == [[]] and missing == []

    # `each`: the re-ask is one call per left-out item, each alone as S1
    asked.clear()
    found, missing = await v.ask_until_judged(3, ask, each=True)
    assert asked == [[0, 1, 2], [1], [2]]
    assert sorted(found) == [0, 1, 2] and missing == []


async def test_ledger_cost_since_a_floor(session, session_factory):
    """What the ledger booked after a floor, every request (a backfill's own spend)."""
    session.add(
        LlmCall(task="t", model="m", request_hash="a", cost_usd=Decimal("1"), latency_ms=1,
                status="ok")
    )  # fmt: skip
    await session.commit()
    floor = await v.ledger_floor(session_factory)
    session.add_all(
        [
            LlmCall(task="t", model="m", request_hash=h, cost_usd=Decimal(c), latency_ms=1,
                    status="ok")
            for h, c in (("b", "0.25"), ("c", "0.125"))
        ]
    )  # fmt: skip
    await session.commit()
    assert await v.ledger_cost_since(session_factory, floor) == pytest.approx(0.375)


def test_the_ledger_floor_and_the_re_ask_loop_exist_once():
    """The ledger-floor query (cli/_backfill.py, cli/analyze_signals.py) and
    the re-ask loop (stage B, the briefs, the newsroom) each have one copy, in
    app/agents/verified_llm.py, so a fix lands once."""
    root = Path(v.__file__).resolve().parents[1]
    sources = {p: p.read_text(encoding="utf-8") for p in root.rglob("*.py")}
    floors = sorted(p.name for p, text in sources.items() if "max(LlmCall.id)" in text)
    loops = sorted(p.name for p, text in sources.items() if "in await ask(" in text)
    assert floors == ["verified_llm.py"] and loops == ["verified_llm.py"]


@pytest.mark.parametrize(
    ("written", "stored"),
    [
        # a see-pointer is removed only where it ends a clause; inside a
        # sentence the ref is a word, so nothing else of the sentence goes with it
        ("See doc:4 for more.", "See the source for more."),
        ("It ships (see doc:4).", "It ships."),
        ("It ships, see doc:4.", "It ships."),  # a trailing pointer goes, with its comma
        # an Oxford-comma list of refs is one list
        ("doc:1, and doc:2 agree.", "The sources agree."),
        ("It ships [doc:1, and doc:2].", "It ships."),
        ("doc:1, doc:2, and kb:3 agree.", "The sources agree."),
        # an abbreviation's full stop does not open a sentence
        ("Prices rose, e.g. doc:3 shows it.", "Prices rose, e.g. the source shows it."),
        ("Rivals, i.e. doc:3 and doc:4, agree.", "Rivals, i.e. the sources, agree."),
        ("It shipped. doc:3 says so.", "It shipped. The source says so."),  # a real stop
        # a pointer that is a sentence of its own goes with its own full stop
        ("It ships. See doc:4.", "It ships."),
        ("It ships! See doc:4 and kb:2. Prices rose.", "It ships! Prices rose."),
    ],
)
def test_ref_cleaner_sibling_forms(written, stored):
    assert signal_analysis._clean(written) == stored
