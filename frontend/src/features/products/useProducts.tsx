import { queryOptions, useQuery } from "@tanstack/react-query";
import { dateDay, dayDate, type TimeSeries } from "@/components/charts/scales";
import { api, ApiError, type Schemas } from "@/lib/api/client";
import { formatCount, plural } from "@/lib/format";
import { ratio, type IntelRules } from "@/lib/intelReference";
import { productRank } from "@/lib/products";
import { dayMonth, weightWords } from "@/features/intel/words";
import { densify } from "@/features/intel/series";

/*
 * The JFrog product deep dives' data (`GET /products`, `GET /products/{slug}`, the same for every
 * persona) and how it reads: the list's order and lead, the printed summaries of the
 * charts, the key figures and the "How this is computed" formulas. Nothing is measured here:
 * every figure is the API's, a value it does not serve stays unmeasured, and the page never shows
 * a JFrog company figure: only the competitive landscape around each product. The windows
 * and thresholds are the API's (GET /intel-reference). `pressure_baseline` false means
 * the pressure has no trend yet, for one of two reasons the pages keep apart: FrogEye has
 * collected for less than the trend's weeks (`young`, the deployment's), or the baseline
 * weeks name no threat (the product's own; as a competitor's momentum). Either reads "no
 * baseline yet", never "rising"; the young one over the weeks collected.
 */

export type ProductSummary = Schemas["ProductSummary"];
export type ProductProfile = Schemas["ProductProfile"];
export type Pressure = ProductSummary["pressure"];
export type ProductCompetitor = ProductProfile["competitors"][number];
export type ProductEvent = ProductProfile["timeline"][number];
export type HiringArea = ProductProfile["hiring_in_area"];

// ---- reads -----------------------------------------------------------------------------------

export function productsQuery() {
  return queryOptions({
    queryKey: ["products"],
    queryFn: async ({ signal }): Promise<ProductSummary[]> => {
      const { data, response } = await api.GET("/api/v1/products", { signal });
      if (!data) throw new ApiError(response.status);
      return data;
    },
    staleTime: 5 * 60_000,
  });
}

export function useProducts() {
  return useQuery(productsQuery());
}

export function productQuery(slug: string) {
  return queryOptions({
    queryKey: ["product", slug],
    queryFn: async ({ signal }): Promise<ProductProfile> => {
      const { data, response } = await api.GET("/api/v1/products/{slug}", { params: { path: { slug } }, signal });
      if (!data) throw new ApiError(response.status);
      return data;
    },
    staleTime: 5 * 60_000,
  });
}

export function useProduct(slug: string) {
  return useQuery(productQuery(slug));
}

// ---- words -----------------------------------------------------------------------------------

/** "A", "A or B", "A, B or C" (`and` for a list of things that are all true). */
export function joinWords(words: string[], last = "and"): string {
  if (words.length <= 1) return words[0] ?? "";
  return `${words.slice(0, -1).join(", ")} ${last} ${words.at(-1)}`;
}

const points = new Intl.NumberFormat("en-US", { maximumFractionDigits: 1 });
/** Severity-weighted pressure points: "6.5", "40", "1.6". */
export const formatPoints = (n: number) => points.format(n);

// ---- the list -------------------------------------------------------------------------

/** A product's pressure as the page reads it: the API's word, or "baseline" for pressure with no baseline yet. */
export type PressureLook = Pressure | "baseline";
export const pressureLook = (p: Pick<ProductSummary, "pressure" | "pressure_baseline">): PressureLook =>
  p.pressure !== "none" && !p.pressure_baseline ? "baseline" : p.pressure;

export const PRESSURE_ORDER: readonly PressureLook[] = ["rising", "baseline", "steady", "easing", "none"];

/** Most pressed first (rising, steady, easing, then none), the portfolio's order (the API's, `order`) within each. */
export function byPressure(rows: ProductSummary[], order: readonly string[]): ProductSummary[] {
  return [...rows].sort((a, b) => PRESSURE_ORDER.indexOf(pressureLook(a)) - PRESSURE_ORDER.indexOf(pressureLook(b)) || productRank(a.name, order) - productRank(b.name, order));
}

