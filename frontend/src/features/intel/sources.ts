import type { CitedSource } from "@/components/signal/query";
import type { Schemas } from "@/lib/api/client";

/*
 * A brief's numbered sources, shared by both deep dives: what each kind is in
 * words, and the shape the `[n]` citation markers read.
 */

export type IntelSource = Schemas["IntelSourceOut"];

/** What a numbered source is, in words, when it names no outlet of its own. */
export const INTEL_KIND: Record<IntelSource["kind"], string> = {
  signal: "FrogEye signal",
  fact: "Reported fact",
  posting: "Job posting",
  page: "Watched page",
};

/**
 * The brief's sources in the shape the citation markers read (the analysis page's `[n]`): a signal
 * keeps its in-app link (a hidden one has none and reads as plain text); every other kind links out.
 */
export function citedSourcesOf(sources: IntelSource[]): CitedSource[] {
  return sources.map((s) => ({
    n: s.n,
    kind: s.kind === "signal" ? "signal" : "document",
    ref: s.ref,
    title: s.title,
    url: s.url,
    outlet: s.outlet?.trim() || INTEL_KIND[s.kind],
    published_at: null,
    is_primary: false,
  }));
}
