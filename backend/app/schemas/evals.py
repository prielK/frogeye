"""The evaluation harness's results (backend/evals): what `python -m evals.run` writes to
`evals/results/latest.json` and `GET /evals` serves. The harness builds its output with
these models, so the file and the API cannot drift apart."""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

# the API always sends every field, so the response schema lists the defaulted ones as
# required too (as the briefing and analysis schemas do).
_SENT = ConfigDict(json_schema_serialization_defaults_required=True)


class EvalMetric(BaseModel):
    model_config = _SENT
    key: str = Field(description="Stable identifier, e.g. 'precision' or 'recall_at_8'.")
    label: str
    value: float | None = Field(description="None when no scored case can compute it.")
    numerator: int | None = Field(default=None, description="For a rate: the cases counted.")
    denominator: int | None = Field(default=None, description="For a rate: the cases it is over.")


class EvalGroup(BaseModel):
    """A breakdown row: a retrieval mode, a guardrail attack type, a triage case kind."""

    name: str
    cases: int
    metrics: list[EvalMetric]


class EvalFailure(BaseModel):
    model_config = _SENT
    case_id: str
    expected: str
    got: str
    detail: str | None = None


class EvalSuite(BaseModel):
    name: str = Field(description="triage, disambiguation, retrieval, guardrails, ...")
    title: str
    description: str = Field(description="What the suite measures and why.")
    label_source: str = Field(description="Who made the labels the suite scores against.")
    cases: int
    scored: int
    stale: int = Field(description="Cases with a model call missing from the recordings.")
    metrics: list[EvalMetric] = Field(description="The headline numbers.")
    groups: list[EvalGroup]
    failures: list[EvalFailure] = Field(description="Every scored case the app got wrong.")
    stale_cases: list[str]


class EvalModels(BaseModel):
    fast: str
    smart: str
    embedding: str
    judge: str


class EvalCost(BaseModel):
    live_usd: float = Field(description="Spent by this run on live calls (its ledger).")
    recorded_usd: float = Field(
        description="What the recorded calls this run replayed cost when they were recorded."
    )
    live_calls: int
    replayed_calls: int


class EvalsOut(BaseModel):
    model_config = _SENT
    schema_version: Literal[1] = 1
    run_at: datetime
    mode: Literal["record", "replay"]
    recorded_at: datetime | None = Field(description="When the recordings were last extended.")
    models: EvalModels
    cost: EvalCost
    stale_cases: int = Field(description="Cases left unscored across suites (not recorded).")
    suites: list[EvalSuite]