/** The pressure's window in words: "in 13 weeks", or "since 30 Sept" while collection is younger than it. */
export function windowWords(young: boolean, since: string | null, rules: IntelRules): string {
  return young && since ? `since ${dayMonth(since)}` : `in ${plural(rules.trend.weeks, "week")}`;
}

/** Why a pressed product has no baseline on a fully collected deployment: its baseline weeks name no threat. */
export const emptyBaseline = (rules: IntelRules) => `no threat in the ${rules.trend.baseline_weeks} weeks before the last ${rules.trend.recent_weeks}`;

/**
 * The list's lead: "13 JFrog products. 3 are under rising competitive pressure, 4 steady and 2
 * easing; 4 have seen no competitive pressure in 13 weeks." A product whose baseline weeks name no
 * threat is counted apart ("1 faces pressure with no baseline yet"). On a young deployment
 * (`young`): "13 JFrog products. 9 face competitive pressure, with no baseline yet to say
 * whether it is rising or easing; 4 have seen none since 30 Sept."
 */
export function listLead(rows: ProductSummary[], rules: IntelRules, young: boolean): string {
  const count = (look: PressureLook) => rows.filter((r) => pressureLook(r) === look).length;
  const none = count("none");
  const all = plural(rows.length, "JFrog product");
  const period = windowWords(young, rules.collected_since, rules);
  if (rows.length === 0) return "No JFrog products are listed yet.";
  if (none === rows.length) return `${all}; none has seen competitive pressure ${period}.`;
  if (young) {
    const pressed = rows.length - none;
    const quiet = none > 0 ? `; ${formatCount(none)} ${none === 1 ? "has" : "have"} seen none ${period}` : "";
    return `${all}. ${formatCount(pressed)} ${pressed === 1 ? "faces" : "face"} competitive pressure, with no baseline yet to say whether it is rising or easing${quiet}.`;
  }
  // The first level present carries the verb: "3 are under rising competitive pressure, 4 steady and 2 easing".
  const levels = (["rising", "steady", "easing"] as const).map((p) => [p, count(p)] as const).filter(([, n]) => n > 0);
  const [first, ...rest] = levels;
  const lead = first ? `${formatCount(first[1])} ${first[1] === 1 ? "is" : "are"} under ${first[0]} competitive pressure` : null;
  const pressed = lead && rest.length ? `${lead}, ${joinWords(rest.map(([p, n]) => `${formatCount(n)} ${p}`))}` : lead;
  const based = count("baseline");
  const unbased = based > 0 ? `${formatCount(based)} ${based === 1 ? "faces" : "face"} pressure with no baseline yet` : null;
  const quiet = none > 0 ? `${formatCount(none)} ${none === 1 ? "has" : "have"} seen no competitive pressure ${period}` : null;
  return `${all}. ${[pressed, unbased, quiet].filter(Boolean).join("; ")}.`;
}

// ---- pressure --------------------------------------------------------------

type Weeks = { threat: number; opportunity: number }[];

/** The mean weekly threat of the trend's recent weeks and of the baseline weeks before them (null without those weeks). */
export function threatMeans(weeks: { threat: number }[], rules: IntelRules): { recent: number; prior: number | null } {
  const mean = (xs: number[]) => (xs.length ? xs.reduce((s, x) => s + x, 0) / xs.length : null);
  const n = rules.trend.recent_weeks;
  const recent = mean(weeks.slice(-n).map((w) => w.threat)) ?? 0;
  return { recent, prior: mean(weeks.slice(0, -n).map((w) => w.threat)) };
}

/** "the last 4 weeks", or "the 3 weeks collected" on a young deployment whose weeks are fewer. */
function recentSpan(weeks: Weeks, rules: IntelRules, young: boolean): string {
  const n = Math.min(rules.trend.recent_weeks, weeks.length);
  return young && n < rules.trend.recent_weeks ? `the ${plural(n, "week")} collected` : `the last ${plural(n, "week")}`;
}

