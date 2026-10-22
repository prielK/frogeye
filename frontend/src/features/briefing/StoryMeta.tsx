import { RelativeTime } from "@/components/common/RelativeTime";
import { hasNoAnalysis } from "@/components/signal/analysisState";
import { AnalysisStateMark } from "@/components/signal/AnalysisStateNote";
import { CompanyChips } from "@/components/signal/CompanyChips";
import { EvidenceGrade } from "@/components/signal/EvidenceGrade";
import { leastTrusted } from "@/components/signal/meta";
import type { SignalCard } from "@/components/signal/query";
import { StanceLabel } from "@/components/signal/StanceLabel";
import { cn } from "@/lib/utils";
import { ABOVE_COVER } from "./story";

/* The meta line under every briefing story; the link it sits in is styled in ./story. */

/**
 * The meta line under a story: the first-read mark when it applies, the stance (rows), the
 * trust line of the least-trusted signal (grade and state together), the companies,
 * the outlets by name and the time the story was first seen.
 *
 * The mark: a body copied from the stage-A first read (`firstRead`) says why the
 * story has no full analysis, from its LIVE state, in the words of the analysis page and the
 * drawer (one copy source, AnalysisStateNote): pending, headline only, or a short take. Once the
 * analysis has landed since the briefing was filed (current or stale), there is no mark.
 */
export function StoryMeta({
  signals,
  firstRead = false,
  stance = false,
  extra,
  aboveCover = false,
  className,
}: {
  /** The signals the story rests on, primary first (one for current briefings). */
  signals: SignalCard[];
  firstRead?: boolean;
  /** Rows say the stance; cards show it per product instead. */
  stance?: boolean;
  /** One more line for the trust line's spoken form, such as the editor's confidence. */
  extra?: string;
  /** On a takeaway card, under its cover: the time is lifted above it, so its hover tooltip stays reachable. */
  aboveCover?: boolean;
  className?: string;
}) {
  const primary = signals[0];
  const weakest = leastTrusted(signals);
  const companies = [...new Set(signals.flatMap((s) => s.competitors))];
  const outlets = [...new Set(signals.flatMap((s) => (s.primary_source ? [s.primary_source] : [])))];
  const live = primary?.analysis_state;
  const mark = firstRead && hasNoAnalysis(live) ? live : null;
  if (!primary) return null;
  return (
    <div className={cn("flex flex-wrap items-center gap-x-3 gap-y-1.5 text-meta text-muted-foreground", className)}>
      {mark ? <AnalysisStateMark state={mark} /> : null}
      {stance && primary ? <StanceLabel stance={primary.stance} /> : null}
      {weakest ? (
        <EvidenceGrade
          plain
          grade={weakest.grade}
          reliability={weakest.reliability}
          credibility={weakest.credibility}
          status={weakest.status}
          extra={[signals.length > 1 ? "The weakest of the signals it rests on." : null, extra].filter(Boolean).join(" ") || undefined}
        />
      ) : null}
      <CompanyChips slugs={companies} max={3} plain />
      {outlets.length > 0 ? <span className="font-medium text-foreground">{outlets.join(", ")}</span> : null}
      <RelativeTime iso={primary.first_seen_at} className={aboveCover ? ABOVE_COVER : undefined} />
    </div>
  );
}
