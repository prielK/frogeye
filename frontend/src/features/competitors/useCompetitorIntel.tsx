import { queryOptions, useQuery } from "@tanstack/react-query";
import { api, ApiError, type Schemas } from "@/lib/api/client";
import { formatCompact, formatCount, plural } from "@/lib/format";
import type { TrendRules } from "@/lib/intelReference";
import { plainDay } from "@/lib/time";
import { shiftDay } from "@/lib/days";
import { productRank, productSlug } from "@/lib/products";

/*
 * The competitor list's data: one read (`GET /competitors/overview`, the same for every persona)
 * and everything the page derives from it on the client: the week's moves, the landscape's
 * plotted and unplotted competitors, the filters and the table's order. Nothing is measured
 * here; a value the API serves as null stays unmeasured, and the page says why.
 */

export type CompetitorSummary = Schemas["CompetitorSummary"];
export type Move = Schemas["TimelineEventOut"];
export type ThreatLevel = Schemas["ThreatOut"]["level"];

export const overviewKey = ["competitor-overview"] as const;

/** Every competitor but JFrog, by threat (high first, then score), then tier and name. */
export function overviewQuery() {
  return queryOptions({
    queryKey: overviewKey,
    queryFn: async ({ signal }) => {
      const { data, response } = await api.GET("/api/v1/competitors/overview", { signal });
      if (!data) throw new ApiError(response.status);
      return data;
    },
    staleTime: 5 * 60_000,
  });
}

export function useCompetitorOverview() {
  return useQuery(overviewQuery());
}

// ---- this week --------------------------------------------------------------------

export const WEEK_MOVES = 5;

/** The first day of the 7 local days ending `today`. */
export const weekStart = (today: string) => shiftDay(today, -6);

/**
 * The week's biggest moves: each competitor whose latest move fell in the last 7 local days,
 * most important first (then newest, then by name), at most `max`.
 */
export function thisWeek(rows: CompetitorSummary[], today: string, max = WEEK_MOVES): { competitor: CompetitorSummary; move: Move }[] {
  const start = weekStart(today);
  return rows
    .flatMap((competitor) => {
      const move = competitor.latest_move;
      return move && move.day >= start && move.day <= today ? [{ competitor, move }] : [];
    })
    .sort((a, b) => b.move.importance - a.move.importance || b.move.day.localeCompare(a.move.day) || a.competitor.name.localeCompare(b.competitor.name))
    .slice(0, max);
}

// ---- the landscape ---------------------------------------------------------

/**
 * A competitor's momentum level: the mean of its recent weekly values (each week the sum of the
 * importance of its public signals), over the trend's recent window (`recent_weeks` of the
 * overview's weeks). The weeks start where collection began, so a young deployment averages
 * the weeks it has. Defined for every scored competitor, a young one included; null only where the
 * API serves no momentum (an insufficient threat).
 */
export function momentumLevel(row: CompetitorSummary, rules: TrendRules): number | null {
  const recent = row.momentum?.slice(-rules.recent_weeks) ?? [];
  return recent.length === 0 ? null : recent.reduce((sum, w) => sum + w.value, 0) / recent.length;
}

/** How many weeks the level averages for these rows: the recent window, or fewer where collection is younger. */
export function levelWeeks(rows: CompetitorSummary[], rules: TrendRules): number {
  const served = Math.max(0, ...rows.map((r) => r.momentum?.length ?? 0));
  return served === 0 ? rules.recent_weeks : Math.min(rules.recent_weeks, served);
}

/** "111", "31", "2.5": whole numbers from 10 up, one decimal below. */
export function formatMomentum(level: number): string {
  return level >= 10 ? formatCount(Math.round(level)) : String(Number(level.toFixed(1)));
}

/**
 * Who the landscape can place: every scored competitor, by its overlap and its momentum level.
 * An insufficient threat has neither: it is listed under the chart instead. (A scored
 * row always carries both; one that did not would be listed with them rather than drawn at 0.)
 */
