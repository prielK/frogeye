import { Num } from "@/components/common/Num";
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";
import { formatCount, formatPercent } from "@/lib/format";
import { cn } from "@/lib/utils";
import { prefilterPassRate } from "./useRuns";

/*
 * One ingestion run's funnel: how much of what was fetched survived each stage.
 * Form: a single 8px track per table row (no chart wall). The four stages are ordinal,
 * so they wear one hue in validated lightness steps (--funnel-1..4); values are text in
 * ink tokens, never in the data colour; a 2px surface gap separates touching segments.
 */
const STAGES = [
  { key: "fetched", legend: "Fetched", spoken: "fetched", fill: "bg-funnel-1", swatch: "bg-funnel-1" },
  { key: "new_raw", legend: "New", spoken: "new", fill: "bg-funnel-2", swatch: "bg-funnel-2" },
  { key: "documents_created", legend: "Docs", spoken: "documents", fill: "bg-funnel-3", swatch: "bg-funnel-3" },
  { key: "prefilter_passed", legend: "Passed", spoken: "passed the prefilter", fill: "bg-funnel-4", swatch: "bg-funnel-4" },
] as const;

const GAP_PX = 2;
const GRID = "grid grid-cols-4 gap-x-2";

type Stats = Record<string, unknown>;

function count(stats: Stats, key: string): number | null {
  const value = stats[key];
  return typeof value === "number" && Number.isFinite(value) && value >= 0 ? value : null;
}

/** Column legend for the runs table: the stage names over the numbers each row shows. */
export function FunnelLegend({ className }: { className?: string }) {
  return (
    <span className={cn(GRID, "w-full", className)}>
      {STAGES.map((stage) => (
        <span key={stage.key} className="inline-flex items-center gap-1.5">
          <span aria-hidden className={cn("size-2 shrink-0 rounded-[2px]", stage.swatch)} />
          {stage.legend}
        </span>
      ))}
    </span>
  );
}

export function FunnelBar({ stats, className }: { stats: Stats; className?: string }) {
  const values = STAGES.map((stage) => ({ ...stage, value: count(stats, stage.key) }));
  const max = Math.max(0, ...values.map((v) => v.value ?? 0));

  // Nested extents anchored on the right, so the bar runs light to dark left to right under the
  // Fetched -> Passed labels: Passed (the result) sits at the right edge, each earlier stage
  // extends further left, and every visible step is a drop-off. Each stage is painted from the
  // next-smaller value out to its own, which stays honest when a later stage outgrows an earlier
  // one (a processing backlog); on a tie the later stage takes the extent and its colour.
  const ascending = values
    .map((v, index) => ({ ...v, stage: index + 1 }))
    .filter((v) => (v.value ?? 0) > 0)
    .sort((a, b) => a.value! - b.value! || b.stage - a.stage);
  const segments: { stage: number; fill: string; from: number; to: number }[] = [];
  let start = 0;
  for (const v of ascending) {
    if (v.value! > start) segments.push({ stage: v.stage, fill: v.fill, from: start / max, to: v.value! / max });
    start = v.value!;
  }

  const label =
    "Funnel: " +
    values.map((v) => (v.value === null ? `${v.spoken.split(" ")[0]} not recorded` : `${formatCount(v.value)} ${v.spoken}`)).join(", ");

  const [fetched, fresh] = values.map((v) => v.value);
  const duplicates = typeof stats.duplicates === "number" ? stats.duplicates : null;
  const passRate = prefilterPassRate(stats);
  const rates = [
    fetched && fresh !== null ? `${formatPercent(fresh / fetched)} of fetched items were new` : null,
    duplicates ? `${formatCount(duplicates)} duplicates were dropped` : null,
    passRate !== null ? `${formatPercent(passRate)} of screened documents passed the prefilter` : null,
  ].filter(Boolean);

  return (
    <div role="img" aria-label={label} className={cn("flex w-full flex-col gap-1.5", className)}>
      <Tooltip>
        <TooltipTrigger asChild>
          <div aria-hidden className={cn("relative h-2 w-full", max === 0 && "rounded-[4px] bg-muted")}>
            {segments.map((s, i) => {
              // The outermost (leftmost) extent carries the rounded data end; inner ones leave a
              // 2px surface gap on their left edge.
              const outermost = i === segments.length - 1;
              const width = `${(s.to - s.from) * 100}%`;
              return (
                <span
                  key={s.stage}
                  data-stage={s.stage}
                  className={cn("absolute inset-y-0", s.fill, outermost && "rounded-l-[4px]")}
                  style={{
                    right: `${s.from * 100}%`,
                    width: outermost ? width : `max(2px, calc(${width} - ${GAP_PX}px))`,
                  }}
                />
              );
            })}
          </div>
        </TooltipTrigger>
        <TooltipContent>{rates.length ? rates.join(". ") + "." : "Nothing was fetched in this run."}</TooltipContent>
      </Tooltip>
      <span aria-hidden className={cn(GRID, "text-meta")}>
        {values.map((v, i) => (
          <span
            key={v.key}
            className={cn(
              v.value === null ? "text-muted-foreground/70" : i === values.length - 1 ? "font-semibold text-foreground" : "text-muted-foreground",
            )}
          >
            {v.value === null ? "—" : <Num value={v.value} />}
          </span>
        ))}
      </span>
    </div>
  );
}
