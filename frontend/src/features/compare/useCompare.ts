import { queryOptions, useQuery } from "@tanstack/react-query";
import { api, ApiError, type Schemas } from "@/lib/api/client";
import { formatCount, plural } from "@/lib/format";
import type { IntelRules } from "@/lib/intelReference";
import { productSlug } from "@/lib/products";
import { weightWords } from "@/features/intel/words";

/*
 * The comparison matrix's data: one read (`GET /compare`, the same for every persona) and what the
 * page derives from it. Every value is one the product and competitor pages already serve: a row's
 * pressure is the products list's, a column's threat the competitor list's, a cell the product
 * deep dive's Competitors row. Nothing is measured here.
 */

export type Compare = Schemas["CompareOut"];
export type CompareProduct = Schemas["CompareProductOut"];
export type CompareCompetitor = Schemas["CompareCompetitorOut"];
export type CompareCell = Schemas["CompareCellOut"];
export type CompareEvidence = Schemas["CompareEvidenceOut"];
export type CellLevel = CompareCell["threat_level"];

export function compareQuery() {
  return queryOptions({
    queryKey: ["compare"],
    queryFn: async ({ signal }): Promise<Compare> => {
      const { data, response } = await api.GET("/api/v1/compare", { signal });
      if (!data) throw new ApiError(response.status);
      return data;
    },
    staleTime: 5 * 60_000,
  });
}

export function useCompare() {
  return useQuery(compareQuery());
}

/** A cell's key: "artifactory:sonatype" (also its address, `?cell=`). */
export const cellKey = (product: string, competitor: string) => `${product}:${competitor}`;

/** The cells by product and competitor slug. */
export function cellIndex(cells: CompareCell[]): Map<string, CompareCell> {
  return new Map(cells.map((c) => [cellKey(c.product_slug, c.competitor_slug), c]));
}

/**
 * A cell's threat in words, the product page's: the threat counts threat entries only, so a
 * pairing whose evidence names another effect reads "No threat named", and one with no evidence
 * at all "No recent evidence".
 */
export function cellWord(cell: Pick<CompareCell, "threat_level" | "evidence">): string {
  if (cell.threat_level !== "none") return { high: "High", medium: "Medium", low: "Low" }[cell.threat_level];
  return cell.evidence > 0 ? "No threat named" : "No recent evidence";
}

/** "Nexus Repository, Sonatype Firewall"; a pairing named only by its signals says so. */
export const productsLine = (cell: CompareCell) => (cell.their_products.length > 0 ? cell.their_products.map((p) => p.name).join(", ") : "No product of theirs mapped");

// ---- filters (in the address, so a shared link opens the same view) ------------------------

export type View = { tier?: number; category?: string };

export function parseView(params: URLSearchParams): View {
  const tier = Number(params.get("tier"));
  return { tier: Number.isInteger(tier) && tier > 0 ? tier : undefined, category: params.get("category") || undefined };
}

/** The view as search params: unset filters left out, other params (the open cell's) kept. */
export function viewParams(view: View, current: URLSearchParams): URLSearchParams {
  const next = new URLSearchParams(current);
  next.delete("tier");
  next.delete("category");
  if (view.tier !== undefined) next.set("tier", String(view.tier));
  if (view.category) next.set("category", view.category);
  return next;
}

export const hasFilters = (view: View) => view.tier !== undefined || view.category !== undefined;

export const tiersOf = (cols: CompareCompetitor[]) => [...new Set(cols.map((c) => c.tier))].sort((a, b) => a - b);

/** The categories some column's seeded products are in, A to Z, keyed by their kebab-case slug. */
export function categoriesOf(cols: CompareCompetitor[]): { slug: string; name: string }[] {
  const names = new Set(cols.flatMap((c) => c.product_categories));
  return [...names].sort((a, b) => a.localeCompare(b)).map((name) => ({ slug: productSlug(name), name }));
}

/** A tier or category the data cannot match reads as "All" (a stale or mistyped address). */
export function knownView(view: View, cols: CompareCompetitor[]): View {
  const tier = view.tier !== undefined && tiersOf(cols).includes(view.tier) ? view.tier : undefined;
  const category = view.category && categoriesOf(cols).some((c) => c.slug === view.category) ? view.category : undefined;
  return tier === view.tier && category === view.category ? view : { tier, category };
}

export function applyFilters(cols: CompareCompetitor[], view: View): CompareCompetitor[] {
  return cols.filter((c) => (view.tier === undefined || c.tier === view.tier) && (!view.category || c.product_categories.some((p) => productSlug(p) === view.category)));
}

// ---- words -----------------------------------------------------------------------------------

/**
 * The page's lead counts the pairings by their threat: "JFrog's 13 products against 17
 * competitors' products. 3 pairings carry a high threat on recent evidence, 6 medium and 11 low;
 * 52 name no threat."
 */
export function leadSentence(data: Compare): string {
  const cells = data.cells;
  const head = `JFrog's ${plural(data.products.length, "product")} against ${plural(data.competitors.length, "competitor")}' products.`;
  if (cells.length === 0) return `${head} No competing product is mapped yet.`;
  const count = (level: CellLevel) => cells.filter((c) => c.threat_level === level).length;
  const [high, medium, low, none] = [count("high"), count("medium"), count("low"), count("none")];
  if (none === cells.length) return `${head} None of the ${formatCount(cells.length)} pairings names a threat on recent evidence yet.`;
  const parts = [high ? `${formatCount(high)} high` : null, medium ? `${formatCount(medium)} medium` : null, low ? `${formatCount(low)} low` : null].filter(Boolean) as string[];
  const scored = high + medium + low;
  const levels = parts.length === 1 ? parts[0] : `${parts.slice(0, -1).join(", ")} and ${parts.at(-1)}`;
  const rest = none > 0 ? `; ${formatCount(none)} name no threat` : "";
  return `${head} ${formatCount(scored)} ${scored === 1 ? "pairing carries" : "pairings carry"} a threat on recent evidence: ${levels}${rest}.`;
}

/** How a cell is built, from the API's own rules. */
export function cellFormula(rules: IntelRules): string {
  const o = rules.overlap;
  return `A cell is a JFrog product against one competitor. It lists the competitor's products that FrogEye's seed maps to that JFrog product, and its threat is the one the product's page shows for that competitor: the sum of the severity weights (${weightWords(rules.severity_weights)}) of the threat entries naming the product in the current analyses of the competitor's public signals over the last ${plural(o.lookback_days, "day")}, read as high from ${o.high_evidence}, medium from ${o.medium_evidence}, low from 1. Entries of another effect are evidence but no threat (“No threat named”); with no entry at all a cell reads “No recent evidence”. The date is the competitor's newest signal naming the product. A blank cell means no product of theirs is mapped to it.`;
}

/** Why a column's overall threat and a cell's level differ. */
export const levelsNote = "A column's threat is the competitor's overall threat to JFrog, as the competitor list scores it (overlap × momentum × hiring), so it is read on a different scale from the cells.";
