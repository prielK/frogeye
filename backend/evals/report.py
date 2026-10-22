"""The results files: latest.json (the API's payload, `EvalsOut`) and a Markdown rendering,
plus the comparison a replay makes against the committed results."""

import json
from pathlib import Path

from app.schemas.evals import EvalMetric, EvalsOut, EvalSuite

DETAIL_CHARS = 400


def write(out: EvalsOut, json_path: Path, md_path: Path) -> None:
    json_path.parent.mkdir(parents=True, exist_ok=True)
    data = out.model_dump(mode="json")
    json_path.write_text(
        json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8", newline="\n"
    )
    md_path.write_text(markdown(out), encoding="utf-8", newline="\n")


def load(path: Path) -> EvalsOut | None:
    if not path.exists():
        return None
    return EvalsOut.model_validate(json.loads(path.read_text(encoding="utf-8")))


def differences(new: EvalsOut, old: EvalsOut | None) -> list[str]:
    """What differs between the suites of `new` and the same suites of `old` (the run date,
    mode and cost are a run's own and never compared)."""
    if old is None:
        return ["no committed results to compare with"]
    before = {s.name: s for s in old.suites}
    out = []
    for suite in new.suites:
        prior = before.get(suite.name)
        if prior is None:
            out.append(f"{suite.name}: not in the committed results")
            continue
        a, b = suite.model_dump(mode="json"), prior.model_dump(mode="json")
        for key in a:
            if a[key] != b[key]:
                out.append(f"{suite.name}.{key} differs")
    return out


def _value(m: EvalMetric) -> str:
    if m.value is None:
        return "n/a"
    if m.denominator is not None:
        return f"{m.value:.1%} ({m.numerator}/{m.denominator})"
    return f"{m.value:.3f}"


def _cell(text: str) -> str:
    return text.replace("|", "\\|").replace("\n", " ")


def headline(out: EvalsOut) -> str:
    lines = ["| Suite | Metric | Value |", "|---|---|---|"]
    for suite in out.suites:
        for m in suite.metrics:
            lines.append(f"| {suite.title} | {m.label} | {_value(m)} |")
    return "\n".join(lines)


def _suite_md(suite: EvalSuite) -> list[str]:
    lines = [
        f"## {suite.title}",
        "",
        suite.description,
        "",
        f"Cases: {suite.cases}, scored: {suite.scored}, not recorded: {suite.stale}. "
        f"Labels: {suite.label_source}.",
        "",
        "| Metric | Value |",
        "|---|---|",
        *(f"| {m.label} | {_value(m)} |" for m in suite.metrics),
        "",
    ]
    if suite.groups:
        lines += ["| Breakdown | Cases | Metrics |", "|---|---|---|"]
        lines += [
            f"| {g.name} | {g.cases} | "
            + "; ".join(f"{m.label} {_value(m)}" for m in g.metrics)
            + " |"
            for g in suite.groups
        ]
        lines.append("")
    if suite.failures:
        lines += [
            f"Failures ({len(suite.failures)}):",
            "",
            "| Case | Expected | Got | Detail |",
            "|---|---|---|---|",
        ]
        for f in suite.failures:
            detail = (f.detail or "")[:DETAIL_CHARS]
            lines.append(
                f"| {f.case_id} | {_cell(f.expected)} | {_cell(f.got)} | {_cell(detail)} |"
            )
        lines.append("")
    if suite.stale_cases:
        lines += [f"Not recorded: {', '.join(suite.stale_cases)}", ""]
    return lines


def markdown(out: EvalsOut) -> str:
    recorded = out.recorded_at.date().isoformat() if out.recorded_at else "never"
    lines = [
        "# FrogEye eval results",
        "",
        f"Run {out.run_at.isoformat(timespec='seconds')} in {out.mode} mode; recordings from "
        f"{recorded}. Models: fast {out.models.fast}, smart {out.models.smart}, embeddings "
        f"{out.models.embedding}, judge {out.models.judge}.",
        "",
        f"Cost: ${out.cost.live_usd:.4f} live ({out.cost.live_calls} calls); the "
        f"{out.cost.replayed_calls} replayed calls cost ${out.cost.recorded_usd:.4f} when "
        f"recorded. Cases not recorded: {out.stale_cases}.",
        "",
        headline(out),
        "",
    ]
    for suite in out.suites:
        lines += _suite_md(suite)
    return "\n".join(lines).rstrip() + "\n"
