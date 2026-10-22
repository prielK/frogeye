import { ChevronRightIcon } from "lucide-react";
import { Fragment } from "react";
import { Num } from "@/components/common/Num";
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";
import { formatCount, plural } from "@/lib/format";
import { cn } from "@/lib/utils";

function count(funnel: Record<string, unknown>, key: string): number | null {
  const v = funnel[key];
  return typeof v === "number" && Number.isFinite(v) && v >= 0 ? v : null;
}

/*
 * "Scanned 412 › 61 relevant › 14 new signals · 6 in this briefing · 3 that matter": how much
 * reading this briefing saved, in one line of the masthead. The funnel is the DAY's (every run that filed a version of this date's briefing, summed), so it agrees with Agent
 * runs. Only the collection stages are chained (each narrows the one before); what the
 * briefing carries follows a dot, because a briefing can carry signals filed on earlier days
 * and need not be a subset of today's new ones. Form (dataviz): text, not a bar: the
 * stages span orders of magnitude, so a linear bar renders the last ones as slivers and a log
 * bar misstates them; DESIGN.md also rules out stat tiles. The full funnel is one hover
 * away. Numbers are tabular; the final stage carries the brand ink.
 */
export function FunnelStrip({
  funnel,
  inBriefing,
  takeaways,
  className,
}: {
  funnel: Record<string, unknown>;
  /** Signals this briefing carries. */
  inBriefing: number;
  /** Top takeaways: the ones that matter. */
  takeaways: number;
  className?: string;
}) {
  const fetched = count(funnel, "fetched");
  if (!fetched) return null;
  const relevant = count(funnel, "relevant");
  const fresh = count(funnel, "signals_new");
  const analysed = count(funnel, "signals_analyzed");
  const runs = count(funnel, "runs") ?? 1;

  const chain = [
    { key: "scanned", before: "Scanned", value: fetched, after: "" },
    relevant !== null ? { key: "relevant", before: "", value: relevant, after: "relevant" } : null,
    fresh !== null ? { key: "new", before: "", value: fresh, after: fresh === 1 ? "new signal" : "new signals" } : null,
  ].filter((s) => s !== null);
  const carried = [
    { key: "briefing", value: inBriefing, after: "in this briefing" },
    takeaways > 0 ? { key: "matter", value: takeaways, after: "that matter" } : null,
  ].filter((s) => s !== null);

  const detail = [
    `${formatCount(fetched)} items fetched from public sources`,
    count(funnel, "documents_created") !== null ? `${formatCount(count(funnel, "documents_created")!)} new documents` : null,
    count(funnel, "prefilter_passed") !== null ? `${formatCount(count(funnel, "prefilter_passed")!)} passed the keyword screen` : null,
    relevant !== null ? `${formatCount(relevant)} judged relevant by triage` : null,
    fresh !== null ? `${plural(fresh, "new signal")} filed${analysed !== null ? `, ${formatCount(analysed)} analysed` : ""}` : null,
    `${plural(inBriefing, "signal")} in this briefing`,
    takeaways > 0 ? `${plural(takeaways, "top takeaway")}` : null,
  ].filter(Boolean);
  const source = runs > 1 ? `From the ${formatCount(runs)} runs that filed this day's briefing: ` : "From the run that filed this briefing: ";

  const stage = (key: string, value: number, before: string, after: string, last: boolean) => (
    <span className="whitespace-nowrap">
      {before ? `${before} ` : null}
      <Num value={value} className={cn("font-semibold", last && key === "matter" ? "text-primary-ink" : "text-foreground")} />
      {after ? ` ${after}` : null}
    </span>
  );

  return (
    <p className={cn("text-meta text-muted-foreground", className)}>
      <Tooltip>
        <TooltipTrigger asChild>
          <button
            type="button"
            className={cn(
              "inline-flex flex-wrap items-center gap-x-1.5 gap-y-1 rounded-sm text-left",
              "transition-colors duration-150 ease-out-quart hover:text-foreground",
              "focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 focus-visible:ring-offset-background focus-visible:outline-none",
            )}
          >
            {chain.map((s, i) => (
              <Fragment key={s.key}>
                {i > 0 ? (
                  <>
                    <span className="sr-only">, </span>
                    <ChevronRightIcon aria-hidden className="size-3 shrink-0 text-muted-foreground/70" />
                  </>
                ) : null}
                <span className="whitespace-nowrap">
                  {stage(s.key, s.value, s.before, s.after, false)}
                  {i === chain.length - 1 && carried.length ? (
                    <>
                      <span className="sr-only">; </span>
                    <span aria-hidden className="ml-1.5 text-muted-foreground/70">
                      ·
                    </span>
                    </>
                  ) : null}
                </span>
              </Fragment>
            ))}
            {/* A dot ends the item before it, so a wrapped line never starts with one. */}
            {carried.map((s, i) => (
              <span key={s.key} className="whitespace-nowrap">
                {stage(s.key, s.value, "", s.after, i === carried.length - 1)}
                {i < carried.length - 1 ? (
                  <>
                    <span className="sr-only">; </span>
                    <span aria-hidden className="ml-1.5 text-muted-foreground/70">
                      ·
                    </span>
                  </>
                ) : null}
              </span>
            ))}
          </button>
        </TooltipTrigger>
        <TooltipContent align="start" className="max-w-80 text-left">
          <span className="font-semibold">{source}</span>
          {detail.join(", then ")}.
        </TooltipContent>
      </Tooltip>
    </p>
  );
}
