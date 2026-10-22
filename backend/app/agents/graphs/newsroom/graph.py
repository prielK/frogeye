"""The Newsroom subgraph's nodes and graph (see the package docstring for the flow)."""

import dataclasses
import json
import logging
import operator
from collections import Counter
from collections.abc import Collection
from dataclasses import dataclass
from datetime import datetime
from functools import cache
from typing import Annotated, Any, TypedDict
from zoneinfo import ZoneInfo

from langgraph.graph import END, START, StateGraph
from langgraph.runtime import Runtime
from langgraph.types import Send

import app.agents.tools  # noqa: F401  (registers the built-in tools)
from app.agents.briefing_schema import (
    EDITION_ROLES,
    ROLE_FOCUS,
    ROLE_LABEL,
    THEMES,
    BriefingContent,
    BriefingModels,
    theme_for,
)
from app.agents.graphs.newsroom.blocks import block, signals_block
from app.agents.graphs.newsroom.check import Claim, Verdicts, check_claims, ok
from app.agents.graphs.newsroom.fill import (
    has_role_action,
    section_item,
    takeaway_card,
    watch_item,
)
from app.agents.graphs.newsroom.selection import SignalView
from app.agents.runtime.accounted import AccountedLLM, BudgetExceeded, LLMError, LLMRefusal
from app.agents.runtime.context import RunCtx
from app.agents.runtime.prompts import load_prompt
from app.agents.runtime.tool_agent import run_tool_agent
from app.agents.scoring_config import ANALYSED_TAKEAWAY_BOOST, edition_rank
from app.agents.tools.base import ToolDeps, get_tools
from app.models.enums import BriefingStatus, StepKind
from app.rag.kb import load_jfrog_context

logger = logging.getLogger(__name__)

EDITOR_TOOLS = ["query_signals", "get_similar_signals"]
EDITOR_MAX_TOOL_CALLS = 3
MAX_TAKEAWAYS = 3
MAX_WATCH = 3
EXEC_MAX_ITEMS = 5
EDITION_COMPANY_CAP = 2  # an edition's takeaways about any one company

QUIET_HEADLINE = "No significant moves"
SKIPPED_HEADLINE = "Briefing skipped — LLM budget reached"
SKIPPED_LEAD = (
    "Today's briefing was not written because the LLM budget was reached. "
    "Its signals carry over to the next run."
)
FAILED_HEADLINE = "Briefing unavailable — the editor failed"
FAILED_LEAD = (
    "The editor could not produce today's briefing. Its signals carry over to the next "
    "run; the agent run has the details."
)


# ---- run environment and state ----------------------------------------------------------


@dataclass(frozen=True)
class Window:
    start: datetime
    end: datetime
    tz: ZoneInfo

    def _at(self, moment: datetime) -> str:
        local = moment.astimezone(self.tz)
        return f"{local.day} {local:%b}, {local:%H:%M}"

    @property
    def label(self) -> str:
        offset = f"{self.end.astimezone(self.tz):%z}"  # "+0300": unambiguous, unlike IST
        zone = f"UTC{offset[:3]}:{offset[3:]}"
        return f"between {self._at(self.start)} and {self._at(self.end)} ({zone})"

    @property
    def date_label(self) -> str:
        local = self.end.astimezone(self.tz)
        return f"{local:%A} {local.day} {local:%B %Y}"


@dataclass(frozen=True)
class NewsroomEnv:
    """LangGraph runtime context: everything a node needs that is not graph state."""

    llm: AccountedLLM
    deps: ToolDeps
    ctx: RunCtx
    models: BriefingModels
    signals: dict[int, SignalView]  # selection order, importance desc
    quiet_competitors: tuple[str, ...]
    window: Window

    def ctx_for(self, agent: str) -> RunCtx:
        return dataclasses.replace(self.ctx, agent=agent)

    def views(self, ids: Any) -> list[SignalView]:
        """The views of `ids`, in selection order."""
        wanted = set(ids)
        return [v for i, v in self.signals.items() if i in wanted]

    @property
    def thin(self) -> set[int]:
        return {i for i, v in self.signals.items() if v.thin}

    async def note(self, agent: str, summary: str, **args: Any) -> None:
        await self.llm.log_step(
            self.ctx_for(agent), kind=StepKind.node, args=args or None, result_summary=summary
        )


