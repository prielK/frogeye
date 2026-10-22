import { keepPreviousData, useInfiniteQuery, useQuery } from "@tanstack/react-query";
import { useEffect, useEffectEvent, useRef, type RefObject } from "react";
import { scopeLabel, signalTypeLabel, trustFor } from "@/components/signal/meta";
import { api, ApiError, type Schemas } from "@/lib/api/client";

/* The Signals Explorer's state lives in the URL: facets, the search term and the search mode. */

export type SignalType = Schemas["SignalType"];
export type Stance = Schemas["Stance"];
export type SignalStatus = Schemas["SignalStatus"];
export type Scope = Schemas["Scope"];
export type RetrievalKind = Schemas["RetrievalKind"];
export type SignalPage = Schemas["SignalPage"];
export type SearchHit = Schemas["SearchHitOut"];
export type SearchResponse = Schemas["SearchResponse"];
export type CapabilityCategory = Schemas["CapabilityCategoryOut"];
export type Kit = Schemas["KitOut"];

export type SinceWindow = "24h" | "7d" | "30d" | "90d";
export type ImportanceStep = 25 | 50 | 75;

export type ExplorerFilters = {
  q?: string;
  competitor?: string;
  type?: SignalType;
  stance?: Stance;
  min_importance?: ImportanceStep;
  since?: SinceWindow;
  scope?: Scope;
  capability?: string;
  kit?: string;
  /** Analysts only: readers are only ever shown auto and approved signals. */
  status?: SignalStatus;
  /** "Search everything" (GET /search) instead of the signals list. */
  everything?: boolean;
  /** Search everything only: one retrieval kind. */
  kind?: RetrievalKind;
};

export const PAGE_SIZE = 50;
/** /signals and /search both reject shorter terms; a single character is still typing. */
export const MIN_QUERY = 2;

const option = <T extends string>(value: T, label: string) => ({ value, label });

export const SIGNAL_TYPES = (
  [
    "product_launch",
    "feature_update",
    "pricing_change",
    "deprecation",
    "messaging_change",
    "partnership",
    "acquisition",
    "funding",
    "earnings_financials",
    "leadership_change",
    "hiring_trend",
    "customer_win_loss",
    "security_incident",
    "analyst_recognition",
    "community_buzz",
    "event_conference",
    "legal_regulatory",
    "industry_trend",
    "standard_spec",
    "other",
  ] satisfies SignalType[]
).map((t) => option(t, signalTypeLabel(t) ?? t));

export const STANCES: Stance[] = ["threat", "opportunity", "neutral"];
/** No "jfrog" scope, hidden from every reader; an old ?scope=jfrog link falls back to any scope. */
export const SCOPES = (["competitor", "industry"] satisfies Scope[]).map((s) => option(s, scopeLabel(s)));
export const IMPORTANCE_STEPS: ImportanceStep[] = [25, 50, 75];

const HOUR = 3_600_000;
export const SINCE: { value: SinceWindow; label: string; ms: number }[] = [
  { value: "24h", label: "Past 24 hours", ms: 24 * HOUR },
  { value: "7d", label: "Past 7 days", ms: 7 * 24 * HOUR },
  { value: "30d", label: "Past 30 days", ms: 30 * 24 * HOUR },
  { value: "90d", label: "Past 90 days", ms: 90 * 24 * HOUR },
];

/** Every status an analyst can ask for; with none chosen the API shows auto, approved and needs review. */
export const STATUSES = (["approved", "auto", "needs_review", "pending", "rejected", "suppressed"] satisfies SignalStatus[]).map(
  (s) => option(s, trustFor(s).label),
);

export const KINDS: { value: RetrievalKind; label: string; plural: string }[] = [
  { value: "article_chunk", label: "Article", plural: "Articles" },
  { value: "signal", label: "Signal", plural: "Signals" },
  { value: "jfrog_kb", label: "JFrog docs", plural: "JFrog docs" },
];

