"""The fact-check of what the newsroom's models write: the headline, the lead and the
takeaway titles. Card text is copied from verified sources and is not
checked again.

One fast-tier call per check, verdicts over enumerated sentences. Evidence is
the cited signals' fact, impact and current analysis summary, plus the JFrog brief. Each
sentence is classified claim or advice, but every one here is a claim in effect: it passes
only when supported (advice could pass unsupported only in the legacy now_what, which
new briefings no longer write). Every passing sentence's numbers must also appear in its
evidence (the number guard). Fail-closed: a sentence citing no signal, or one the model
returns no verdict for, does not pass. As for stage B, the sentences a
reply leaves without a verdict (the fast model sometimes judges S1, the headline, only) are
asked again, alone and renumbered, once; a sentence still unjudged then does not pass.
"""

from dataclasses import dataclass
from typing import Any

from app.agents.briefing_schema import make_fact_check_model
from app.agents.graphs.newsroom.blocks import block, evidence_block, flat, summary_of
from app.agents.graphs.newsroom.selection import SignalView
from app.agents.nodes.verify import number_tokens
from app.agents.runtime.accounted import AccountedLLM
from app.agents.runtime.context import RunCtx
from app.agents.runtime.prompts import load_prompt
from app.agents.verified_llm import ask_until_judged
from app.rag.kb import load_jfrog_context

NO_VERDICT = "no verdict returned"

Verdicts = dict[tuple[Any, ...], tuple[bool, str]]


@dataclass(frozen=True)
class Claim:
    key: tuple[Any, ...]
    text: str
    cites: tuple[int, ...]


def _numbers(text: str) -> set[str]:
    """The number tokens, normalised so "$1,200" == "1200" and "20%" == "20 percent"."""
    return {t.replace(",", "").strip("$%") for t in number_tokens(text)}


def _unknown_numbers(claim: Claim, known: set[str]) -> list[str]:
    return [t for t in number_tokens(claim.text) if t.replace(",", "").strip("$%") not in known]


def _evidence_numbers(v: SignalView) -> set[str]:
    return _numbers(f"{v.title} {v.fact} {v.impact} {summary_of(v) or ''}")


async def _ask(
    llm: AccountedLLM,
    signals: dict[int, SignalView],
    claims: list[Claim],
    context: str,
    ctx: RunCtx,
) -> list[Any]:
    """One checker call over `claims` (numbered S1..Sn); returns its verdicts."""
    sentences = "\n".join(
        f"S{n} (cites signals {', '.join(map(str, c.cites))}): {flat(c.text)}"
        for n, c in enumerate(claims, 1)
    )
    cited = {i for c in claims for i in c.cites}
    out = await llm.structured(
        "fact_checker",
        "fast",
        make_fact_check_model(len(claims)),
        prompt=load_prompt("fact_checker"),
        variables={
            "jfrog_context": context,
            "signals": evidence_block([v for i, v in signals.items() if i in cited]),
            "sentences": block("sentences", sentences),
        },
        ctx=ctx,
    )
    return list(out.verdicts)


async def check_claims(
    llm: AccountedLLM, signals: dict[int, SignalView], claims: list[Claim], *, ctx: RunCtx
) -> Verdicts:
    """(ok, reason) per claim key (see the module docstring). Raises BudgetExceeded,
    LLMRefusal and LLMError."""
    verdicts: Verdicts = {}
    asked = []
    for claim in claims:
        if claim.cites:
            asked.append(claim)
        else:
            verdicts[claim.key] = (False, "cites no signal")
    if not asked:
        return verdicts
    context = load_jfrog_context()

    async def ask(pending: list[int]) -> list[Any]:
        return await _ask(llm, signals, [asked[i] for i in pending], context, ctx)

    # index in `asked` -> its verdicts; a sentence still unjudged fails closed below
    found, _ = await ask_until_judged(len(asked), ask)
    brief_numbers = _numbers(context)
    cited = {i for c in asked for i in c.cites}
    signal_numbers = {i: _evidence_numbers(signals[i]) for i in cited}
    for n, claim in enumerate(asked):
        judged = found.get(n)
        if not judged:
            verdicts[claim.key] = (False, NO_VERDICT)
        elif not all(v.supported for v in judged):
            verdicts[claim.key] = (
                False,
                "; ".join(flat(v.reason) for v in judged if not v.supported),
            )
        elif unknown := _unknown_numbers(
            claim, brief_numbers.union(*(signal_numbers[i] for i in claim.cites))
        ):  # a deterministic guard on every passing sentence
            verdicts[claim.key] = (False, f"numbers not in the sources: {', '.join(unknown)}")
        else:
            verdicts[claim.key] = (True, "")
    return verdicts


def ok(verdicts: Verdicts, key: tuple[Any, ...]) -> bool:
    return verdicts[key][0]
