"""The daily run's funnel: one set of counts shared by the graph, the run record, the
briefing and the API.

- During a run the graph writes the counts so far into `PipelineRun.stats` after every
  stage, before it announces the stage over SSE, so a poll of the run and the live event
  carry the same numbers.
- A briefing carries its DAY's funnel: the counts of every run that filed a version of that
  date's briefing, summed (`day_funnel`), with `runs` saying how many runs that is.
"""

from collections.abc import Iterable
from typing import Any

FUNNEL_COUNTS = (
    "fetched",
    "documents_created",
    "prefilter_passed",
    "triaged",
    "relevant",
    "signals_new",
    "signals_analyzed",
    "signals_deep_analyzed",
    "postings_tagged",
    "page_changes",
    "facts_extracted",
    "signals_breaking",
)
FUNNEL_KEYS = (*FUNNEL_COUNTS, "briefing_status")


def empty_funnel() -> dict[str, Any]:
    return {**dict.fromkeys(FUNNEL_COUNTS, 0), "briefing_status": None}


def _count(value: Any) -> int:
    return value if isinstance(value, int) and not isinstance(value, bool) and value > 0 else 0


def day_funnel(earlier: Iterable[dict[str, Any] | None], current: dict[str, Any]) -> dict[str, Any]:
    """The day's cumulative funnel: `current` (this run's counts) plus every earlier run's
    that fed the same briefing date. `briefing_status` is this run's; `runs` counts them."""
    runs = [dict(r or {}) for r in earlier]
    out: dict[str, Any] = {
        k: _count(current.get(k)) + sum(_count(r.get(k)) for r in runs) for k in FUNNEL_COUNTS
    }
    out["briefing_status"] = current.get("briefing_status")
    out["runs"] = len(runs) + 1
    return out