function pick<T extends string | number>(raw: string | null, allowed: readonly { value: T }[] | readonly T[]): T | undefined {
  const values = allowed.map((a) => (typeof a === "object" ? a.value : a));
  return values.find((v) => String(v) === raw);
}

const slug = (raw: string | null) => (raw && /^[a-z0-9][a-z0-9-]{0,80}$/.test(raw) ? raw : undefined);

/** Untrusted URL values → filters the API accepts; unknown values are dropped, `status` unless `analyst`. */
export function parseFilters(params: URLSearchParams, { analyst }: { analyst: boolean }): ExplorerFilters {
  const q = params.get("q")?.trim();
  return {
    q: q || undefined,
    competitor: slug(params.get("competitor")),
    type: pick(params.get("type"), SIGNAL_TYPES),
    stance: pick(params.get("stance"), STANCES),
    min_importance: pick(params.get("min_importance"), IMPORTANCE_STEPS),
    since: pick(params.get("since"), SINCE),
    scope: pick(params.get("scope"), SCOPES),
    capability: slug(params.get("capability")),
    kit: slug(params.get("kit")),
    status: analyst ? pick(params.get("status"), STATUSES) : undefined,
    everything: params.get("mode") === "everything" || undefined,
    kind: pick(params.get("kind"), KINDS),
  };
}

const FILTER_KEYS = ["q", "competitor", "type", "stance", "min_importance", "since", "scope", "capability", "kit", "status", "kind"] as const;

/** The URL for `filters`; anything else already in `current` (such as the open signal) is kept. */
export function toParams(filters: ExplorerFilters, current: URLSearchParams): URLSearchParams {
  const params = new URLSearchParams(current);
  for (const key of [...FILTER_KEYS, "mode"]) params.delete(key);
  for (const key of FILTER_KEYS) {
    const value = filters[key];
    if (value !== undefined && value !== "") params.set(key, String(value));
  }
  if (filters.everything) params.set("mode", "everything");
  return params;
}

/** Facets hidden behind "More filters" until used. */
export function secondaryCount(f: ExplorerFilters): number {
  return [f.scope, f.capability, f.kit, f.status].filter(Boolean).length;
}

/**
 * True when anything narrows what the current mode shows (the mode itself is not a filter).
 * Search everything only applies the term, company, date and kind; the list ignores kind.
 */
export function hasFilters(f: ExplorerFilters): boolean {
  if (f.everything) return Boolean(f.q || f.competitor || f.since || f.kind);
  return Boolean(f.q || f.competitor || f.type || f.stance || f.min_importance || f.since || secondaryCount(f));
}

/** The term to search for, or undefined while it is too short to send. */
export function searchTerm(f: ExplorerFilters): string | undefined {
  return f.q && f.q.length >= MIN_QUERY ? f.q : undefined;
}

function sinceIso(window: SinceWindow | undefined): string | undefined {
  const ms = SINCE.find((s) => s.value === window)?.ms;
  return ms ? new Date(Date.now() - ms).toISOString() : undefined;
}

const many = <T,>(value: T | undefined) => (value === undefined ? undefined : [value]);

async function fetchSignals(f: ExplorerFilters, q: string | undefined, cursor: string | null, signal: AbortSignal): Promise<SignalPage> {
  const { data, response } = await api.GET("/api/v1/signals", {
    params: {
      query: {
        competitor: many(f.competitor),
        type: many(f.type),
        stance: f.stance,
        min_importance: f.min_importance,
        since: sinceIso(f.since),
        scope: f.scope,
        capability: many(f.capability),
        kit: many(f.kit),
        status: many(f.status),
        q,
        cursor: cursor ?? undefined,
        limit: PAGE_SIZE,
      },
    },
    signal,
  });
  if (!data) throw new ApiError(response.status);
  return data;
}

/**
 * The Explorer's signals, most important first, a page at a time (`next_cursor`). With a
 * search term it is one page of the top matches: the API never pages a search, so
 * neither does this. The previous result stays on screen while a new one loads.
 */