export function landscapeOf(rows: CompetitorSummary[], rules: TrendRules) {
  const placed = (r: CompetitorSummary) => r.threat.level !== "insufficient" && r.overlap !== null && momentumLevel(r, rules) !== null;
  return { plotted: rows.filter(placed), insufficient: rows.filter((r) => !placed(r)) };
}

/** Overlap is a sum of severity weights: a whole number in practice, one decimal at most. */
export function formatOverlap(overlap: number): string {
  return String(Number(overlap.toFixed(1)));
}

// ---- figures -------------------------------------------------------------------------------

const money = new Map<string, Intl.NumberFormat>();

/** "$154M", "€20M", "$1.1B"; an unknown or missing currency keeps the bare figure ("154M"). */
export function formatMoney(amount: number, currency: string | null): string {
  if (!currency) return formatCompact(amount);
  let format = money.get(currency);
  if (!format) {
    try {
      format = new Intl.NumberFormat("en-US", { style: "currency", currency, notation: "compact", minimumFractionDigits: 0, maximumFractionDigits: 1 });
    } catch {
      return `${formatCompact(amount)} ${currency}`;
    }
    money.set(currency, format);
  }
  return format.format(amount);
}

/**
 * The total raised and its scope: a lower bound ("≥ $1.1B") when some round
 * states no amount or another currency, with the rounds it counts and leaves out in words, and
 * how current it is: "latest Mar 2024", the newest dated round in it.
 */
export function fundingOf(total: NonNullable<CompetitorSummary["funding_total"]>): { amount: string; scope: string; bound: boolean; latest: string | null } {
  const bound = total.excluded_rounds > 0;
  const undated = total.undated_rounds > 0 ? `, incl. ${total.undated_rounds} undated` : "";
  const left = bound ? ` · ${total.excluded_rounds} not counted` : "";
  // One unit on a line: "latest Mar 2024" never leaves "2024" alone.
  const latest = total.as_of ? `latest ${plainDay(total.as_of, "monthYear")}`.replace(/ /g, "\u00a0") : null;
  return { amount: `${bound ? "≥ " : ""}${formatMoney(total.amount, total.currency)}`, scope: `${plural(total.rounds, "round")}${undated}${left}`, bound, latest };
}

export type BoardState = CompetitorSummary["board_state"];

/**
 * Why a competitor has (or has no) open-roles count, in the API's terms (`board_state`): measured;
 * a board found and not listed in full yet; a board no longer readable (stale); none.
 */
export function boardState(row: { open_roles: number | null; has_board: boolean; board_state?: BoardState }): BoardState {
  if (row.board_state) return row.board_state;
  if (row.open_roles !== null) return "measured";
  return row.has_board ? "pending" : "none";
}

export const NO_BOARD = "No public Greenhouse, Lever or Ashby board found";
/** The chart kit's shorter name for the same state, where a legend or tooltip has little room. */
export const NO_JOB_BOARD = "No job board";
export const PENDING = "First snapshot pending";
/** A board no longer listed in full, where a legend or tooltip has little room. */
export const NOT_READABLE = "Board not readable";
/** The same state in full, where there is room (the deep dive): the API's own words. */
export const BOARD_PENDING = "Job board found — first snapshot pending";

/** A competitor's relationship to JFrog, in words. */
export const RELATIONSHIP: Record<string, string> = { competitor: "Competitor", frenemy: "Frenemy", partner: "Partner" };

// ---- the page's lead -----------------------------------------------------------------------

/**
 * One sentence on who matters: "11 competitors tracked. 2 pose a high threat to JFrog and 3 a
 * medium one; 3 have too little evidence to score yet."
 */
