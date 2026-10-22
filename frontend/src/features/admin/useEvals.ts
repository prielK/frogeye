import { queryOptions, useQuery } from "@tanstack/react-query";
import { api, ApiError, type Schemas } from "@/lib/api/client";
import { formatCount, plural } from "@/lib/format";

/*
 * The evaluation results (`GET /evals`): what `python -m evals.run` measured and committed with
 * the code. Read-only, the same for every role; a 404 means no results have been written yet.
 */

export type Evals = Schemas["EvalsOut"];
export type EvalSuite = Schemas["EvalSuite"];
export type EvalMetric = Schemas["EvalMetric"];
export type EvalGroup = Schemas["EvalGroup"];
export type EvalFailure = Schemas["EvalFailure"];

export function evalsQuery() {
  return queryOptions({
    queryKey: ["evals"],
    queryFn: async ({ signal }): Promise<Evals> => {
      const { data, response } = await api.GET("/api/v1/evals", { signal });
      if (!data) throw new ApiError(response.status);
      return data;
    },
    staleTime: 10 * 60_000,
  });
}

export function useEvals() {
  return useQuery(evalsQuery());
}

/** A metric with a numerator and denominator is a rate of cases; one without is a score (F1, MRR, nDCG). */
export const isRate = (m: Pick<EvalMetric, "numerator" | "denominator">) => m.numerator !== null && m.denominator !== null;

/** "64.4%", "100%" for a rate; "0.773" for a score; null is "Not computed". */
export function formatMetric(m: Pick<EvalMetric, "value" | "numerator" | "denominator">): string {
  if (m.value === null) return "Not computed";
  return isRate(m) ? formatRate(m.value) : m.value.toFixed(3);
}

/** A share to one decimal, without a trailing ".0": "64.4%", "100%", "0%". */
export const formatRate = (v: number) => `${Number((v * 100).toFixed(1))}%`;

/** "29/45" for a rate, null for a score. */
export const fraction = (m: Pick<EvalMetric, "numerator" | "denominator">) => (isRate(m) ? `${m.numerator}/${m.denominator}` : null);

const GROUP_NAMES: Record<string, string> = { hybrid_rerank: "Hybrid + rerank", keyword: "Keyword only", vector: "Vector only", hybrid: "Hybrid" };

/** A breakdown's name for reading: "hard_negative" → "Hard negative"; the retrieval modes by name. */
export function groupName(name: string): string {
  const known = GROUP_NAMES[name];
  if (known) return known;
  const words = name.replace(/_/g, " ").trim().replace(/\bjfrog\b/gi, "JFrog");
  return words.charAt(0).toUpperCase() + words.slice(1);
}

/** The metric keys a suite's groups report, in first-seen order, with each one's label. */
export function groupMetrics(groups: EvalGroup[]): { key: string; label: string }[] {
  const seen = new Map<string, string>();
  for (const g of groups) for (const m of g.metrics) if (!seen.has(m.key)) seen.set(m.key, m.label);
  return [...seen].map(([key, label]) => ({ key, label }));
}

/**
 * How a suite's breakdowns read: a bar chart when three or more groups report the same metrics
 * (the retrieval modes, the faithfulness sections), the rest as a table. A group whose name has a
 * " · " is a sub-breakdown of one of them ("hybrid · keyword queries") and goes to the table.
 */
export function splitGroups(groups: EvalGroup[]): { chart: EvalGroup[]; table: EvalGroup[] } {
  const top = groups.filter((g) => !g.name.includes(" · "));
  const rest = groups.filter((g) => g.name.includes(" · "));
  const keys = (g: EvalGroup) => g.metrics.map((m) => m.key).join(",");
  const same = top.length >= 3 && top.every((g) => keys(g) === keys(top[0]!) && g.metrics.every((m) => m.value !== null));
  return same ? { chart: top, table: rest } : { chart: [], table: groups };
}

/** The "hybrid · " every group of a sub-breakdown starts with, or undefined when they do not share one. */
export function sharedPrefix(groups: EvalGroup[]): string | undefined {
  const heads = groups.map((g) => (g.name.includes(" · ") ? g.name.slice(0, g.name.indexOf(" · ") + 3) : null));
  return heads.length > 0 && heads[0] && heads.every((h) => h === heads[0]) ? heads[0] : undefined;
}

/** The page's lead: what was measured and what went wrong, in one sentence. */
export function evalsLead(data: Evals): string {
  const cases = data.suites.reduce((n, s) => n + s.cases, 0);
  const failures = data.suites.reduce((n, s) => n + s.failures.length, 0);
  const wrong = failures === 0 ? "every scored case matched its label" : `the app got ${formatCount(failures)} wrong, each listed under its suite`;
  const stale = data.stale_cases > 0 ? ` ${plural(data.stale_cases, "case was", "cases were")} not recorded and left out of the metrics.` : "";
  return `How accurate FrogEye's models and guards are, measured on ${plural(cases, "labelled case")} in ${plural(data.suites.length, "suite")}; ${wrong}.${stale}`;
}