class NewsroomState(TypedDict, total=False):
    plan: dict[str, Any]  # the editor's normalised plan
    failed: str  # the editor's error -> status failed
    budget: Annotated[bool, operator.or_]
    errors: Annotated[list[str], operator.add]
    master: dict[str, Any]
    editions: Annotated[list[dict[str, Any]], operator.add]  # {role, content}
    result: dict[str, Any]


# The role_adapt Send payload is a plain dict (so LangGraph adds no channels for it):
#   {"role", "master", "themes": [[signal_id, theme], ...]}  (the editor's assignment)


# ---- planning rules ---------------------------------------------------------------------


def _plan_takeaways(takeaways: list[dict[str, Any]], thin: set[int]) -> list[dict[str, Any]]:
    """A master takeaway rests on ONE signal (its `signal_id`); one
    on a headline-only (thin) signal or on another takeaway's signal is dropped. At most
    MAX_TAKEAWAYS, so a 4th is never even checked."""
    kept, used = [], set()
    for t in takeaways:
        sid = t["signal_id"]
        if sid in thin or sid in used:
            continue
        used.add(sid)
        kept.append(t)
    return kept[:MAX_TAKEAWAYS]


def _normalise_plan(env: NewsroomEnv, plan: dict[str, Any]) -> dict[str, Any]:
    """One theme per signal (the editor's first wins). A selected signal the editor
    left unthemed is still shown, under the theme its signal_type implies, after the
    editor's own (in importance order)."""
    seen: set[int] = set()
    assignment = []
    for a in plan["theme_assignment"]:
        if a["signal_id"] not in seen:
            seen.add(a["signal_id"])
            assignment.append(a)
    assignment += [
        {"signal_id": v.id, "theme": theme_for(v.signal_type)}
        for v in env.signals.values()
        if v.id not in seen
    ]
    return {
        "headline": plan["headline"],
        "lead": plan["lead"],
        "top_takeaways": _plan_takeaways(plan["top_takeaways"], env.thin),
        "theme_assignment": assignment,
        "watchlist": list(dict.fromkeys(w["signal_id"] for w in plan["watchlist"]))[:MAX_WATCH],
    }


def _front_claims(env: NewsroomEnv, front: dict[str, Any]) -> list[Claim]:
    """What a model wrote and a reader sees: the headline and lead (citing every selected
    signal) and each takeaway's title (citing its signal)."""
    everything = tuple(env.signals)
    return [
        Claim(("headline",), front["headline"], everything),
        Claim(("lead",), front["lead"], everything),
        *(
            Claim(("takeaway", n), t["title"], (t["signal_id"],))
            for n, t in enumerate(front["top_takeaways"])
        ),
    ]


def _checked_front(front: dict[str, Any], verdicts: Verdicts) -> dict[str, Any]:
    """A rejected headline or lead becomes None (the caller falls back to verified text); a
    takeaway whose title is rejected is dropped."""
    return {
        "headline": front["headline"] if ok(verdicts, ("headline",)) else None,
        "lead": front["lead"] if ok(verdicts, ("lead",)) else None,
        "top_takeaways": [
            t for n, t in enumerate(front["top_takeaways"]) if ok(verdicts, ("takeaway", n))
        ],
    }


def _sections(env: NewsroomEnv, ids: list[int], theme_of: dict[int, str]) -> list[dict]:
    """Section items for `ids` (in that order within a theme), in THEMES order."""
    sections = [
        {"theme": t, "items": [section_item(env.signals[i]) for i in ids if theme_of.get(i) == t]}
        for t in THEMES
    ]
    return [s for s in sections if s["items"]]


def _shown(content: dict[str, Any]) -> list[int]:
    """A briefing's cards, takeaways first: each signal is shown once."""
    ids = [sid for t in content["top_takeaways"] for sid in t["signal_ids"]]
    ids += [i["signal_id"] for s in content["sections"] for i in s["items"]]
    return ids


