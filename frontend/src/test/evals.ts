import type { Schemas } from "@/lib/api/client";

/*
 * GET /evals as the tests serve it: a trimmed copy of a record run's results (backend/evals:
 * results/latest.json), four suites with each shape the page reads: rates with their counts and
 * scores without, groups that share their measures (a chart) and groups that do not (a table),
 * a sub-breakdown, failures, a suite with a case left unscored, and the fact-checker.
 */

type Evals = Schemas["EvalsOut"];
type EvalMetric = Schemas["EvalMetric"];

const rate = (key: string, label: string, numerator: number, denominator: number): EvalMetric => ({ key, label, value: denominator ? Number((numerator / denominator).toFixed(4)) : null, numerator, denominator });
const score = (key: string, label: string, value: number | null): EvalMetric => ({ key, label, value, numerator: null, denominator: null });
const scores = (r: number, m: number, n: number) => [score("recall_at_8", "Recall@8", r), score("mrr_at_10", "MRR@10", m), score("ndcg_at_8", "nDCG@8", n)];

export function evalResults(overrides: Partial<Evals> = {}): Evals {
  return {
    schema_version: 1,
    run_at: "2026-10-21T19:31:17Z",
    mode: "record",
    recorded_at: "2026-10-21T19:30:11Z",
    models: { fast: "gpt-5-nano", smart: "gpt-6-luna", embedding: "text-embedding-3-small", judge: "gpt-6-luna" },
    cost: { live_usd: 0, recorded_usd: 0.030118, live_calls: 0, replayed_calls: 447 },
    stale_cases: 1,
    suites: [
      {
        name: "triage",
        title: "Triage",
        description: "The fast model's relevance call and competitor attribution on 60 real collected documents.",
        label_source: "labelled blind from each document's raw text, never from FrogEye's outputs",
        cases: 60,
        scored: 60,
        stale: 0,
        metrics: [rate("precision", "Precision", 29, 45), rate("recall", "Recall", 29, 30), score("f1", "F1", 0.7733)],
        groups: [
          { name: "hard_negative", cases: 22, metrics: [rate("accuracy", "Accuracy", 9, 22), rate("pipeline_accuracy", "Accuracy, keyword screen first", 14, 22)] },
          { name: "hard_positive", cases: 9, metrics: [rate("accuracy", "Accuracy", 8, 9), rate("pipeline_accuracy", "Accuracy, keyword screen first", 8, 9)] },
          { name: "negative", cases: 8, metrics: [rate("accuracy", "Accuracy", 5, 8), rate("pipeline_accuracy", "Accuracy, keyword screen first", 7, 8)] },
          { name: "positive", cases: 21, metrics: [rate("accuracy", "Accuracy", 21, 21), rate("pipeline_accuracy", "Accuracy, keyword screen first", 21, 21)] },
        ],
        failures: [
          { case_id: "triage-02", expected: "relevant [github]", got: "relevant []", detail: 'doc 5317 "GitHub package firewall in public preview" (positive); model listed 0 slugs []' },
          { case_id: "triage-07", expected: "not relevant", got: "relevant [harness]", detail: null },
        ],
        stale_cases: [],
      },
      {
        name: "disambiguation",
        title: "Competitor disambiguation",
        description: "The smart tool-using agent that re-judges triage when a document names an ambiguous competitor.",
        label_source: "labelled blind from each document's raw text, never from FrogEye's outputs",
        cases: 28,
        scored: 27,
        stale: 1,
        metrics: [rate("accuracy", "Accuracy after disambiguation", 25, 27), rate("triage_accuracy", "Accuracy of triage alone (baseline)", 18, 27)],
        groups: [
          { name: "about the competitor", cases: 12, metrics: [rate("accuracy", "Accuracy", 11, 12)] },
          { name: "not about it", cases: 15, metrics: [rate("accuracy", "Accuracy", 14, 15)] },
        ],
        failures: [],
        stale_cases: ["disambig-09"],
      },
      {
        name: "retrieval",
        title: "Retrieval",
        description: "Search quality on 25 analyst queries, per mode: keyword only, vector only, hybrid and hybrid with the LLM rerank.",
        label_source: "labelled blind from each document's raw text, never from FrogEye's outputs",
        cases: 25,
        scored: 25,
        stale: 0,
        metrics: scores(0.9669, 1, 0.9568),
        groups: [
          { name: "keyword", cases: 25, metrics: scores(0.6181, 0.76, 0.652) },
          { name: "vector", cases: 25, metrics: scores(0.9535, 0.98, 0.9292) },
          { name: "hybrid", cases: 25, metrics: scores(0.9669, 1, 0.9568) },
          { name: "hybrid_rerank", cases: 25, metrics: scores(0.9615, 0.9033, 0.8904) },
          { name: "hybrid · semantic queries", cases: 11, metrics: scores(0.9247, 1, 0.9048) },
        ],
        failures: [],
        stale_cases: [],
      },
      {
        name: "guardrails",
        title: "Deterministic guardrails",
        description: "The no-LLM checks between the models and the reader.",
        label_source: "real verified quotes and numbers, with planted attacks",
        cases: 20,
        scored: 20,
        stale: 0,
        metrics: [rate("catch_rate", "Catch rate", 10, 10), rate("false_alarm_rate", "False-alarm rate", 0, 10)],
        groups: [
          { name: "quote_genuine", cases: 10, metrics: [rate("pass_rate", "Pass rate", 10, 10)] },
          { name: "quote_fabricated", cases: 5, metrics: [rate("catch_rate", "Catch rate", 5, 5)] },
          { name: "number_altered", cases: 5, metrics: [rate("catch_rate", "Catch rate", 5, 5)] },
        ],
        failures: [],
        stale_cases: [],
      },
      {
        name: "fact_checker",
        title: "Stage-B fact-checker",
        description: "Stage B's model checker, which deletes claims their cited sources do not support.",
        label_source: "claims written against real excerpts, with planted errors",
        cases: 30,
        scored: 30,
        stale: 0,
        metrics: [rate("unsupported_recall", "Unsupported claims removed", 16, 16), rate("false_removal_rate", "Supported claims removed", 0, 14)],
        groups: [
          { name: "wrong_number", cases: 4, metrics: [rate("caught", "Caught", 4, 4)] },
          { name: "supported", cases: 14, metrics: [rate("kept", "Kept", 14, 14)] },
        ],
        failures: [],
        stale_cases: [],
      },
    ],
    ...overrides,
  };
}