const opportunityOf = (weeks: Weeks) => formatPoints(weeks.reduce((s, w) => s + w.opportunity, 0));

/** The "How this is computed" line: "6.5 threat a week over the last 4 weeks, 1.6 in the 9 before". */
export function pressureLine(weeks: Weeks, rules: IntelRules, baseline: boolean, young: boolean, since: string | null): string {
  const period = windowWords(young, since, rules);
  if (weeks.length === 0) return `Nothing named it as a threat or an opportunity ${period}`;
  if (!weeks.some((w) => w.threat > 0)) return `No threat ${period}; opportunity ${opportunityOf(weeks)} in all`;
  const { recent, prior } = threatMeans(weeks, rules);
  const span = recentSpan(weeks, rules, young);
  const b = rules.trend.baseline_weeks;
  if (!baseline) return `${formatPoints(recent)} threat a week over ${span}; ${young ? "no baseline yet" : `none in the ${b} before, so no baseline yet`}`;
  return `${formatPoints(recent)} threat a week over ${span}, ${prior ? `${formatPoints(prior)} in the ${b} before` : `none in the ${b} before`}`;
}

/**
 * The pressure key figure, as a value and its line: "6.5 a week" over "threat over the last 4
 * weeks; 1.6 in the 9 before" (the pressure word itself is in the page's header); on a young
 * deployment "threat over the 3 weeks collected; no baseline yet".
 */
export function pressureFigure(weeks: Weeks, rules: IntelRules, baseline: boolean, young: boolean, since: string | null): { value: string; sub: string; measured: boolean } {
  const period = windowWords(young, since, rules);
  if (weeks.length === 0) return { value: `None ${period}`, sub: "No signal named it as a threat or an opportunity", measured: false };
  if (!weeks.some((w) => w.threat > 0)) return { value: `No threat ${period}`, sub: `opportunity ${opportunityOf(weeks)} in all`, measured: false };
  const { recent, prior } = threatMeans(weeks, rules);
  const span = recentSpan(weeks, rules, young);
  const b = rules.trend.baseline_weeks;
  const before = !baseline
    ? young
      ? "no baseline yet"
      : `none in the ${b} before, so no baseline yet`
    : prior
      ? `${formatPoints(prior)} in the ${b} before`
      : `none in the ${b} before`;
  return { value: `${formatPoints(recent)} a week`, sub: `threat over ${span}; ${before}`, measured: true };
}

/**
 * The pressure chart's summary: "Threat 40 against opportunity 10 over 13 weeks; the last 4 weeks
 * average 6.5 threat a week, against 1.6 in the 9 before: rising." The word is the API's. With no
 * baseline yet the summary gives the weeks collected and no word.
 */
export function pressureSummary(weeks: Weeks, pressure: Pressure, rules: IntelRules, baseline: boolean, young: boolean): string {
  const threat = weeks.reduce((s, w) => s + w.threat, 0);
  const opportunity = weeks.reduce((s, w) => s + w.opportunity, 0);
  const span = young ? `the ${plural(weeks.length, "week")} collected` : plural(weeks.length, "week");
  if (threat === 0) return `No threat in ${span}; opportunity ${formatPoints(opportunity)} in all.`;
  const totals = `Threat ${formatPoints(threat)} against opportunity ${formatPoints(opportunity)} over ${span}`;
  if (!baseline && young) return `${totals}; no baseline yet to call it rising or easing.`;
  // everything was collected, but the baseline weeks name no threat: as a momentum trend, no baseline yet.
  if (!baseline) return `${totals}; ${emptyBaseline(rules)}, so no baseline yet to call it rising or easing.`;
  const { recent, prior } = threatMeans(weeks, rules);
  const b = rules.trend.baseline_weeks;
  const before = prior ? `against ${formatPoints(prior)} in the ${b} before` : `with none in the ${b} before`;
  return `Threat ${formatPoints(threat)} against opportunity ${formatPoints(opportunity)} over ${span}; the last ${plural(rules.trend.recent_weeks, "week")} average ${formatPoints(recent)} threat a week, ${before}: ${pressure}.`;
}