export function leadSentence(rows: CompetitorSummary[]): string {
  const count = (level: ThreatLevel) => rows.filter((r) => r.threat.level === level).length;
  const high = count("high");
  const medium = count("medium");
  const unscored = count("insufficient");
  const tracked = `${plural(rows.length, "competitor")} tracked`;
  if (rows.length === 0) return "No competitors are tracked yet.";
  if (unscored === rows.length) return `${tracked}; none has enough evidence to score yet.`;
  const pose = (n: number) => (n === 1 ? "poses" : "pose");
  const threat =
    high > 0
      ? `${high} ${pose(high)} a high threat to JFrog${medium > 0 ? ` and ${medium} a medium one` : ""}`
      : medium > 0
        ? `None poses a high threat to JFrog; ${medium} ${pose(medium)} a medium one`
        : "None poses a high or medium threat to JFrog";
  const rest = unscored > 0 ? `; ${unscored} ${unscored === 1 ? "has" : "have"} too little evidence to score yet` : "";
  return `${tracked}. ${threat}${rest}.`;
}

// ---- filters and order (in the address, so a shared link opens the same view) ---------------

export type SortKey = "threat" | "momentum" | "open_roles" | "name";
export type SortDir = "asc" | "desc";
export type View = { tier?: number; product?: string; category?: string; sort: SortKey; dir: SortDir };

export const SORT_LABEL: Record<SortKey, string> = { threat: "Threat", momentum: "Momentum", open_roles: "Open roles", name: "Name" };
/** Each column's first direction: the most first, names A to Z. */
export const FIRST_DIR: Record<SortKey, SortDir> = { threat: "desc", momentum: "desc", open_roles: "desc", name: "asc" };
const SORT_KEYS = Object.keys(SORT_LABEL) as SortKey[];

export function parseView(params: URLSearchParams): View {
  const tier = Number(params.get("tier"));
  const sortParam = params.get("sort");
  const sort = SORT_KEYS.includes(sortParam as SortKey) ? (sortParam as SortKey) : "threat";
  const dirParam = params.get("dir");
  return {
    tier: Number.isInteger(tier) && tier > 0 ? tier : undefined,
    product: params.get("product") || undefined,
    category: params.get("category") || undefined,
    sort,
    dir: dirParam === "asc" || dirParam === "desc" ? dirParam : FIRST_DIR[sort],
  };
}

/** The view as search params: defaults left out, other params (the signal drawer's) kept. */
export function viewParams(view: View, current: URLSearchParams): URLSearchParams {
  const next = new URLSearchParams(current);
  for (const key of ["tier", "product", "category", "sort", "dir"]) next.delete(key);
  if (view.tier !== undefined) next.set("tier", String(view.tier));
  if (view.product) next.set("product", view.product);
  if (view.category) next.set("category", view.category);
  if (view.sort !== "threat") next.set("sort", view.sort);
  if (view.dir !== FIRST_DIR[view.sort]) next.set("dir", view.dir);
  return next;
}

export const hasFilters = (view: View) => view.tier !== undefined || view.product !== undefined || view.category !== undefined;

/** The view with every filter cleared, its order kept. */
export const clearFilters = (view: View): View => ({ ...view, tier: undefined, product: undefined, category: undefined });

/**
 * The view with filters the data cannot match left out: a stale or mistyped `?tier=`,
 * `?product=` or `?category=` reads as "All", never as an empty list under a blank select.
 */
export function knownView(view: View, rows: CompetitorSummary[]): View {
  const tier = view.tier !== undefined && tiersOf(rows).includes(view.tier) ? view.tier : undefined;
  const product = view.product && rows.some((r) => r.jfrog_products.some((p) => productSlug(p) === view.product)) ? view.product : undefined;
  const category = view.category && categoriesOf(rows).some((c) => c.slug === view.category) ? view.category : undefined;
  return tier === view.tier && product === view.product && category === view.category ? view : { ...view, tier, product, category };
}

/** The order in words, for the direction toggle: "highest first", "lowest first", "A to Z", "Z to A". */
export function orderWords(sort: SortKey, dir: SortDir): string {
  if (sort === "name") return dir === "asc" ? "A to Z" : "Z to A";
  return dir === "desc" ? "highest first" : "lowest first";
}

