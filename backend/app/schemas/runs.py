from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

RunMode = Literal["full", "ingest"]


class RunOut(BaseModel):
    id: int
    kind: str
    mode: RunMode = Field(
        description=(
            "'full' for a Daily Intelligence run (kind 'daily': ingest, then triage, "
            "analysis and the briefing), 'ingest' for an ingestion-only run."
        ),
    )
    trigger: str
    status: str
    started_at: datetime
    finished_at: datetime | None
    stats: dict = Field(
        description=(
            "The run's counts, written as it goes. Ingestion: sources_attempted, "
            "sources_failed, sources_skipped_today (job boards and watched pages already "
            "fetched that local day: each is read at most once a day), fetched, new_raw, "
            "metrics_written, documents_created, "
            "prefilter_passed, and the job boards' postings_new (first seen) and "
            "postings_closed (no longer listed by a complete listing). A full run adds its "
            "funnel: triaged, relevant, signals_new, signals_analyzed, signals_deep_analyzed, "
            "postings_tagged, page_changes (meaningful page changes filed as documents), "
            "facts_extracted, signals_breaking and briefing_status; and `errors` when a stage "
            "failed. A count is absent when the run stopped before its stage."
        ),
    )
    error: str | None
    agent_run_id: int | None = Field(
        description=(
            "The agent run that traces a full run's graph (its steps, tokens and cost). Null "
            "for ingest-only runs."
        ),
    )
    active: bool = Field(
        description=(
            "True while a worker is executing this run (it is 'running' and the ingestion lock "
            "is held). A 'running' run that is not active was interrupted: its worker stopped "
            "before finishing, and the next run marks it failed."
        ),
    )


class RunIn(BaseModel):
    source_ids: list[int] | None = None
    mode: RunMode = Field(
        "full",
        description=(
            "'full' (default) queues the Daily Intelligence graph; 'ingest' queues ingestion "
            "only. Without OPENAI_API_KEY a full run only ingests."
        ),
    )


class RunQueued(BaseModel):
    """The job is queued; the worker records its pipeline run (and, for a full run, its
    agent run) when it starts. GET /runs lists them (RunOut.agent_run_id)."""

    task_id: str
    mode: RunMode


class CooldownOut(BaseModel):
    next_manual_run_at: datetime | None