// ---- competitors -------------------------------------------------------------------------------

/** "1 high, 2 medium and 1 low threat", from the levels with any competitor. */
export function threatMix(competitors: ProductCompetitor[]): string | null {
  const levels = (["high", "medium", "low"] as const).map((l) => [l, competitors.filter((c) => c.threat_level === l).length] as const).filter(([, n]) => n > 0);
  if (levels.length === 0) return null;
  return `${joinWords(levels.map(([l, n]) => `${formatCount(n)} ${l}`))} threat`;
}

/** "4 of 5 competitors show recent evidence against Artifactory; Sonatype shows the most (evidence 14)." */
export function competitorsSummary(competitors: ProductCompetitor[], product: string, nameOf: (slug: string) => string, days: number): string {
  const live = competitors.filter((c) => c.evidence > 0);
  const n = competitors.length;
  if (live.length === 0) return `None of the ${plural(n, "competitor")} mapped to ${product} shows recent evidence against it in ${plural(days, "day")}.`;
  const top = live.reduce((a, b) => (b.evidence > a.evidence ? b : a));
  const who = live.length === n ? (n === 1 ? "Its one competitor" : `All ${n} competitors`) : `${live.length} of ${n} competitors`;
  const verb = live.length === 1 ? "shows" : "show";
  const most = live.length === 1 ? ` (evidence ${formatCount(top.evidence)})` : `; ${nameOf(top.competitor_slug)} shows the most (evidence ${formatCount(top.evidence)})`;
  return `${who} ${verb} recent evidence against ${product}${most}.`;
}

// ---- moves -----------------------------------------------------------------------------------

/** The public signals naming it in 180 days (one move can be about two competitors: counted once). */
export const signalCount = (timeline: ProductEvent[]) => new Set(timeline.map((e) => e.signal_id)).size;

/** The swimlane's lanes: its competitors in the table's order, then any other competitor a move names. */
export function laneKeys(profile: ProductProfile): string[] {
  const keys = profile.competitors.map((c) => c.competitor_slug);
  for (const e of profile.timeline) if (!keys.includes(e.competitor_slug)) keys.push(e.competitor_slug);
  return keys;
}

/** "11 public moves name Artifactory in 180 days, the most from GitHub (4); the latest, from Sonatype, on 19 Oct." */
export function movesSummary(timeline: ProductEvent[], product: string, nameOf: (slug: string) => string, days: number): string {
  const counts = new Map<string, number>();
  for (const e of timeline) counts.set(e.competitor_slug, (counts.get(e.competitor_slug) ?? 0) + 1);
  const [top, n] = [...counts].reduce((best, c) => (c[1] > best[1] ? c : best));
  const latest = timeline.reduce((a, b) => (b.day > a.day ? b : a));
  const moves = signalCount(timeline);
  const most = counts.size === 1 ? `all from ${nameOf(top)}` : `the most from ${nameOf(top)} (${formatCount(n)})`;
  return `${plural(moves, "public move")} ${moves === 1 ? "names" : "name"} ${product} in ${plural(days, "day")}, ${most}; the latest, from ${nameOf(latest.competitor_slug)}, on ${dayMonth(latest.day)}.`;
}

// ---- the brief -----------------------------------------------------------------------------------

/** Why there is no brief yet, as a note's title and its hint: a brief needs the API's `briefs.min_signals`. */
export function noBrief(profile: ProductProfile, rules: IntelRules): { title: string; hint: string } {
  const n = signalCount(profile.timeline);
  const { min_signals: needed, lookback_days: days } = rules.briefs;
  const period = `in the last ${plural(days, "day")}`;
  if (n >= needed) return { title: "No brief has been written yet", hint: `Briefs are written weekly; ${plural(n, "public signal")} ${n === 1 ? "names" : "name"} ${profile.name} ${period}.` };
  const named = n === 0 ? `No public signal names ${profile.name}` : `${plural(n, "public signal")} ${n === 1 ? "names" : "name"} ${profile.name}`;
  return { title: "Not enough evidence for a brief yet", hint: `${named} ${period} (${needed} needed).` };
}

