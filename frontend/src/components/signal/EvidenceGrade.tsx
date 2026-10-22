import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";
import { cn } from "@/lib/utils";
import { CREDIBILITY, RELIABILITY, trustFor } from "./meta";

type EvidenceGradeProps = {
  /** e.g. "B2"; null when the signal is not scored yet. */
  grade: string | null;
  reliability: string | null;
  credibility: number | null;
  /** The signal status that sets the trust state: approved | auto | needs_review | ... */
  status: string;
  /** One more tooltip line, such as the editor's confidence in a takeaway. */
  extra?: string;
  /**
   * Text, not a control: for a trust line inside a link (a briefing card or story row), where
   * a button would be a control nested in a control. The same badge and state; the grade is
   * spelled out to screen readers instead of in a tooltip.
   */
  plain?: boolean;
  className?: string;
};

/** The grade badge and the trust state, icon plus word: what both forms of the trust line show. */
function Parts({ grade, status }: { grade: string | null; status: string }) {
  const trust = trustFor(status);
  const Icon = trust.icon;
  return (
    <>
      {grade ? (
        <span
          data-numeric
          className="inline-flex h-5 min-w-7 items-center justify-center rounded-sm border border-input px-1 text-meta font-semibold tracking-[0.04em] text-foreground"
        >
          {grade}
        </span>
      ) : null}
      <span className={cn("inline-flex items-center gap-1 font-medium", trust.className)}>
        <Icon aria-hidden className="size-3.5 shrink-0" />
        <span className={status === "auto" ? "text-muted-foreground" : undefined}>{trust.label}</span>
      </span>
    </>
  );
}

/**
 * The trust line: the Admiralty grade badge (source reliability A-F x information
 * credibility 1-6) and the approval state, as one control whose tooltip explains both.
 */
export function EvidenceGrade({ grade, reliability, credibility, status, extra, plain = false, className }: EvidenceGradeProps) {
  const trust = trustFor(status);
  const rel = reliability ? RELIABILITY[reliability] : undefined;
  const cred = credibility !== null ? CREDIBILITY[credibility] : undefined;
  const spoken = grade
    ? `Admiralty grade ${grade}${rel && cred ? `: source ${rel}, information ${cred}` : ""}. ${trust.label}.`
    : `Not graded yet. ${trust.label}.`;
  if (plain) {
    return (
      <span className={cn("inline-flex h-6 items-center text-meta text-muted-foreground", className)}>
        <span className="sr-only">{[spoken, extra].filter(Boolean).join(" ")}</span>
        <span aria-hidden className="inline-flex items-center gap-2">
          <Parts grade={grade} status={status} />
        </span>
      </span>
    );
  }
  return (
    <Tooltip>
      <TooltipTrigger asChild>
        <button
          type="button"
          aria-label={spoken}
          className={cn(
            "relative z-10 inline-flex h-6 items-center gap-2 rounded-sm text-meta text-muted-foreground",
            "transition-colors duration-150 ease-out-quart hover:text-foreground",
            "focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 focus-visible:ring-offset-background focus-visible:outline-none",
            className,
          )}
        >
          <Parts grade={grade} status={status} />
        </button>
      </TooltipTrigger>
      <TooltipContent align="start" className="flex max-w-80 flex-col gap-1.5 px-2.5 py-2 text-left">
        {grade ? (
          <span>
            <span className="font-semibold">Admiralty grade {grade}.</span>{" "}
            {rel && cred ? `Source reliability ${reliability}, ${rel}; information credibility ${credibility}, ${cred}.` : null}
          </span>
        ) : (
          <span className="font-semibold">Not graded yet.</span>
        )}
        <span className="opacity-80">
          Reliability runs A (completely reliable) to F (cannot be judged); credibility 1 (confirmed) to 6 (cannot be judged).
        </span>
        <span>{trust.explain}</span>
        {extra ? <span>{extra}</span> : null}
      </TooltipContent>
    </Tooltip>
  );
}
