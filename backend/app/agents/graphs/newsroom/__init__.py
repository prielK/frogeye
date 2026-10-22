"""Newsroom subgraph: writes the daily Morning Briefing (a master plus 4 role editions).

    editor ─▶ fact_check ─┬▶ role_adapt ×4 (Send) ─▶ assemble
                          └▶ assemble (budget stop)
    editor ─▶ assemble (editor failed or budget stop)

Modules: `selection` (eligibility and the signal views, with their current stage-B
analyses), `fill` (card text copied from the analysis), `blocks` (prompt blocks), `check`
(the fact-check), `graph` (the nodes) and `run` (entry points and persistence).

- Card text: a takeaway's summary, why it matters, products
  and actions, a section item's summary and why it matters, and a watch note are copied from
  the signal's verified analysis, or from its stage-A fact, impact and role actions when it
  has no current one (`from_analysis` False marks those). No model writes
  them. A card is one signal and a
  signal is shown at most once per briefing: a takeaway's signal is never repeated in a
  section.
- editor: smart tier, read-only tools; plans the headline, lead, takeaway titles (exactly
  one `signal_id` each), the theme assignment and the watchlist (signal ids). Its
  signal ids are a `Literal` over the selected signals, its themes `EDITOR_THEMES`
  (no "JFrog in the news"). A selected signal it leaves unthemed is placed under
  `theme_for(signal_type)`, so every selected signal is shown, and every edition can
  show any of them as an item.
- a signal whose documents are all thin (headline only, or a few lines: raw body
  length, whoever wrote it; the first-party exemption is for scoring only) is
  marked as such in the prompts and never rests a top takeaway, in the master or an
  edition. Themed sections and the watchlist may still carry it.
- fact_check: fast tier, verdicts over enumerated sentences: the headline, the lead and
  each takeaway title, the only text a model writes. Evidence is the cited
  signals' fact, impact and analysis summary plus the JFrog brief, and every passing
  sentence's numbers must appear in it. A takeaway whose title fails is dropped (its signal
  stays in its theme); a failed headline or lead falls back to the top shown story's
  verified title / fact. Then the master is built.
- role_adapt: code first chooses the edition's takeaways (`select_edition_takeaways`): the
  top 3 master signals by `role_score` (Exec's scaled by the analysis's risk level;
  a story with a current analysis boosted) that are not thin and have an action for
  the role (in the analysis, or stage A without one), at most 2 about any one company;
  fewer when fewer qualify. When all of them are master takeaways and another story
  qualifies, the third gives way to the best one the master does not lead with, so
  every edition leads with a story of its own whenever one exists. Then one fast-tier call
  per role writes the edition's headline, lead and titles for those ids (its schema allows
  no other; code matches them: a repeated id keeps its first title, an untitled
  story takes its own verified title), and the items it picks, in order. One fact-check
  of the written front follows; a chosen story whose title it removes is shown as an item, and a
  rejected headline or lead falls back to the edition's first shown story's verified title / fact.
  Items are copied exactly as the master's; a takeaway carries the role's angle (`why_it_matters`
  from the role's own analysis section, or its stage-A line without an analysis; `angle`; Exec adds
  `risk_level`) and the role's other actions.
  Exec keeps ≤ 5 items: dropped takeaways' stories first, then top by `role_score`.
- assemble: resolves the status and the final contents.

Failure modes: BudgetExceeded anywhere sets a flag and the briefing is
`skipped_budget` (a placeholder master only, no partial briefing). An editor
refusal/error makes it `failed`. A fact-checker error drops the master's takeaways and
falls back to the verified front (the copied card text needs no check); an edition's
checker error gives that edition the master's front and no takeaways; a role adapter
error omits that edition. Every error and budget stop logs a node step.

Per-run dependencies (LLM, tool deps, selected signals, per-run schemas) travel in the
LangGraph runtime context (`NewsroomEnv`), so graph state stays JSON-plain. The graph is
compiled with `checkpointer=False`: invoked from the daily graph's `newsroom` node it
never inherits that graph's checkpointer, so a resumed `newsroom` node re-runs the whole
subgraph (cheap: unchanged structured calls hit the LLM-call cache). Sessions are short:
`run_newsroom` reads everything in one session before the first LLM call; nodes reach the
DB only through AccountedLLM and the read-only tools. `persist_briefings` is flush-only: the caller
commits. The briefing's cost is the run's agent steps under the newsroom's node.
"""

from app.agents.graphs.newsroom.fill import FilledItem, fill_from_analysis, watch_note
from app.agents.graphs.newsroom.graph import (
    FAILED_HEADLINE,
    FAILED_LEAD,
    QUIET_HEADLINE,
    SKIPPED_HEADLINE,
    SKIPPED_LEAD,
    newsroom_graph,
)
from app.agents.graphs.newsroom.run import NewsroomResult, persist_briefings, run_newsroom
from app.agents.graphs.newsroom.selection import (
    LIVE_STATUSES,
    SignalView,
    _load_views,
    quiet_competitors,
    select_signals,
)

__all__ = [
    "FAILED_HEADLINE",
    "FAILED_LEAD",
    "LIVE_STATUSES",
    "QUIET_HEADLINE",
    "SKIPPED_HEADLINE",
    "SKIPPED_LEAD",
    "FilledItem",
    "NewsroomResult",
    "SignalView",
    "_load_views",
    "fill_from_analysis",
    "newsroom_graph",
    "persist_briefings",
    "quiet_competitors",
    "run_newsroom",
    "select_signals",
    "watch_note",
]
