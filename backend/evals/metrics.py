"""Pure metric functions of the eval suites (no I/O, no app imports).

Rates return `EvalMetric`s carrying their numerator and denominator, so a reader sees
"57 of 60" next to 0.95, and a metric over zero cases is None, never 0 or 1.
"""

import math
from collections.abc import Iterable, Sequence

from app.schemas.evals import EvalMetric

DIGITS = 4


def rate(key: str, label: str, hits: int, total: int) -> EvalMetric:
    value = round(hits / total, DIGITS) if total else None
    return EvalMetric(key=key, label=label, value=value, numerator=hits, denominator=total)


def score(key: str, label: str, value: float | None) -> EvalMetric:
    return EvalMetric(key=key, label=label, value=None if value is None else round(value, DIGITS))


def mean(values: Iterable[float]) -> float | None:
    values = list(values)
    return sum(values) / len(values) if values else None


# ---- binary classification (triage relevance) -------------------------------------------


def confusion(pairs: Iterable[tuple[bool, bool]]) -> tuple[int, int, int, int]:
    """(tp, fp, fn, tn) of (expected, predicted) pairs."""
    tp = fp = fn = tn = 0
    for expected, predicted in pairs:
        if expected and predicted:
            tp += 1
        elif predicted:
            fp += 1
        elif expected:
            fn += 1
        else:
            tn += 1
    return tp, fp, fn, tn


def classification(pairs: Sequence[tuple[bool, bool]]) -> list[EvalMetric]:
    """Precision, recall, F1 and accuracy of the positive class."""
    tp, fp, fn, tn = confusion(pairs)
    f1 = 2 * tp / (2 * tp + fp + fn) if tp + fp + fn else None
    return [
        rate("precision", "Precision", tp, tp + fp),
        rate("recall", "Recall", tp, tp + fn),
        score("f1", "F1", f1),
        rate("accuracy", "Accuracy", tp + tn, len(pairs)),
    ]


def attribution_ok(required: Iterable[str], also_ok: Iterable[str], got: Iterable[str]) -> bool:
    """Every required vendor named, and nothing beyond the required and acceptable ones."""
    required, got = set(required), set(got)
    return required <= got <= required | set(also_ok)


# ---- ranking (retrieval) ----------------------------------------------------------------


def dedupe(refs: Iterable[str]) -> list[str]:
    """Refs in first-seen order (several chunks of one document count once)."""
    return list(dict.fromkeys(refs))


def recall_at(ranked: Sequence[str], relevant: set[str], k: int) -> float:
    if not relevant:
        raise ValueError("a query needs at least one relevant item")
    return len(set(ranked[:k]) & relevant) / len(relevant)


def mrr_at(ranked: Sequence[str], relevant: set[str], k: int) -> float:
    """Reciprocal rank of the first relevant item in the top k (0 when none is)."""
    for i, ref in enumerate(ranked[:k], start=1):
        if ref in relevant:
            return 1 / i
    return 0.0


def ndcg_at(ranked: Sequence[str], relevant: set[str], k: int) -> float:
    """Binary-gain nDCG@k: DCG over the ideal DCG of min(|relevant|, k) hits at the top."""
    if not relevant:
        raise ValueError("a query needs at least one relevant item")
    dcg = sum(1 / math.log2(i + 1) for i, ref in enumerate(ranked[:k], start=1) if ref in relevant)
    ideal = sum(1 / math.log2(i + 1) for i in range(1, min(len(relevant), k) + 1))
    return dcg / ideal


def ranking(runs: Sequence[tuple[Sequence[str], set[str]]]) -> list[EvalMetric]:
    """Mean recall@8, MRR@10 and nDCG@8 over (ranked refs, relevant refs) per query."""
    return [
        score("recall_at_8", "Recall@8", mean(recall_at(r, rel, 8) for r, rel in runs)),
        score("mrr_at_10", "MRR@10", mean(mrr_at(r, rel, 10) for r, rel in runs)),
        score("ndcg_at_8", "nDCG@8", mean(ndcg_at(r, rel, 8) for r, rel in runs)),
    ]


# ---- detection (guardrails, fact-checker) -----------------------------------------------


def detection(
    pairs: Sequence[tuple[bool, bool]],
    *,
    caught: tuple[str, str] = ("catch_rate", "Catch rate"),
    false_alarm: tuple[str, str] = ("false_alarm_rate", "False-alarm rate"),
) -> list[EvalMetric]:
    """(should be flagged, was flagged) pairs: the share of planted errors flagged, and the
    share of genuine inputs flagged by mistake."""
    bad = [flagged for should, flagged in pairs if should]
    good = [flagged for should, flagged in pairs if not should]
    return [rate(*caught, sum(bad), len(bad)), rate(*false_alarm, sum(good), len(good))]


# ---- judged shares (faithfulness) -------------------------------------------------------


def shares(ratings: Sequence[str], labels: Sequence[tuple[str, str]]) -> list[EvalMetric]:
    """The share of each rating, in `labels` order ((rating, label) pairs)."""
    return [
        rate(f"{key}_share", label, sum(r == key for r in ratings), len(ratings))
        for key, label in labels
    ]
