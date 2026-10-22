import math

import pytest

from evals import metrics


def by_key(ms):
    return {m.key: m for m in ms}


def test_classification_counts_the_positive_class():
    # tp=2, fp=1, fn=1, tn=1
    pairs = [(True, True), (True, True), (False, True), (True, False), (False, False)]
    m = by_key(metrics.classification(pairs))
    assert (m["precision"].numerator, m["precision"].denominator) == (2, 3)
    assert (m["recall"].numerator, m["recall"].denominator) == (2, 3)
    assert m["f1"].value == pytest.approx(round(4 / 6, 4))
    assert m["accuracy"].value == 0.6


def test_a_rate_over_no_cases_is_none_not_zero():
    m = by_key(metrics.classification([(False, False)]))
    assert m["precision"].value is None and m["precision"].denominator == 0
    assert m["recall"].value is None and m["f1"].value is None
    assert m["accuracy"].value == 1.0


def test_attribution_needs_every_required_vendor_and_nothing_unexpected():
    assert metrics.attribution_ok(["gitlab"], [], ["gitlab"])
    assert metrics.attribution_ok(["chainguard"], ["github"], ["chainguard", "github"])
    assert not metrics.attribution_ok(["gitlab"], [], [])  # missed
    assert not metrics.attribution_ok(["chainguard"], [], ["chainguard", "harness"])  # extra
    assert metrics.attribution_ok([], [], [])  # industry news names no vendor


def test_ranking_metrics_on_a_hand_computed_list():
    ranked = metrics.dedupe(["doc:9", "doc:1", "doc:1", "kb:2", "doc:5"])
    assert ranked == ["doc:9", "doc:1", "kb:2", "doc:5"]
    relevant = {"doc:1", "doc:5", "doc:7"}
    assert metrics.recall_at(ranked, relevant, 8) == pytest.approx(2 / 3)
    assert metrics.mrr_at(ranked, relevant, 10) == 0.5
    dcg = 1 / math.log2(3) + 1 / math.log2(5)
    ideal = 1 + 1 / math.log2(3) + 1 / math.log2(4)
    assert metrics.ndcg_at(ranked, relevant, 8) == pytest.approx(dcg / ideal)
    assert metrics.mrr_at(["doc:3"], relevant, 10) == 0.0
    assert metrics.ndcg_at(["doc:1", "doc:5", "doc:7"], relevant, 8) == pytest.approx(1.0)


def test_ranking_cuts_at_k():
    ranked = [f"doc:{i}" for i in range(20)]
    assert metrics.recall_at(ranked, {"doc:8"}, 8) == 0.0  # ninth place
    assert metrics.mrr_at(ranked, {"doc:10"}, 10) == 0.0
    with pytest.raises(ValueError):
        metrics.recall_at(ranked, set(), 8)


def test_ranking_means_over_queries():
    m = by_key(metrics.ranking([(["a"], {"a"}), (["b"], {"a"})]))
    assert m["recall_at_8"].value == 0.5 and m["mrr_at_10"].value == 0.5
    assert by_key(metrics.ranking([]))["ndcg_at_8"].value is None


def test_detection_splits_planted_from_genuine():
    pairs = [(True, True), (True, False), (True, True), (False, False), (False, True)]
    caught, alarm = metrics.detection(pairs)
    assert (caught.numerator, caught.denominator) == (2, 3)
    assert (alarm.numerator, alarm.denominator) == (1, 2)


def test_shares_follow_the_label_order():
    ms = metrics.shares(
        ["supported", "partial", "supported"],
        [("supported", "S"), ("partial", "P"), ("unsupported", "U")],
    )
    assert [(m.key, m.numerator) for m in ms] == [
        ("supported_share", 2),
        ("partial_share", 1),
        ("unsupported_share", 0),
    ]
