import { dayDate } from "@/components/charts/scales";
import { Sparkline } from "@/components/charts/Sparkline";
import { STANCE } from "@/components/signal/meta";
import { formatCount, plural } from "@/lib/format";
import { nameFromSlug } from "@/lib/competitors";
import { cn } from "@/lib/utils";
import { useCompetitorOverview } from "@/features/competitors/useCompetitorIntel";
import { weekOf } from "@/features/intel/words";
import { stanceOf, type Stance } from "./tally";
import { ABOVE_COVER } from "./story";

/*
 * What tells a briefing reader where to look first, story by story: the importance meter, the
 * stance chip and, on the lead story, its competitor's momentum. One value against a fixed
 * maximum of 100 is a meter, not a chart (dataviz): the number leads, tabular, and the bar is a
 * quiet reading of the same value in `meter` on its `meter-track`. Stance is an icon and a word
 * on the meaning's soft ground, never colour alone.
 */

const METER = {
  /** The lead story: the number at `title`, a long bar under it. */
  lead: { number: "font-headline text-title leading-none", bar: "h-1.5 w-28" },
  /** The other top takeaways. */
  card: { number: "text-body leading-none font-semibold", bar: "h-1 w-16" },
  /** A section row's gutter. */
  row: { number: "text-body leading-none font-semibold", bar: "h-1 w-9" },
} as const;

/** "Importance 88 of 100": one image, the value printed beside its bar. */
export function ImportanceMeter({ value, size, className }: { value: number; size: keyof typeof METER; className?: string }) {
  const share = Math.max(0, Math.min(100, value));
  const look = METER[size];
  return (
    <span
      role="img"
      aria-label={`Importance ${formatCount(value)} of 100`}
      data-importance={value}
      className={cn("inline-flex flex-col items-start gap-1.5", size === "lead" && "gap-2", className)}
    >
      <span aria-hidden className={cn("text-foreground tabular-nums", look.number)}>
        {formatCount(value)}
      </span>
      <span aria-hidden className={cn("relative rounded-r-full bg-meter-track", look.bar)}>
        {share > 0 ? <span className="absolute inset-y-0 left-0 rounded-r-full bg-meter" style={{ width: `max(2px, ${share}%)` }} /> : null}
      </span>
    </span>
  );
}

const CHIP: Record<Stance, string> = {
  threat: "border-threat/30 bg-threat-soft text-threat",
  opportunity: "border-opportunity/30 bg-opportunity-soft text-opportunity",
  neutral: "border-input bg-card text-muted-foreground",
};

/** The story's stance as a soft chip: its icon and its word in the meaning colour. */
export function StanceChip({ stance, className }: { stance: string | null | undefined; className?: string }) {
  const key = stanceOf({ stance: stance ?? null });
  const look = STANCE[key]!;
  const Icon = look.icon;
  return (
    <span
      data-stance-chip={key}
      className={cn("inline-flex h-6 items-center gap-1.5 rounded-sm border px-2 text-meta font-medium whitespace-nowrap", CHIP[key], className)}
    >
      <Icon aria-hidden className="size-3.5 shrink-0" />
      {look.label}
    </span>
  );
}

/**
 * The lead story's competitor over 13 weeks (`GET /competitors/overview`, weekly importance
 * sums), its latest week printed beside the line; the line's name says whose it is. Nothing
 * while the overview loads or when the competitor has no measured weeks: the hero then reads
 * without it rather than show an empty line. JFrog itself is never listed.
 */
export function CompetitorMomentum({ slug }: { slug: string }) {
  const overview = useCompetitorOverview();
  const row = overview.data?.find((c) => c.slug === slug);
  const weeks = row?.momentum;
  if (!row || !weeks || weeks.length === 0) return null;
  const name = row.name || nameFromSlug(slug);
  return (
    <span className={cn(ABOVE_COVER, "inline-flex items-center gap-2.5 text-meta text-muted-foreground")}>
      <span>Momentum</span>
      <Sparkline
        label={`${name} momentum, last ${plural(weeks.length, "week")}`}
        values={weeks.map((w) => ({ x: dayDate(w.week_start), y: w.value }))}
        xFormat={weekOf}
        surface="background"
      />
      <span className="whitespace-nowrap">
        <span className="font-semibold text-foreground tabular-nums">{formatCount(weeks.at(-1)!.value)}</span> this week
      </span>
    </span>
  );
}
