import { queryOptions, useQuery } from "@tanstack/react-query";
import { api, ApiError, type Schemas } from "@/lib/api/client";
import { hostOf } from "@/lib/format";

export type SignalCard = Schemas["SignalCard"];
export type SignalDetail = Schemas["SignalDetail"];
/** The stage-B analysis, every `cites` already a 1-based number into `SignalDetail.sources`. */
export type SignalAnalysis = Schemas["SignalAnalysisOut"];
export type AnalysisState = SignalDetail["analysis_state"];
export type CitedText = Schemas["CitedTextOut"];
export type ProductImpact = Schemas["ProductImpactOut"];
export type RiskOpportunity = Schemas["RiskOpportunityOut"];
export type WatchItem = Schemas["WatchItemOut"];
/** One numbered source: a member document, a cited JFrog docs page or a cited signal. */
export type CitedSource = Schemas["CitedSourceOut"];

/** One signal's detail (analysis, sources, evidence, score breakdown, related): `GET /signals/{id}`. */
export function signalQuery(id: number) {
  return queryOptions({
    queryKey: ["signals", "detail", id],
    queryFn: async ({ signal }): Promise<SignalDetail> => {
      const { data, response } = await api.GET("/api/v1/signals/{signal_id}", {
        params: { path: { signal_id: id } },
        signal,
      });
      if (!data) throw new ApiError(response.status);
      return data;
    },
  });
}

/** The detail for `id`; idle while `id` is null. A 404 (withdrawn, or hidden from this reader) is an ApiError. */
export function useSignal(id: number | null) {
  return useQuery({ ...signalQuery(id ?? 0), enabled: id !== null });
}

/**
 * The analysis the reading surfaces show: a current one, or a stale one (the story has new
 * coverage since it was written), which still reads in full under a quiet "being refreshed"
 * note, as card summaries do. Thin and unanalysed signals have none and fall back to
 * their stage-A fact and impact.
 */
export function readableAnalysis(detail: SignalDetail): SignalAnalysis | null {
  return detail.analysis_state === "current" || detail.analysis_state === "stale" ? detail.analysis : null;
}

/**
 * The numbered Sources list. The API always numbers the member documents; a response without
 * `sources` (an older API) falls back to the documents in the same order, primary first.
 */
export function sourcesOf(detail: SignalDetail): CitedSource[] {
  if (detail.sources.length > 0) return detail.sources;
  return detail.documents.map((d, i) => ({
    n: i + 1,
    kind: "document",
    ref: `doc:${d.id}`,
    title: d.title?.trim() || `Untitled page on ${hostOf(d.url)}`, // never a bare URL
    url: d.url,
    outlet: d.source_name,
    published_at: d.published_at,
    is_primary: d.is_primary,
  }));
}

/** The id of a cited signal the reader may open (its url is "/signals/104"), or null (hidden from this reader, or not a signal). */
export function signalSourceId(source: CitedSource): number | null {
  const match = source.kind === "signal" && source.url ? /^\/signals\/(\d{1,9})$/.exec(source.url) : null;
  return match ? Number(match[1]) : null;
}

/** Who published a source, in words: the outlet, "JFrog documentation", or "FrogEye signal". */
export function sourceOutlet(source: CitedSource): string {
  if (source.outlet?.trim()) return source.outlet.trim();
  return source.kind === "signal" ? "FrogEye signal" : source.kind === "kb" ? "JFrog documentation" : "Source";
}
