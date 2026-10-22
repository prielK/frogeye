import { keepPreviousData, useInfiniteQuery } from "@tanstack/react-query";
import { api, ApiError, type DocumentPage, type Schemas } from "@/lib/api/client";

export type Scope = Schemas["Scope"];
export type DocStatus = Schemas["DocStatus"];

export type DocumentFilters = {
  competitor?: string;
  scope?: Scope;
  status?: DocStatus;
  q?: string;
};

export const PAGE_SIZE = 50;

/** No "jfrog" scope: JFrog-self news is never listed; ?scope=jfrog falls back to any scope. */
export const SCOPES: { value: Scope; label: string }[] = [
  { value: "competitor", label: "Competitor" },
  { value: "industry", label: "Industry" },
];

/**
 * Reader-facing names for the pipeline's statuses: "new" passed the keyword prefilter and
 * awaits triage, "irrelevant" was screened out, "relevant" was confirmed by triage.
 */
export const STATUSES: { value: DocStatus; label: string }[] = [
  { value: "new", label: "New" },
  { value: "relevant", label: "Relevant" },
  { value: "irrelevant", label: "Filtered out" },
  { value: "duplicate", label: "Duplicate" },
];

export function scopeLabel(scope: string): string {
  return SCOPES.find((s) => s.value === scope)?.label ?? scope;
}

export function statusLabel(status: string): string {
  return STATUSES.find((s) => s.value === status)?.label ?? status;
}

/** Parse untrusted URL values into filters the API accepts; unknown values are dropped. */
export function parseFilters(params: URLSearchParams): DocumentFilters {
  const scope = params.get("scope");
  const status = params.get("status");
  const competitor = params.get("competitor")?.trim();
  const q = params.get("q")?.trim();
  return {
    competitor: competitor || undefined,
    scope: SCOPES.some((s) => s.value === scope) ? (scope as Scope) : undefined,
    status: STATUSES.some((s) => s.value === status) ? (status as DocStatus) : undefined,
    q: q || undefined,
  };
}

export function hasFilters(filters: DocumentFilters): boolean {
  return Boolean(filters.competitor || filters.scope || filters.status || filters.q);
}

async function fetchPage(filters: DocumentFilters, cursor: string | null, signal: AbortSignal): Promise<DocumentPage> {
  const { data, response } = await api.GET("/api/v1/documents", {
    params: { query: { ...filters, cursor: cursor ?? undefined, limit: PAGE_SIZE } },
    signal,
  });
  if (!data) throw new ApiError(response.status);
  return data;
}

/**
 * The feed, newest first, a page at a time: each page's `next_cursor` fetches the next.
 * While a new filter loads, the previous list stays on screen (dimmed by the caller).
 */
export function useDocuments(filters: DocumentFilters) {
  return useInfiniteQuery({
    queryKey: ["documents", filters],
    queryFn: ({ pageParam, signal }) => fetchPage(filters, pageParam, signal),
    initialPageParam: null as string | null,
    getNextPageParam: (last) => last.next_cursor,
    placeholderData: keepPreviousData,
  });
}
