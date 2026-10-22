"""Prompt blocks. Signal content (titles, facts, analyses) only inside `untrusted()`."""

from app.agents.graphs.newsroom.selection import SignalView
from app.agents.runtime.prompts import untrusted
from app.agents.scoring_config import edition_rank

BLOCK_CHARS = 60_000  # `untrusted` cap for prompt blocks: 25 signals are never cut


def flat(text: str | None) -> str:
    return " ".join((text or "-").split())


def block(name: str, body: str) -> str:
    """An untrusted block whose entries start on their own lines."""
    return untrusted(name, f"\n{body}\n", max_chars=BLOCK_CHARS)


def summary_of(v: SignalView) -> str | None:
    """The current analysis's summary: shown to the models and accepted as evidence."""
    return v.analysis.summary.text if v.analysis is not None else None


def signal_entry(v: SignalView, *, role: str | None = None) -> str:
    head = [f"Signal {v.id}"]
    if role is not None:
        score = edition_rank(v.importance, v.signal_type, role, v.exec_risk)  # its order
        head.append(f"role score {round(score)}")
    head += [
        f"importance {v.importance}",
        v.signal_type or "unclassified",
        v.stance or "neutral",
        f"competitors: {', '.join(v.competitors) or 'none'}",
        f"hedged: {'yes' if v.hedged else 'no'}",
    ]
    if v.thin:
        head.append("evidence: headline only")
    lines = [" | ".join(head), f"Title: {flat(v.title)}", f"Fact: {flat(v.fact)}"]
    lines.append(f"Impact: {flat(v.impact)}")
    if (summary := summary_of(v)) is not None:
        lines.append(f"Summary: {flat(summary)}")
    return "\n".join(lines)


def signals_block(views: list[SignalView], *, role: str | None = None) -> str:
    body = "\n\n".join(signal_entry(v, role=role) for v in views) or "none"
    return block("signals", body)


def evidence_block(views: list[SignalView]) -> str:
    """The signal evidence the fact-checker may accept: each cited signal's fact, impact and
    verified analysis summary (plus its hedged flag, so a hedge cannot become a certainty)."""
    entries = []
    for v in views:
        lines = [f"Signal {v.id} | hedged: {'yes' if v.hedged else 'no'}"]
        lines += [f"Fact: {flat(v.fact)}", f"Impact: {flat(v.impact)}"]
        if (summary := summary_of(v)) is not None:
            lines.append(f"Summary: {flat(summary)}")
        entries.append("\n".join(lines))
    return block("signals", "\n\n".join(entries) or "none")