export function applyFilters(rows: CompetitorSummary[], view: View): CompetitorSummary[] {
  return rows.filter(
    (r) =>
      (view.tier === undefined || r.tier === view.tier) &&
      (!view.product || r.jfrog_products.some((p) => productSlug(p) === view.product)) &&
      (!view.category || r.product_categories.some((c) => productSlug(c) === view.category)),
  );
}

/** The tiers present, lowest first. */
export function tiersOf(rows: CompetitorSummary[]): number[] {
  return [...new Set(rows.map((r) => r.tier))].sort((a, b) => a - b);
}

/** The JFrog products someone competes with, in the API's product order (`order`; a name it lacks goes last). */
export function productsOf(rows: CompetitorSummary[], order: readonly string[]): { slug: string; name: string }[] {
  const names = new Set(rows.flatMap((r) => r.jfrog_products));
  return [...names].sort((a, b) => productRank(a, order) - productRank(b, order) || a.localeCompare(b)).map((name) => ({ slug: productSlug(name), name }));
}

/** The product categories some competitor's seeded products are in, A to Z, each keyed by its kebab-case slug. */
export function categoriesOf(rows: CompetitorSummary[]): { slug: string; name: string }[] {
  const names = new Set(rows.flatMap((r) => r.product_categories));
  return [...names].sort((a, b) => a.localeCompare(b)).map((name) => ({ slug: productSlug(name), name }));
}

/** A row's JFrog products in the API's product order. */
export const orderedProducts = (products: string[], order: readonly string[]) => [...products].sort((a, b) => productRank(a, order) - productRank(b, order));

const LEVEL_RANK: Record<Exclude<ThreatLevel, "insufficient">, number> = { high: 3, medium: 2, low: 1 };

/**
 * What a column sorts by, compared in order; null is unmeasured and always sorts last. The threat:
 * its level, then its score. Momentum: its level, the landscape's y (a trend has no baseline
 * for the first 13 weeks, so sorting by it would order nothing), then its trend.
 */
function sortValue(row: CompetitorSummary, key: Exclude<SortKey, "name">, rules: TrendRules): number[] | null {
  if (key === "threat") return row.threat.level === "insufficient" ? null : [LEVEL_RANK[row.threat.level], row.threat.score ?? 0];
  if (key === "momentum") {
    const level = momentumLevel(row, rules);
    return level === null ? null : [level, row.threat.momentum_trend ?? 0];
  }
  return row.open_roles === null ? null : [row.open_roles];
}

/** Two sort values compared element by element. */
function compareValues(a: number[], b: number[]): number {
  for (let i = 0; i < Math.max(a.length, b.length); i++) {
    const d = (a[i] ?? 0) - (b[i] ?? 0);
    if (d !== 0) return d;
  }
  return 0;
}

/**
 * The table's order. Ties keep the API's order (threat, then tier and name), and a value that was
 * never measured (no board, no trend, too little evidence) sorts after every measured one in
 * either direction: unmeasured is not "the least".
 */
export function sortRows(rows: CompetitorSummary[], sort: SortKey, dir: SortDir, rules: TrendRules): CompetitorSummary[] {
  const sign = dir === "asc" ? 1 : -1;
  const indexed = rows.map((row, i) => ({ row, i }));
  if (sort === "name") return indexed.sort((a, b) => sign * a.row.name.localeCompare(b.row.name) || a.i - b.i).map((x) => x.row);
  return indexed
    .sort((a, b) => {
      const va = sortValue(a.row, sort, rules);
      const vb = sortValue(b.row, sort, rules);
      if (va === null || vb === null) return va === vb ? a.i - b.i : va === null ? 1 : -1;
      return sign * compareValues(va, vb) || a.i - b.i;
    })
    .map((x) => x.row);
}
