import type { TimeSeries } from "@/components/charts/scales";
import { nameFromSlug } from "@/lib/competitors";
import { dayLabel, dayMonth, weightWords } from "@/features/intel/words";
import { daysBetween } from "@/lib/days";
import { formatCount, formatPercent, plural } from "@/lib/format";
import { clampFigure, firstBaselineDay, ratio, trendWord, youngCollection, type IntelRules } from "@/lib/intelReference";
import { formatCalendarDay, plainDay } from "@/lib/time";
import { formatMoney } from "./useCompetitorIntel";
import type { Fact, FactKind, FactSource, Posting } from "./useCompetitorProfile";

/*
 * The deep dive's words: how a note, a fact, a source and a posting read, the printed summaries
 * of its charts, and the "How this is computed" formulas. Pure functions of the API's data; the
 * data and series helpers are in useCompetitorProfile.
 */

/** A momentum sparkline's name: "Momentum, last 13 weeks", the weeks it spans, which start where collection began. */
export const momentumLabel = (weeks: unknown[] | null, fallback: number) => `Momentum, last ${plural(weeks?.length || fallback, "week")}`;

// ---- facts ---------------------------------------------------------------------------------------

/** "18%", "1,250 employees", "$45M"; null when the source states no figure. */
export function factValue(f: { value: number | null; unit: string | null; currency: string | null }): string | null {
  if (f.value === null) return null;
  if (f.currency) return formatMoney(f.value, f.currency);
  if (f.unit === "%") return `${formatCount(f.value)}%`;
  if (f.unit) return `${formatCount(f.value)} ${f.unit}`;
  return formatCount(f.value);
}

export const KIND_LABEL: Record<FactKind, string> = {
  funding_round: "Funding round",
  valuation: "Valuation",
  headcount: "Headcount",
  layoff: "Layoff",
  revenue: "Revenue",
  arr: "ARR",
  customer: "Customer",
  customer_count: "Customer count",
  market_share: "Market share",
  acquisition: "Acquisition",
  leadership_change: "Leadership change",
  partnership: "Partnership",
  office: "Office",
};

/** A fact in one line, in the source's words: "Series B · $35M", "1,250 employees", "Partnership · AWS". */
export function factHeadline(f: Pick<Fact, "kind" | "label" | "value" | "unit" | "currency">): string {
  const value = factValue(f);
  switch (f.kind) {
    case "funding_round":
      return `${f.label} · ${value ?? "amount not disclosed"}`;
    case "valuation":
      return `Valuation · ${value ?? "not stated"}`;
    case "headcount":
      return value === null ? "Headcount not stated" : f.unit ? value : `${value} employees`;
    case "layoff":
      return `Layoff · ${value ?? "size not stated"}`;
    case "revenue":
      return value === null ? `Revenue, ${f.label}` : `${value} revenue${f.label && f.label.toLowerCase() !== "revenue" ? `, ${f.label}` : ""}`;
    case "arr":
      return value === null ? "ARR not stated" : `${value} ARR`;
    case "customer":
      return f.label;
    case "customer_count":
      return value ?? f.label;
    case "market_share":
      return value === null ? f.label : `${value} · ${f.label}`;
    case "acquisition":
      return `Acquisition · ${f.label}${value ? ` · ${value}` : ""}`;
    default:
      return `${KIND_LABEL[f.kind]} · ${f.label}`;
  }
}

/** Who says it: the outlet, else the page's own title ("Sonatype about page"). */
export const sourceName = (s: FactSource) => s.outlet?.trim() || s.title;

/** When the source said it: its publication (or fetch) day, else the day it gives. */
export function sourceDay(s: FactSource): string | null {
  if (s.published_at) return formatCalendarDay(s.published_at);
  return s.as_of ? plainDay(s.as_of, "date") : null;
}

// ---- printed summaries -------------------------------------------------------------------------------

const sum = (points: { y: number }[]) => points.reduce((s, p) => s + p.y, 0);

