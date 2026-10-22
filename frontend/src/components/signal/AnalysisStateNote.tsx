import { RefreshCwIcon } from "lucide-react";
import { cn } from "@/lib/utils";
import { hasNoAnalysis, NO_ANALYSIS, type NoAnalysisState } from "./analysisState";
import type { AnalysisState } from "./query";

const STALE_NOTE = "This story has new coverage; its analysis is being refreshed.";

/**
 * Says how complete the analysis on screen is, never alarming. A stale analysis still reads in
 * full, so it gets one quiet `meta` line. A signal shown from its stage-A fact and impact
 * gets a dashed panel with an icon and its reason. `compact` drops the
 * reason (the drawer). Nothing for a current one.
 */
export function AnalysisStateNote({ state, compact = false, className }: { state: AnalysisState; compact?: boolean; className?: string }) {
  if (state === "stale") {
    return (
      <p role="note" className={cn("flex items-start gap-1.5 text-meta text-muted-foreground", className)}>
        <RefreshCwIcon aria-hidden className="mt-0.5 size-3.5 shrink-0" />
        {STALE_NOTE}
      </p>
    );
  }
  if (!hasNoAnalysis(state)) return null;
  const look = NO_ANALYSIS[state];
  const Icon = look.icon;
  return (
    <div role="note" className={cn("flex gap-2.5 rounded-md border border-dashed px-3.5 py-3", className)}>
      <Icon aria-hidden className="mt-1 size-4 shrink-0 text-muted-foreground" />
      <div className="flex flex-col gap-0.5">
        <p className="text-body font-medium text-foreground">{look.title}</p>
        {compact ? null : <p className="text-body text-muted-foreground">{look.text}</p>}
      </div>
    </div>
  );
}

/** The same reason as an inline `meta` mark, for a briefing story read from its first take (StoryMeta). */
export function AnalysisStateMark({ state }: { state: NoAnalysisState }) {
  const look = NO_ANALYSIS[state];
  const Icon = look.icon;
  return (
    <span className="inline-flex h-5 items-center gap-1 rounded-sm border border-dashed border-input px-1.5 font-medium text-foreground">
      <Icon aria-hidden className="size-3.5 shrink-0 text-muted-foreground" />
      {look.title}
    </span>
  );
}
