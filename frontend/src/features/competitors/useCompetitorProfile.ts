import { queryOptions, useInfiniteQuery, useQuery } from "@tanstack/react-query";
import { dayDate, SERIES_COLORS, type TimeSeries } from "@/components/charts/scales";
import { densify } from "@/features/intel/series";
import { api, ApiError, type Schemas } from "@/lib/api/client";
import { shiftDay } from "@/lib/days";

/*
 * The competitor deep dive's data (`GET /competitors/{slug}/profile`, the same for every persona):
 * the reads, the section registry and what counts as data in a section, the brief's
 * numbered sources, and the series helpers. Nothing is measured here: every figure is the API's,
 * and a value it serves as null stays unmeasured. How things read is in profileWords.
 */

export type CompetitorProfile = Schemas["CompetitorProfile"];
export type Fact = Schemas["FactOut"];
export type FactSource = Schemas["FactSourceOut"];
export type FactPoint = Schemas["FactPointOut"];
export type HeadcountPoint = Schemas["HeadcountPointOut"];
export type FundingRound = Schemas["FundingRoundOut"];
export type Posting = Schemas["PostingOut"];
export type IntelSource = Schemas["IntelSourceOut"];
export type PageChange = Schemas["PageChangeOut"];
export type ProductOverlap = Schemas["ProductOverlapOut"];
export type SectionKey = Schemas["CoverageNoteOut"]["section"];
export type FactKind = Fact["kind"];

// ---- reads -----------------------------------------------------------------------------------

export function profileQuery(slug: string) {
  return queryOptions({
    queryKey: ["competitor-profile", slug],
    queryFn: async ({ signal }): Promise<CompetitorProfile> => {
      const { data, response } = await api.GET("/api/v1/competitors/{slug}/profile", { params: { path: { slug } }, signal });
      if (!data) throw new ApiError(response.status);
      return data;
    },
    staleTime: 5 * 60_000,
  });
}

export function useCompetitorProfile(slug: string) {
  return useQuery(profileQuery(slug));
}

export const POSTINGS_PAGE = 50;

/** A filter of the open roles: a capability tag or a department, the API's own filters. */
export type PostingFilter = { tag?: string; department?: Posting["department"] };

/** Every open role on the competitor's board, newest first, 50 a page (`GET /competitors/{slug}/postings`), optionally by tag or department. */
export function usePostings(slug: string, enabled: boolean, filter: PostingFilter = {}) {
  return useInfiniteQuery({
    queryKey: ["competitor-postings", slug, "open", filter.tag ?? null, filter.department ?? null],
    queryFn: async ({ pageParam, signal }) => {
      const base = { status: "open" as const, limit: POSTINGS_PAGE, ...(filter.tag ? { tag: filter.tag } : {}), ...(filter.department ? { department: filter.department } : {}) };
      const query = pageParam ? { ...base, cursor: pageParam } : base;
      const { data, response } = await api.GET("/api/v1/competitors/{slug}/postings", { params: { path: { slug }, query }, signal });
      if (!data) throw new ApiError(response.status);
      return data;
    },
    initialPageParam: null as string | null,
    getNextPageParam: (last) => last.next_cursor,
    enabled,
    staleTime: 5 * 60_000,
  });
}

// ---- sections (layout A: one long page, the exact menu labels) -------------------------

export const SECTIONS = [
  { id: "overview", key: "overview", label: "Overview" },
  { id: "products", key: "products", label: "Products vs JFrog" },
  { id: "momentum", key: "momentum", label: "Momentum" },
  { id: "hiring", key: "hiring", label: "People & hiring" },
  { id: "headcount", key: "headcount", label: "Headcount" },
  { id: "money", key: "money", label: "Money" },
  { id: "market", key: "market", label: "Market" },
  { id: "customers", key: "customers", label: "Customers" },
  { id: "pricing", key: "pricing", label: "Pricing" },
  { id: "community", key: "community", label: "Community" },
  { id: "people-partners", key: "people_partners", label: "People & partners" },
  { id: "latest-signals", key: "latest_signals", label: "Latest signals" },
  { id: "sources", key: "sources", label: "Sources" },
] as const satisfies readonly { id: string; key: SectionKey; label: string }[];

export type Section = (typeof SECTIONS)[number];
export type SectionId = Section["id"];
export const SECTION_IDS: readonly SectionId[] = SECTIONS.map((s) => s.id);

export const sectionOf = (key: SectionKey) => SECTIONS.find((s) => s.key === key)!;

/** The share-of-voice series the chart draws: the competitor and its top 3 peers by overlap. */
export const voiceSlugs = (profile: CompetitorProfile) => [profile.competitor.slug, ...profile.market.peers.slice(0, 3)];

/**
 * Share of voice has data for `metric` when one of the four drawn series counted something. A
 * peer past the top 3 is not drawn, so its counts never make the menu say Market has data while
 * the chart says nothing was counted.
 */