/** "Sonatype drew 30% of the articles about it and its 3 closest peers over 13 weeks (461 of 1,524); GitHub drew the most (638)." */
export function voiceSummary(series: TimeSeries[], metric: "documents" | "signals"): string {
  const noun = metric === "documents" ? "articles" : "public signals";
  const own = series[0]!;
  const totals = series.map((s) => ({ label: s.label, total: sum(s.points) }));
  const group = totals.reduce((s, t) => s + t.total, 0);
  const leader = totals.reduce((best, t) => (t.total > best.total ? t : best), totals[0]!);
  const weeks = new Set(series.flatMap((s) => s.points.map((p) => p.x.getTime()))).size;
  const span = plural(weeks, "week");
  const peers = series.length - 1;
  const most = leader.label === own.label ? "the most of the group" : `${leader.label} drew the most (${formatCount(leader.total)})`;
  if (totals[0]!.total === 0) return group === 0 ? `No ${noun} about ${own.label} or its peers in ${span}.` : `${own.label} drew no ${noun} in ${span}; ${most}.`;
  const share = formatPercent(totals[0]!.total / group);
  return `${own.label} drew ${share} of the ${noun} about it and its ${peers === 1 ? "closest peer" : `${peers} closest peers`} over ${span} (${formatCount(totals[0]!.total)} of ${formatCount(group)}); ${most}.`;
}

/** "FrogEye has collected since 30 Sept, under the 13 weeks a trend compares", or null when the window is all collected. */
export function youngWords(collectedSince: string | null, today: string, rules: IntelRules): string | null {
  if (!collectedSince || !youngCollection(collectedSince, today, rules.trend)) return null;
  return `FrogEye has collected since ${dayMonth(collectedSince)}, under the ${rules.trend.weeks} weeks a trend compares`;
}

/**
 * "131 this week; the last 4 weeks average 1.6× the 9 before: rising." With no trend: why, in
 * words, collection too young for one or no public signal in the weeks before.
 */
export function momentumSummary(values: { value: number }[], trend: number | null, rules: IntelRules, young: string | null): string {
  const last = values.at(-1)?.value ?? 0;
  const now = `${formatCount(Math.round(last))} this week`;
  const { recent_weeks: recent, baseline_weeks: baseline } = rules.trend;
  if (trend === null) return young ? `${now}; no baseline yet: ${young}.` : `${now}; no baseline yet: no public signals in the ${baseline} weeks before the last ${recent}.`;
  return `${now}; the last ${recent} weeks average ${ratio(trend)} the ${baseline} before: ${trendWord(trend, rules.trend)}.`;
}

/** "21 public moves in 180 days, the most in Launches (7); the latest, Pricing, on 19 Oct." */
export function timelineSummary(events: { day: string; lane: string }[], days: number): string {
  const counts = new Map<string, number>();
  for (const e of events) counts.set(e.lane, (counts.get(e.lane) ?? 0) + 1);
  const [lane, n] = [...counts].reduce((best, c) => (c[1] > best[1] ? c : best));
  const latest = events.reduce((a, b) => (b.day > a.day ? b : a));
  return `${plural(events.length, "public move")} in ${plural(days, "day")}, the most in ${lane} (${formatCount(n)}); the latest, ${latest.lane}, on ${dayMonth(latest.day)}.`;
}

/**
 * "142 open now, up 12 in 30 days"; on a board read for under the change's `days` "142 open
 * now; its board has 10 days of history, since 10 Oct, so no 30-day change yet", or "its board was
 * first read today" on its first day, never its count as growth.
 */
export function openRolesSummary(open: number, delta: number | null, since: string | null, today: string, days: number): string {
  const now = `${formatCount(open)} open now`;
  if (delta === null) {
    if (!since) return `${now}; no ${days}-day change yet`;
    const age = daysBetween(since, today);
    if (age <= 0) return `${now}; its board was first read today, so no ${days}-day change yet`;
    return `${now}; its board has ${plural(age, "day")} of history, since ${dayMonth(since)}, so no ${days}-day change yet`;
  }
  if (delta === 0) return `${now}, no change in ${plural(days, "day")}`;
  return `${now}, ${delta > 0 ? "up" : "down"} ${formatCount(Math.abs(delta))} in ${plural(days, "day")}`;
}

/** The open-roles key figure's change line when there is none yet: why, and since when. */
export function noChangeYet(since: string | null, today: string, days: number): string {
  const none = `No ${days}-day change yet`;
  if (!since) return none;
  return daysBetween(since, today) <= 0 ? `${none}: its board was first read today` : `${none}: its board was first read on ${dayMonth(since)}`;
}

