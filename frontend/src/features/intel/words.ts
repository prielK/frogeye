import { dateDay } from "@/components/charts/scales";
import { nameFromSlug } from "@/lib/competitors";
import { plural } from "@/lib/format";
import { collectedWithin, type IntelRules } from "@/lib/intelReference";
import { plainDay } from "@/lib/time";

/*
 * Words both deep dives share: how a day, a week, the severity weights, a
 * capability and a brief's evidence read, and the swimlanes' young-deployment line. The
 * competitor's own words are in features/competitors/profileWords, the product's in useProducts.
 */

const DATE_IN_TEXT = /\b(\d{1,2}) (Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sept?|Oct|Nov|Dec)\b(?: (\d{4})\b)?/g;

/** "since 15 Oct 2026" keeps its date on one line: the date's spaces become no-break spaces. */
export const keepDates = (text: string) => text.replace(DATE_IN_TEXT, (_, d: string, m: string, y?: string) => (y ? `${d}\u00a0${m}\u00a0${y}` : `${d}\u00a0${m}`));

/** "17 Oct 2026" for a plain day. */
export const dayLabel = (day: string) => plainDay(day, "date");
/** "17 Oct" for a plain day. */
export const dayMonth = (day: string) => plainDay(day, "dayMonth");
/** "Week of 14 Sept": a weekly point's tooltip and table day. */
export const weekOf = (d: Date) => `Week of ${dayMonth(dateDay(d))}`;

/** "high 3, medium 2, low 1": the severity weights, as the API serves them. */
export const weightWords = (w: IntelRules["severity_weights"]) => `high ${w.high}, medium ${w.medium}, low ${w.low}`;

/** A posting's tag as its capability's name ("model-scanning" → "Model scanning"). */
export const capabilityName = (slug: string, names: Map<string, string>) => names.get(slug) ?? nameFromSlug(slug);

/** "from 9 signals, 3 facts, 2 postings and 1 page change". */
export function evidenceLine(evidence: Record<string, number>): string | null {
  const words: [string, string, string][] = [
    ["signals", "signal", "signals"],
    ["facts", "fact", "facts"],
    ["postings", "posting", "postings"],
    ["pages", "page change", "page changes"],
  ];
  const parts = words.filter(([k]) => (evidence[k] ?? 0) > 0).map(([k, one, many]) => plural(evidence[k]!, one, many));
  if (parts.length === 0) return null;
  return `from ${parts.length === 1 ? parts[0] : `${parts.slice(0, -1).join(", ")} and ${parts.at(-1)}`}`;
}

/**
 * A swimlane's word on a young deployment: collection began inside its window, so the lanes
 * before that day are not quiet; null when the whole window was collected.
 */
export function lanesNote(collectedSince: string | null, today: string, days: number): string | null {
  if (!collectedSince || !collectedWithin(collectedSince, today, days)) return null;
  return `FrogEye has collected since ${dayLabel(collectedSince)}; before then the lanes hold only moves its sources still listed when it began.`;
}