def _build_master(env: NewsroomEnv, plan: dict[str, Any], front: dict[str, Any]) -> dict:
    """The master from the plan and its checked front. A takeaway's signal is not repeated
    in a section (a dropped takeaway's signal stays in its theme). A headline/lead the
    checker rejected falls back to the top SHOWN story's verified title/fact, so the front
    never names a story the body dropped."""
    theme_of = {a["signal_id"]: a["theme"] for a in plan["theme_assignment"]}
    takeaways = [
        takeaway_card(
            env.signals[t["signal_id"]], title=t["title"], confidence=t["confidence"], role=None
        )
        for t in front["top_takeaways"]
    ]
    used = {t["signal_ids"][0] for t in takeaways}
    sections = _sections(env, [i for i in theme_of if i not in used], theme_of)
    master = {"top_takeaways": takeaways, "sections": sections}
    top = (env.views(_shown(master)) or list(env.signals.values()))[0]
    return {
        "headline": front["headline"] or top.title,
        "lead": front["lead"] or top.fact or top.title,
        **master,
        "quiet_competitors": list(env.quiet_competitors),
        "watchlist": [watch_item(env.signals[i]) for i in plan["watchlist"]],
    }


def _rank(env: NewsroomEnv, ids: Any, role: str, *, takeaways: bool = False) -> list[SignalView]:
    """`ids` in `role`'s edition order: `edition_rank` (role_score; Exec's scaled by the
    analysis's exec risk level), then importance, then id. For the edition's
    takeaways a story with a current analysis has its rank scaled by
    `ANALYSED_TAKEAWAY_BOOST`: its card carries the role's own angle and actions, so it
    wins a tie or a close call, while an important story without one still leads. For Exec
    the boost compounds with the risk multiplier: an analysed story its analysis
    rates low risk nets x0.92 (0.8 x 1.15) and can rank below an unanalysed story of a
    slightly lower role score, since the analysis itself says the risk is low; a high-risk
    one nets x1.4375."""

    def rank(v: SignalView) -> float:
        score = edition_rank(v.importance, v.signal_type, role, v.exec_risk)
        if takeaways and v.analysis is not None:
            score *= ANALYSED_TAKEAWAY_BOOST
        return score

    return sorted(env.views(ids), key=lambda v: (-rank(v), -v.importance, v.id))


def _outline(master: dict[str, Any]) -> dict[str, Any]:
    """What the role adapter is shown of the master: its front and which signals sit where
    (the signals' own text is in the signals block)."""
    return {
        "headline": master["headline"],
        "lead": master["lead"],
        "top_takeaways": [
            {"signal_id": t["signal_ids"][0], "title": t["title"]} for t in master["top_takeaways"]
        ],
        "sections": [
            {"theme": s["theme"], "signal_ids": [i["signal_id"] for i in s["items"]]}
            for s in master["sections"]
        ],
    }


def _within_cap(views: list[SignalView]) -> bool:
    """At most EDITION_COMPANY_CAP of `views` about any one company (a story about two
    counts for both; one about none is not capped)."""
    per_company = Counter(c for v in views for c in set(v.competitors))
    return all(n <= EDITION_COMPANY_CAP for n in per_company.values())


def select_edition_takeaways(
    env: NewsroomEnv, role: str, pool: list[int], *, master: Collection[int] = ()
) -> list[int]:
    """An edition's takeaways, chosen in code (its adapter only titles them): the top
    MAX_TAKEAWAYS of the master's signals (`pool`) in the role's takeaway order (`_rank`,
    analysed stories boosted) that are not thin (by raw evidence length) and have an action for the
    role (in the current analysis, or stage A without one), within the company cap (`_within_cap`).
    Fewer when fewer qualify: never filler.

    A role with no story of its own types ranks by importance alone, so it would
    lead with exactly the master's takeaways (`master`). When every chosen story is one of
    them and another eligible story keeps the cap, the lowest-ranked pick gives way to the
    best-ranked such story the master does not lead with."""
    eligible = [
        v for v in _rank(env, pool, role, takeaways=True) if not v.thin and has_role_action(v, role)
    ]
    chosen: list[SignalView] = []
    for v in eligible:
        if len(chosen) < MAX_TAKEAWAYS and _within_cap([*chosen, v]):
            chosen.append(v)
    leads = set(master)
    if chosen and all(v.id in leads for v in chosen):
        for v in eligible:
            swapped = [*chosen[:-1], v]
            if v.id not in leads and _within_cap(swapped):
                chosen = sorted(swapped, key=eligible.index)  # still the role's order
                break
    return [v.id for v in chosen]