export const voiceMeasured = (profile: CompetitorProfile, metric: "documents" | "signals") => {
  const drawn = new Set(voiceSlugs(profile));
  return profile.market.share_of_voice.some((p) => drawn.has(p.competitor_slug) && p[metric] > 0);
};

/** Whether a section has anything of its own to show (the menu marks the ones that do not). */
export function hasData(profile: CompetitorProfile, key: SectionKey): boolean {
  const facts = (f: { facts?: Fact[]; undated: Fact[] }) => (f.facts?.length ?? 0) + f.undated.length > 0;
  switch (key) {
    case "overview":
    case "sources":
      return true;
    case "products":
      return profile.products.length > 0;
    case "momentum":
      return profile.timeline.length > 0 || profile.momentum !== null;
    case "hiring":
      return profile.hiring.open_now !== null;
    case "headcount":
      return profile.headcount.points.length + profile.headcount.undated.length > 0;
    case "money":
      return profile.money.funding_rounds.length + profile.money.valuations.length + profile.money.revenue.length + profile.money.undated.length > 0;
    case "market":
      return profile.market.market_share.length + profile.market.undated.length > 0 || voiceMeasured(profile, "documents") || voiceMeasured(profile, "signals");
    case "customers":
      return facts(profile.customers) || profile.customers.signals.length > 0;
    case "pricing":
      return profile.pricing.length > 0;
    case "community":
      return profile.community.repos.some((r) => [...r.stars, ...r.forks].some((p) => p.value > 0)) || profile.community.mentions.some((m) => m.count > 0);
    case "people_partners":
      return facts(profile.people_partners) || profile.people_partners.signals.length > 0;
    case "latest_signals":
      return profile.latest_signals.length > 0;
  }
}

/** The API's coverage notes for one section (why it is empty or partial), in its words. */
export function notesFor(profile: CompetitorProfile, key: SectionKey): string[] {
  return profile.sources_freshness.notes.filter((n) => n.section === key).map((n) => n.text);
}

export type LedgerRow = Pick<Fact, "kind" | "label" | "value" | "unit" | "currency" | "as_of" | "sources_disagree" | "sources"> & { key: string };

/** Fact groups (and points, rounds) as ledger rows, newest first, undated last. */
export const ledgerRows = (facts: Omit<LedgerRow, "key">[]): LedgerRow[] =>
  facts
    .map((f, i) => ({ ...f, key: `${f.kind}-${f.label}-${f.as_of ?? "undated"}-${f.value ?? "x"}-${i}` }))
    .sort((a, b) => (b.as_of ?? "").localeCompare(a.as_of ?? ""));

// ---- series ---------------------------------------------------------------------------------------

export const WINDOWS = [7, 30, 90] as const;
export type Window = (typeof WINDOWS)[number];

/**
 * A window of `days` days of a daily series, ending on its latest day: `days + 1` snapshots, so its
 * first point is the one a `days`-day change compares with. A series that starts later (a young
 * board) gives what it has.
 */
export function lastDays<T extends { day: string }>(points: T[], days: number): T[] {
  const end = points.at(-1)?.day;
  if (!end) return [];
  const from = shiftDay(end, -days);
  return points.filter((p) => p.day >= from);
}

/**
 * Each key's change in open roles over the API's change window (`days`, its 30-day change):
 * today's count (the breakdown) less its count on the board's snapshot that many days before the
 * latest. A key absent from that snapshot had 0 then. A board with no snapshot that day (its series
 * start later: a younger board) has no change to report, and neither does one whose total
 * change the API serves as null.
 */
export function deltasOver(
  breakdown: { key: string; count: number }[],
  series: { day: string; key: string; count: number }[],
  total: { day: string }[],
  history: boolean,
  days: number,
): Map<string, number | null> {
  const latest = total.at(-1)?.day;
  const then = latest ? shiftDay(latest, -days) : null;
  const known = history && then !== null && total.some((t) => t.day === then);
  const before = new Map(series.filter((p) => p.day === then).map((p) => [p.key, p.count]));
  return new Map(breakdown.map((b) => [b.key, known ? b.count - (before.get(b.key) ?? 0) : null]));
}

/** Share of voice: the competitor and its top 3 peers by overlap, densified by week, colour pinned to the entity. */
export function voiceSeries(profile: CompetitorProfile, nameOf: (slug: string) => string, metric: "documents" | "signals"): TimeSeries[] {
  const own = profile.competitor;
  const slugs = voiceSlugs(profile);
  const weeks = [...new Set(profile.market.share_of_voice.map((p) => p.week_start))].sort();
  const dense = densify(
    profile.market.share_of_voice.filter((p) => slugs.includes(p.competitor_slug)).map((p) => ({ x: p.week_start, key: p.competitor_slug, y: p[metric] })),
    weeks,
  );
  return slugs.map((slug, i) => ({
    key: slug,
    label: slug === own.slug ? own.name : nameOf(slug),
    color: SERIES_COLORS[i],
    points: (dense.get(slug) ?? []).map((p) => ({ x: dayDate(p.x), y: p.y })),
  }));
}