// ---- hiring in its area ---------------------------------------------------------------------

/** The competitors the area chart names (the kit's four colours); the rest are one "Other". */
export const AREA_NAMED = 4;
const NUMBER_WORDS = ["one", "two", "three", "four"];
export const AREA_OTHER = "other";

/**
 * The area chart's lines: every competitor hiring for it, each from its board's first
 * snapshot as the API serves it (densified once it appears; absent before, never a zero
 * nobody measured), ranked by its open roles today, then by name. The first four are named, so a
 * young board with the most roles today is named, never folded. The rest are ONE "Other" built
 * here, not by the kit: their sum from the first day every one of them is measured (the latest
 * member's first snapshot), so it never steps up on a member's first read. Its members keep
 * their own lines for the legend and the table twin.
 */
export function areaSeries(area: HiringArea, nameOf: (slug: string) => string): TimeSeries[] {
  const days = area.total.map((t) => t.day);
  const dense = densify(
    area.by_competitor.map((p) => ({ x: p.day, key: p.key, y: p.count })),
    days,
  );
  const today = (pts: { y: number }[]) => pts.at(-1)?.y ?? 0;
  const ranked = [...dense].sort(([ka, a], [kb, b]) => today(b) - today(a) || nameOf(ka).localeCompare(nameOf(kb)));
  const lines = ranked.map(([key, pts]) => ({ key, label: nameOf(key), points: pts.map((p) => ({ x: dayDate(p.x), y: p.y })) }));
  if (lines.length <= AREA_NAMED) return lines;

  const members = lines.slice(AREA_NAMED).sort((a, b) => a.label.localeCompare(b.label));
  const from = Math.max(...members.map((m) => m.points[0]!.x.getTime()));
  const sums = new Map<number, number>();
  for (const m of members) for (const p of m.points) if (p.x.getTime() >= from) sums.set(p.x.getTime(), (sums.get(p.x.getTime()) ?? 0) + p.y);
  const points = [...sums].sort(([a], [b]) => a - b).map(([x, y]) => ({ x: new Date(x), y }));
  return [...lines.slice(0, AREA_NAMED), { key: AREA_OTHER, label: "Other", points, members }];
}

/**
 * The area chart's footnote on where lines start: a drawn line whose board was first read inside
 * the window starts there, and Other starts on the first day all its boards are read.
 */