def _edition_plan(draft: dict[str, Any], pool: list[int], chosen: list[int]) -> dict[str, Any]:
    """An edition only uses the master's signals, and its takeaways are exactly
    the code-chosen stories, in the code's order. The adapter's titles are matched to them
    in code (its schema has no count keywords): an id it was not given is ignored, a
    repeated one keeps its first title, and a chosen story it left untitled (or titled "")
    is listed in `untitled`, to take its own verified title (`_check_edition`). `drafted` is
    every chosen story, so one whose title the check drops is still shown as an item."""
    wanted = set(chosen)
    titles: dict[int, str] = {}
    for t in draft.get("top_takeaways", []):  # absent when nothing was chosen
        if t["signal_id"] in wanted and t["title"].strip():
            titles.setdefault(t["signal_id"], t["title"])
    allowed = set(pool)
    return {
        "headline": draft["headline"],
        "lead": draft["lead"],
        "top_takeaways": [{"signal_id": i, "title": titles[i]} for i in chosen if i in titles],
        "untitled": [i for i in chosen if i not in titles],
        "drafted": list(chosen),
        "pick": [i for i in dict.fromkeys(draft["pick"]) if i in allowed],
    }


def _confidence(env: NewsroomEnv, sid: int, master: dict[str, Any]) -> str:
    """The master's confidence for the same story, else low when hedged, medium otherwise."""
    for t in master["top_takeaways"]:
        if t["signal_ids"] == [sid]:
            return t["confidence"]
    return "low" if env.signals[sid].hedged else "medium"


def _shape_edition(
    env: NewsroomEnv,
    role: str,
    front: dict[str, Any],
    draft: dict[str, Any],
    master: dict[str, Any],
    theme_of: dict[int, str],
) -> dict[str, Any]:
    """Per-signal text is copied from the verified analysis (an edition's takeaway
    with the role's angle and actions; an item exactly as the master's); the adapter only
    writes the front and titles, picks and orders. Each item sits under the editor's theme,
    in THEMES order; an edition takeaway's signal is not repeated as an item. A chosen
    takeaway that did not survive (its title removed by the check, or every one on a
    checker error) leaves its story as an item, first, as the master does: the adapter
    may have left it out of `pick`. Then Exec keeps at most EXEC_MAX_ITEMS items: those
    stories, then the rest in the role's order (`_rank`).

    A headline or lead the check rejected (None) falls back to the edition's FIRST SHOWN
    story's verified title (fact), as the master's falls back to its own top story (editions): the
    master's front may name a story this edition does not show. Only
    an edition that shows nothing takes the master's."""
    takeaways = [
        takeaway_card(
            env.signals[t["signal_id"]],
            title=t["title"],
            confidence=_confidence(env, t["signal_id"], master),
            role=role,
        )
        for t in front["top_takeaways"]
    ]
    used = {t["signal_ids"][0] for t in takeaways}
    dropped = [i for i in draft["drafted"] if i not in used and i in theme_of]
    items = [
        i for i in dict.fromkeys([*dropped, *draft["pick"]]) if i not in used and i in theme_of
    ]
    if role == "exec":
        ranked = [v.id for v in _rank(env, [i for i in items if i not in dropped], role)]
        keep = set([*dropped, *ranked][:EXEC_MAX_ITEMS])
        items = [i for i in items if i in keep]
    body = {"top_takeaways": takeaways, "sections": _sections(env, items, theme_of)}
    first = next((env.signals[i] for i in _shown(body)), None)
    return {
        "headline": front["headline"] or (first.title if first else master["headline"]),
        "lead": front["lead"] or ((first.fact or first.title) if first else master["lead"]),
        **body,
        "quiet_competitors": list(master["quiet_competitors"]),
        "watchlist": [dict(w) for w in master["watchlist"]],
    }


# ---- nodes ------------------------------------------------------------------------------


