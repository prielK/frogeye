import { keepPreviousData, useQuery } from "@tanstack/react-query";
import type { SignalCard } from "@/components/signal/query";
import { api, ApiError, type Schemas } from "@/lib/api/client";
import { agentRunsQuery, costsQuery } from "@/lib/api/queries";
import type { Edition } from "@/lib/roles";

export type BriefingOut = Schemas["BriefingOut"];
export type BriefingSummary = Schemas["BriefingSummary"];
export type AgentRunOut = Schemas["AgentRunOut"];

/** "2026-10-17": the shape of a briefing date in the URL and the API. */
export function isBriefingDate(value: string | undefined): value is string {
  return Boolean(value && /^\d{4}-\d{2}-\d{2}$/.test(value) && !Number.isNaN(Date.parse(`${value}T00:00:00`)));
}

async function fetchBriefing(date: string | undefined, signal: AbortSignal): Promise<BriefingOut> {
  const { data, response } = date
    ? await api.GET("/api/v1/briefings/{date}", { params: { path: { date } }, signal })
    : await api.GET("/api/v1/briefings/latest", { signal });
  if (!data) throw new ApiError(response.status);
  return data;
}

/**
 * One day's briefing in the reader's own edition (the API serves only that one; `edition`
 * keys the cache, so another persona never reads this one's): the latest when `date` is
 * omitted. A 404 (no briefing yet, or none that day) is an ApiError the page reads as a
 * state, not a failure. While another date loads, the one on screen stays (dimmed by the page).
 */
export function useBriefing(date: string | undefined, edition: Edition) {
  return useQuery({
    queryKey: ["briefings", date ?? "latest", edition],
    queryFn: ({ signal }) => fetchBriefing(date, signal),
    placeholderData: keepPreviousData,
  });
}

/** Recent briefing dates in the reader's own edition (`edition` keys the cache), newest first: date navigation and "the last full briefing". */
export function useBriefingList(edition: Edition) {
  return useQuery({
    queryKey: ["briefings", "list", edition],
    queryFn: async ({ signal }) => {
      const { data, response } = await api.GET("/api/v1/briefings", { params: { query: { limit: 60 } }, signal });
      if (!data) throw new ApiError(response.status);
      return data;
    },
  });
}

/** True when an agent run recorded that it ran without an OpenAI key (notes are "; "-joined flags). */
export function isLlmDisabled(run: Pick<AgentRunOut, "notes"> | null | undefined): boolean {
  return (run?.notes ?? "").split(";").some((flag) => flag.trim() === "llm_disabled");
}

/**
 * Whether AI analysis is off: the latest finished daily run says `llm_disabled`. A run still
 * going has no notes yet, so it is skipped rather than read as "on". Every role may list runs.
 */
export function useLlmDisabled(): boolean {
  const runs = useQuery(agentRunsQuery(5));
  return isLlmDisabled(runs.data?.find((r) => r.status !== "running"));
}

/** LLM spend against both caps, in USD (`GET /costs`, every role); the day is the server's local day. */
export type Budgets = { todayUsd: number; dailyBudgetUsd: number; totalUsd: number; totalBudgetUsd: number };

/** The spend and caps for the skipped-budget explanation; null while loading or when /costs failed. */
export function useBudgets(enabled: boolean): Budgets | null {
  const costs = useQuery({ ...costsQuery(), enabled });
  if (!costs.data) return null;
  const { today_usd, daily_budget_usd, total_usd, total_budget_usd } = costs.data;
  return { todayUsd: today_usd, dailyBudgetUsd: daily_budget_usd, totalUsd: total_usd, totalBudgetUsd: total_budget_usd };
}

/**
 * Which cap stopped a skipped day, as far as today's costs can tell. The backend checks the
 * total cap first (AccountedLLM), and total spend only grows, so a spent total explains the
 * skip and is the cap to raise. Otherwise the latest skip was the daily cap; an older day's
 * cause can't be read from today's figures, and without figures nothing is asserted.
 */
export function skippedCause(budgets: Budgets | null, isLatest: boolean): "total" | "daily" | "unknown" {
  if (!budgets) return "unknown";
  if (budgets.totalUsd >= budgets.totalBudgetUsd) return "total";
  return isLatest ? "daily" : "unknown";
}

/** Signal cards by id, for resolving the ids a briefing's content cites. */
export function indexSignals(signals: SignalCard[]): Map<number, SignalCard> {
  return new Map(signals.map((s) => [s.id, s]));
}

/** The newest fully written briefing before `date` (for quiet, skipped and failed days). */
export function lastFullBriefing(list: BriefingSummary[] | undefined, date: string): BriefingSummary | null {
  return list?.find((b) => b.date < date && b.status === "published") ?? null;
}

/** The dates either side of `date` in a newest-first list. */
export function neighbours(list: BriefingSummary[] | undefined, date: string) {
  const index = list?.findIndex((b) => b.date === date) ?? -1;
  if (!list || index < 0) return { older: null, newer: null };
  return { older: list[index + 1] ?? null, newer: index > 0 ? list[index - 1]! : null };
}

/** A briefing's address: the latest for null. */
export function briefingHref(date: string | null): string {
  return date ? `/briefing/${date}` : "/briefing";
}

/** The focus ring the Briefing's links share (DESIGN.md: 2px ring with offset). */
export const FOCUS_RING =
  "focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 focus-visible:ring-offset-background focus-visible:outline-none";