export function areaStarts(series: TimeSeries[], nameOf: (slug: string) => string, windowStart: string | undefined): string {
  const start = windowStart ? dayDate(windowStart).getTime() : -Infinity;
  const late = series.filter((s) => !s.members && s.points[0] && s.points[0].x.getTime() > start);
  const other = series.find((s) => s.members);
  const parts: string[] = [];
  if (late.length === 1) parts.push(`${late[0]!.label}'s line starts on ${dayMonth(dateDay(late[0]!.points[0]!.x))}, its board's first snapshot.`);
  if (late.length > 1) parts.push(`Lines start on their boards' first snapshots (${joinWords(late.map((s) => `${s.label}'s on ${dayMonth(dateDay(s.points[0]!.x))}`))}).`);
  if (other?.members && other.points[0]) {
    const names = joinWords(other.members.map((m) => nameOf(m.key)));
    const n = other.members.length;
    const otherLate = other.points[0].x.getTime() > start;
    const from = dayMonth(dateDay(other.points[0].x));
    // one folded board is named as one, never "all 1 boards".
    if (n === 1) parts.push(otherLate ? `Other (1) is ${names}, from ${from}, its board's first snapshot.` : `Other (1) is ${names}.`);
    else parts.push(otherLate ? `Other (${n}) sums ${names} from ${from}, the first day ${n === 2 ? "both" : `all ${n}`} boards are read.` : `Other (${n}) sums ${names}.`);
  }
  if (late.length > 0 || (other?.points[0] && other.points[0].x.getTime() > start)) parts.push("So no line rises because a board was first read.");
  return parts.join(" ");
}

/**
 * The area chart's footnote: what is counted, who is named (the four with the most open roles
 * today when some fold into Other, else every competitor) and where the lines start.
 */
export function areaNote(series: TimeSeries[], caps: string[], nameOf: (slug: string) => string, windowStart: string | undefined): string {
  const counted = `Open postings on competitors' public job boards that name ${joinWords(caps, "or")}, each counted once a day`;
  const folded = series.some((s) => s.members);
  const named = folded ? `the ${NUMBER_WORDS[AREA_NAMED - 1]} with the most open roles today are named` : "each competitor hiring for it is named";
  const starts = areaStarts(series, nameOf, windowStart) || "Each line starts on its board's first snapshot.";
  return `${counted}; ${named}. ${starts}`;
}

/**
 * On the day every board was first read (one snapshot) there is no line over
 * time yet: the area reads as today's open roles by competitor, and its footnote says when the
 * line begins.
 */
export function areaTodayNote(caps: string[]): string {
  return `Open postings on competitors' public job boards that name ${joinWords(caps, "or")}, counted today. Every board was first read today, so the line over time starts with the next daily snapshot.`;
}

/** Today's open roles in the area and the competitors that hold them; null when nothing is measured. */
export function areaToday(area: HiringArea): { open: number; competitors: { slug: string; open: number }[] } | null {
  const day = area.total.at(-1)?.day;
  if (!day) return null;
  const competitors = area.by_competitor.filter((p) => p.day === day && p.count > 0).map((p) => ({ slug: p.key, open: p.count }));
  return { open: area.total.at(-1)!.count, competitors: competitors.sort((a, b) => b.open - a.open || a.slug.localeCompare(b.slug)) };
}

/** "29 open roles name its capabilities today, across 6 competitors; Sonatype has the most (14)." */
export function areaSummary(today: NonNullable<ReturnType<typeof areaToday>>, nameOf: (slug: string) => string): string {
  const roles = `${plural(today.open, "open role")} ${today.open === 1 ? "names" : "name"} its capabilities today`;
  const [top] = today.competitors;
  if (!top) return `No open role names its capabilities today.`;
  if (today.competitors.length === 1) return `${roles}, all on ${nameOf(top.slug)}'s board.`;
  return `${roles}, across ${plural(today.competitors.length, "competitor")}; ${nameOf(top.slug)} has the most (${formatCount(top.open)}).`;
}

// ---- "How this is computed": the API's own rules, served by GET /intel-reference ---------

export function pressureFormula(rules: IntelRules): string {
  const t = rules.trend;
  return `Each week sums the severity weights (${weightWords(rules.severity_weights)}) of the JFrog-impact entries that name the product in the current analyses of its public signals: threat entries below the baseline, opportunity entries above it. A signal counts once however many competitors it is about, and a neutral entry is not pressure. Weeks are 7-day windows ending today. The pressure word reads threat only: the mean of the last ${t.recent_weeks} weeks over the mean of the ${t.baseline_weeks} before is rising from ${ratio(t.rising_ratio)}, easing at ${ratio(t.easing_ratio)} or less, steady between; with no threat in ${plural(t.weeks, "week")} there is no competitive pressure. With no threat in the ${t.baseline_weeks} weeks before the last ${t.recent_weeks} there is no baseline yet: the page says so instead of a word. Until all ${t.weeks} weeks have been collected there is no baseline either.`;
}

export function competitorsFormula(product: string, rules: IntelRules): string {
  const o = rules.overlap;
  return `A competitor is listed when FrogEye's seed maps one of its products to ${product}, or when its public signals name ${product} in the last ${plural(o.lookback_days, "day")}. Its evidence is the sum of the severity weights (${weightWords(rules.severity_weights)}) of the JFrog-impact entries naming ${product} in the current analyses of those signals, and its threat to ${product} counts the threat entries only: high from ${o.high_evidence}, medium from ${o.medium_evidence}, low from 1. A seeded competitor with no evidence reads “No recent evidence”, and one whose evidence names no threat reads “No threat named”.`;
}