async def _editor(state: NewsroomState, runtime: Runtime[NewsroomEnv]) -> dict[str, Any]:
    env = runtime.context
    views = list(env.signals.values())
    try:
        plan = await run_tool_agent(
            env.llm,
            task="editor",
            tier="smart",
            prompt=load_prompt("editor"),
            variables={
                "date": env.window.date_label,
                "window": env.window.label,
                "count": len(views),
                "signals": signals_block(views),
            },
            tools=get_tools(EDITOR_TOOLS),
            output_schema=env.models.EditorPlan,
            max_tool_calls=EDITOR_MAX_TOOL_CALLS,
            deps=env.deps,
            ctx=env.ctx_for("editor"),
        )
    except BudgetExceeded as exc:
        await env.note("editor", f"budget: {exc.scope}")
        return {"budget": True}
    except (LLMRefusal, LLMError) as exc:  # includes schema-invalid output, e.g. unknown id
        logger.warning("newsroom editor failed: %s", exc)
        await env.note("editor", f"error: {exc}")
        return {"failed": str(exc), "errors": [f"editor: {exc}"]}
    return {"plan": _normalise_plan(env, plan.model_dump(mode="json"))}


def _after_editor(state: NewsroomState) -> str:
    return "assemble" if state.get("budget") or state.get("failed") else "fact_check"


async def _fact_check(state: NewsroomState, runtime: Runtime[NewsroomEnv]) -> dict[str, Any]:
    """Check the master's front, then build the master from the plan (copied card text)."""
    env = runtime.context
    plan = state["plan"]
    claims = _front_claims(env, plan)
    try:
        verdicts = await check_claims(env.llm, env.signals, claims, ctx=env.ctx_for("fact_checker"))
    except BudgetExceeded as exc:
        await env.note("fact_checker", f"budget: {exc.scope}")
        return {"budget": True}
    except (LLMRefusal, LLMError) as exc:
        logger.warning("newsroom fact-check failed: %s", exc)
        await env.note("fact_checker", f"error: {exc}")
        front = {"headline": None, "lead": None, "top_takeaways": []}  # unchecked: not shipped
        return {"master": _build_master(env, plan, front), "errors": [f"fact_checker: {exc}"]}
    front = _checked_front(plan, verdicts)
    passed = sum(1 for good, _ in verdicts.values() if good)
    dropped = len(plan["top_takeaways"]) - len(front["top_takeaways"])
    await env.note("fact_checker", f"{passed}/{len(claims)} ok, {dropped} takeaways dropped")
    return {"master": _build_master(env, plan, front)}


def _after_fact_check(state: NewsroomState) -> Any:
    if state.get("budget"):
        return "assemble"
    themes = [[a["signal_id"], a["theme"]] for a in state["plan"]["theme_assignment"]]
    return [
        Send("role_adapt", {"role": r, "master": state["master"], "themes": themes})
        for r in EDITION_ROLES
    ]


async def _check_edition(
    env: NewsroomEnv, role: str, draft: dict[str, Any], master: dict[str, Any]
) -> dict[str, Any]:
    """One fact-check of the edition's own front; unsupported text is removed (no rewrite):
    a rejected headline or lead is None, and `_shape_edition` replaces it with the
    edition's own top story's verified text. A chosen story the adapter left untitled takes
    its signal's own title: verified text, as the master's items show it, so it is
    not checked again. The takeaways keep the code's order. A checker error falls back to
    the master's front and no takeaways; BudgetExceeded propagates to the node."""
    try:
        verdicts = await check_claims(
            env.llm, env.signals, _front_claims(env, draft), ctx=env.ctx_for("fact_checker")
        )
    except (LLMRefusal, LLMError) as exc:
        logger.warning("newsroom fact-check of the %s edition failed: %s", role, exc)
        await env.note("fact_checker", f"error: {exc}", role=role)
        return {"headline": master["headline"], "lead": master["lead"], "top_takeaways": []}
    front = _checked_front(draft, verdicts)
    kept = {t["signal_id"]: t for t in front["top_takeaways"]}
    kept |= {i: {"signal_id": i, "title": env.signals[i].title} for i in draft["untitled"]}
    return {
        "headline": front["headline"],
        "lead": front["lead"],
        "top_takeaways": [kept[i] for i in draft["drafted"] if i in kept],
    }