export function useSignals(filters: ExplorerFilters, { enabled = true }: { enabled?: boolean } = {}) {
  const q = searchTerm(filters);
  // The search mode and its kind facet belong to Search everything, not to the list.
  const facets: ExplorerFilters = { ...filters, everything: undefined, kind: undefined };
  return useInfiniteQuery({
    queryKey: ["signals", "list", { ...facets, q }],
    queryFn: ({ pageParam, signal }) => fetchSignals(facets, q, pageParam, signal),
    initialPageParam: null as string | null,
    getNextPageParam: (last) => (q ? undefined : last.next_cursor),
    placeholderData: keepPreviousData,
    enabled,
  });
}

/** Search everything (GET /search): articles, signals and JFrog docs, ranked together; keyword-only (search_mode) without AI. */
export function useSearchEverything(filters: ExplorerFilters, { enabled = true }: { enabled?: boolean } = {}) {
  const q = searchTerm(filters);
  const { competitor, since, kind } = filters;
  return useQuery({
    queryKey: ["search", { q, competitor, since, kind }],
    queryFn: async ({ signal }): Promise<SearchResponse> => {
      const { data, response } = await api.GET("/api/v1/search", {
        params: { query: { q: q!, competitor: many(competitor), since: sinceIso(since), kinds: many(kind) } },
        signal,
      });
      if (!data) throw new ApiError(response.status);
      return data;
    },
    enabled: enabled && q !== undefined,
    // Each search spends LLM budget: keep answers a while, and never retry a missing key or the rate limit.
    staleTime: 5 * 60_000,
    retry: (failures, error) => !(error instanceof ApiError && error.status >= 400) && failures < 1,
    placeholderData: keepPreviousData,
  });
}

/** The capability taxonomy, by category (GET /capabilities). Reference data: it changes only with the seed. */
export function useCapabilities() {
  return useQuery({
    queryKey: ["reference", "capabilities"],
    queryFn: async (): Promise<CapabilityCategory[]> => {
      const { data, response } = await api.GET("/api/v1/capabilities");
      if (!data) throw new ApiError(response.status);
      return data;
    },
    staleTime: 10 * 60_000,
  });
}

/** The CI team's key intelligence topics, highest priority first (GET /kits). */
export function useKits() {
  return useQuery({
    queryKey: ["reference", "kits"],
    queryFn: async (): Promise<Kit[]> => {
      const { data, response } = await api.GET("/api/v1/kits");
      if (!data) throw new ApiError(response.status);
      return data;
    },
    staleTime: 10 * 60_000,
  });
}

/** Where a j/k keypress belongs to the control, not the list. */
const TYPING =
  "input, textarea, select, [contenteditable=''], [contenteditable='true'], [role='combobox'], [role='listbox'], [role='option'], [role='menu'], [role='menuitem'], [role='dialog']";

/**
 * j/k move focus down/up the rows of `list` (their `[data-row-target]` controls), so Enter
 * opens the focused row natively. Ignored while typing or inside a menu or the drawer. `onEnd`
 * runs on j at the last row (load the next page).
 */
export function useRowKeys(list: RefObject<HTMLElement | null>, onEnd?: () => void) {
  const last = useRef<number | null>(null);
  const atEnd = useEffectEvent(() => onEnd?.());
  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if ((event.key !== "j" && event.key !== "k") || event.defaultPrevented || event.metaKey || event.ctrlKey || event.altKey) return;
      if (event.target instanceof Element && event.target.closest(TYPING)) return;
      const rows = [...(list.current?.querySelectorAll<HTMLElement>("[data-row-target]") ?? [])];
      if (rows.length === 0) return;
      event.preventDefault();
      const focused = rows.findIndex((row) => row === document.activeElement);
      const from = focused !== -1 ? focused : last.current;
      let next = 0;
      if (from !== null && from < rows.length) {
        next = event.key === "j" ? Math.min(from + 1, rows.length - 1) : Math.max(from - 1, 0);
        if (event.key === "j" && from === rows.length - 1) atEnd();
      }
      last.current = next;
      rows[next]!.scrollIntoView({ block: "nearest" });
      rows[next]!.focus({ preventScroll: true });
    };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [list]);
}
