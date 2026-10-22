"""The committed eval datasets are well formed: unique ids, a label source on every row,
and every reference resolves into the fixture corpus (no model, no database)."""

import pytest

from evals.harness import DATASETS, read_jsonl
from evals.suites import guardrails

CORPUS = {r["id"]: r for r in read_jsonl(DATASETS / "corpus" / "documents.jsonl")}
KB = {r["id"] for r in read_jsonl(DATASETS / "corpus" / "jfrog_kb.jsonl")}
EXCERPT_CHARS = 1200
ITEM_CHARS = 2000


@pytest.mark.parametrize(
    "name",
    [
        "triage.jsonl",
        "disambiguation.jsonl",
        "retrieval.jsonl",
        "guardrails.jsonl",
        "fact_checker.jsonl",
        "faithfulness.jsonl",
        "corpus/documents.jsonl",
        "corpus/jfrog_kb.jsonl",
    ],
)
def test_rows_have_unique_ids_and_a_label_source(name):
    rows = read_jsonl(DATASETS / name)
    assert rows and len({r["id"] for r in rows}) == len(rows)
    assert all(r["label_source"] for r in rows)


def test_corpus_holds_excerpts_only():
    assert all(len(r["text"]) <= EXCERPT_CHARS for r in CORPUS.values())
    for row in read_jsonl(DATASETS / "faithfulness.jsonl"):  # passages, about 2,000 per item
        for item in row["items"]:
            assert sum(len(t) for t in item["evidence"].values()) <= ITEM_CHARS
            assert set(item["evidence"]) <= set(row["sources"])


def test_labelled_documents_are_in_the_corpus():
    for name in ("triage.jsonl", "disambiguation.jsonl"):
        assert all(r["doc_id"] in CORPUS for r in read_jsonl(DATASETS / name))
    triage = read_jsonl(DATASETS / "triage.jsonl")
    assert sum(r["relevant"] for r in triage) * 2 == len(triage)  # balanced


def test_retrieval_refs_resolve():
    for row in read_jsonl(DATASETS / "retrieval.jsonl"):
        for ref in row["relevant"]:
            kind, _, ref_id = ref.partition(":")
            assert int(ref_id) in (CORPUS if kind == "doc" else KB), ref


def test_guardrail_attacks_are_known_and_genuine_quotes_are_in_their_source():
    for row in read_jsonl(DATASETS / "guardrails.jsonl"):
        assert row["attack"] in guardrails.ATTACKS
        if row["attack"] == "quote_genuine":
            assert row["text"] in row["source"]["text"]