async def _role_adapt(task: dict[str, Any], runtime: Runtime[NewsroomEnv]) -> dict[str, Any]:
    env = runtime.context
    role, master = task["role"], task["master"]
    theme_of = {sid: theme for sid, theme in task["themes"]}
    pool = list(dict.fromkeys(_shown(master)))
    leads = [t["signal_ids"][0] for t in master["top_takeaways"]]
    chosen = select_edition_takeaways(env, role, pool, master=leads)
    try:
        out = await env.llm.structured(
            "role_adapter",
            "fast",
            env.models.role_edition(tuple(chosen)),
            prompt=load_prompt("role_adapter"),
            variables={
                "jfrog_context": load_jfrog_context(),
                "role": role,
                "label": ROLE_LABEL[role],
                "focus": ROLE_FOCUS[role],
                "master": block(
                    "master", json.dumps(_outline(master), ensure_ascii=False, indent=2)
                ),
                "signals": signals_block(_rank(env, pool, role), role=role),
                "takeaways": ", ".join(map(str, chosen)) or "none",
            },
            ctx=env.ctx_for("role_adapter"),
        )
        draft = _edition_plan(out.model_dump(mode="json"), pool, chosen)
        front = await _check_edition(env, role, draft, master)
    except BudgetExceeded as exc:
        await env.note("role_adapter", f"budget: {exc.scope}", role=role)
        return {"budget": True}
    except (LLMRefusal, LLMError) as exc:
        logger.warning("newsroom role adapter %s failed: %s", role, exc)
        await env.note("role_adapter", f"error: {exc}", role=role)
        return {"errors": [f"role_adapter {role}: {exc}"]}
    edition = _shape_edition(env, role, front, draft, master, theme_of)
    return {"editions": [{"role": role, "content": edition}]}


def _placeholder(headline: str, lead: str) -> dict[str, Any]:
    return BriefingContent(
        headline=headline,
        lead=lead,
        top_takeaways=[],
        sections=[],
        quiet_competitors=[],
        watchlist=[],
    ).model_dump(mode="json")


def _validated(models: BriefingModels, content: dict[str, Any]) -> dict[str, Any]:
    """Guard: the final content must only cite selected signals (raises otherwise)."""
    return models.BriefingContent.model_validate(content).model_dump(mode="json")


async def _assemble(state: NewsroomState, runtime: Runtime[NewsroomEnv]) -> dict[str, Any]:
    env = runtime.context
    editions: dict[str, Any] = {}
    if state.get("budget"):
        status, master = BriefingStatus.skipped_budget, _placeholder(SKIPPED_HEADLINE, SKIPPED_LEAD)
    elif state.get("failed"):
        status, master = BriefingStatus.failed, _placeholder(FAILED_HEADLINE, FAILED_LEAD)
    else:
        status, master = BriefingStatus.published, _validated(env.models, state["master"])
        by_role = {e["role"]: e["content"] for e in state.get("editions", [])}
        editions = {r: _validated(env.models, by_role[r]) for r in EDITION_ROLES if r in by_role}
    n_items = sum(len(s["items"]) for s in master["sections"])
    await env.note(
        "assemble",
        f"{status}: {n_items} items, {len(master['top_takeaways'])} takeaways, "
        f"editions: {', '.join(editions) or 'none'}",
    )
    return {"result": {"status": str(status), "master": master, "editions": editions}}


@cache
def newsroom_graph():
    """The compiled Newsroom subgraph; never checkpointed, not even under a parent's."""
    g = StateGraph(NewsroomState, context_schema=NewsroomEnv)
    g.add_node("editor", _editor)
    g.add_node("fact_check", _fact_check)
    g.add_node("role_adapt", _role_adapt)
    g.add_node("assemble", _assemble)
    g.add_edge(START, "editor")
    g.add_conditional_edges("editor", _after_editor, ["fact_check", "assemble"])
    g.add_conditional_edges("fact_check", _after_fact_check, ["role_adapt", "assemble"])
    g.add_edge("role_adapt", "assemble")
    g.add_edge("assemble", END)
    return g.compile(name="newsroom", checkpointer=False)