/** "board first read today", "board first read 10 Oct": why a young board has no change yet (the overview's `board_since`). */
export const boardFirstRead = (since: string, today: string) => (daysBetween(since, today) <= 0 ? "board first read today" : `board first read ${dayMonth(since)}`);

/** "+12 in 30 days", "−3 in 30 days": a signed change over the API's change window. */
export const changeIn = (delta: number, days: number) => `${delta > 0 ? "+" : "−"}${formatCount(Math.abs(delta))} in ${plural(days, "day")}`;

// ---- words -------------------------------------------------------------------------------------------

export const SOURCE_KIND: Record<string, string> = {
  rss: "RSS feed",
  google_news: "Google News",
  gdelt: "GDELT",
  hn: "Hacker News",
  github_releases: "GitHub releases",
  github_repo_stats: "GitHub stats",
  sec_edgar: "SEC filings",
  page_watch: "Watched page",
  job_board: "Job board",
  oss_stats: "Package downloads",
  tavily: "Web search",
};

export const sourceKindLabel = (kind: string) => SOURCE_KIND[kind] ?? nameFromSlug(kind);

/** "Security · Staff & Principal · Remote, United States". */
export function postingMeta(p: Posting): string {
  // A remote posting's board often repeats "Remote" and the country inside its location
  // ("Remote, Canada; Remote, United States"): each place is named once.
  const where = p.remote
    ? ["Remote", ...(p.location ?? "").split(/[,;]/), p.country ?? ""].map((v) => v.trim()).filter((v, i, all) => v && all.indexOf(v) === i)
    : [p.location, p.location && p.country && p.location.includes(p.country) ? null : p.country].filter(Boolean);
  return [p.department, p.seniority, (where as string[]).join(", ")].filter(Boolean).join(" · ");
}

// ---- "How this is computed": the API's own rules, served by GET /intel-reference --

export function threatFormula(rules: IntelRules): string {
  const { threat: t, trend } = rules;
  const days = t.hiring_history_days;
  return `The score is overlap × a momentum factor × a hiring factor. The momentum factor is the trend (the last ${trend.recent_weeks} weeks over the ${trend.baseline_weeks} before), held between ${clampFigure(t.momentum_factor_min)} and ${clampFigure(t.momentum_factor_max)}, or 1 with no baseline yet; the hiring factor is 1 plus the ${days}-day change in open roles as a share, held within ±${clampFigure(t.hiring_factor_bound)}, or 1 without a job board listed in full or before the board has ${plural(days, "day")} of history. The level is high from a score of ${t.high_score}, medium from ${t.medium_score}, else low; it is insufficient data with fewer than ${t.min_signals} public signals in ${plural(t.lookback_days, "day")} and no job board listed in full, and then nothing is scored.`;
}

export function overlapFormula(rules: IntelRules): string {
  const o = rules.overlap;
  return `Each product's evidence is the sum of the severity weights (${weightWords(rules.severity_weights)}) of the JFrog-impact entries that name one of its JFrog counterparts, in the current analyses of its public signals over the last ${plural(o.lookback_days, "day")}. High from ${o.high_evidence}, medium from ${o.medium_evidence}, low from 1; with none it reads “No recent evidence”, since the seed already says the products compete.`;
}

export function momentumFormula(rules: IntelRules): string {
  const t = rules.trend;
  return `Each week is the sum of the importance of its public signals that week (7-day windows ending today). The trend is the mean of the last ${t.recent_weeks} weeks over the mean of the ${t.baseline_weeks} weeks before, at most ${ratio(t.cap)}: rising from ${ratio(t.rising_ratio)}, easing at ${ratio(t.easing_ratio)} or less, steady between. With no public signals in those ${t.baseline_weeks} weeks there is no baseline yet, and there is none until all ${t.weeks} weeks have been collected.`;
}

/** In "How this is computed": where the weeks start, and the first day a trend can read. */
export function collectedLine(collectedSince: string, rules: IntelRules): string {
  return `FrogEye has collected since ${dayLabel(collectedSince)}, so its weeks start then: a trend needs all ${rules.trend.weeks} collected, from ${dayLabel(firstBaselineDay(collectedSince, rules.trend))}.`;
}
