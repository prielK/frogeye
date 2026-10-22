import { useId } from "react";
import { useNavigate } from "react-router";
import { BubbleScatter, type BubblePoint } from "@/components/charts/BubbleScatter";
import { CompetitorChip } from "@/components/common/CompetitorChip";
import { SectionHead } from "@/components/layout/SectionHead";
import { plural } from "@/lib/format";
import { ratio, trendWord, useRules, youngCollection, type IntelRules, type TrendRules } from "@/lib/intelReference";
import { cn } from "@/lib/utils";
import { weightWords } from "@/features/intel/words";
import { boardState, formatMomentum, formatOverlap, landscapeOf, levelWeeks, momentumLevel, NO_JOB_BOARD, NOT_READABLE, PENDING, type CompetitorSummary } from "./useCompetitorIntel";

/*
 * Who matters and who is moving, at a glance: x is the threat overlap with
 * JFrog's products, y the weekly momentum level (the mean weekly signal importance of the trend's
 * recent weeks, or of the weeks collected on a young deployment; defined for every scored
 * competitor), the bubble's area the open roles, its colour and shape the tier. The trend rides in
 * each bubble's tooltip, as in the table. Colour follows the entity: the tier slots come from every
 * tier on the page, never from the filtered slice, so a filter never repaints a survivor. Only
 * competitors with too little evidence to score are left off, and they are named under it.
 */

/** "Sonatype", "Sonatype and GitHub", "Sonatype, GitHub and GitLab". */
function names(rows: CompetitorSummary[]): string {
  const all = rows.map((r) => r.name);
  return all.length < 3 ? all.join(" and ") : `${all.slice(0, -1).join(", ")} and ${all.at(-1)}`;
}

/** The printed one-line summary: who overlaps most with JFrog, and who is the most active. */
function summarize(points: CompetitorSummary[], rules: TrendRules): string {
  const top = Math.max(...points.map((r) => r.overlap!));
  const leaders = points.filter((r) => r.overlap === top);
  const active = points.reduce((best, r) => (momentumLevel(r, rules)! > momentumLevel(best, rules)! ? r : best), points[0]!);
  const level = `a weekly momentum of ${formatMomentum(momentumLevel(active, rules)!)}`;
  const overlap = `${names(leaders)} ${leaders.length === 1 ? "overlaps" : "overlap"} most with JFrog`;
  if (leaders.length === 1 && leaders[0] === active) return `${overlap} and is the most active, at ${level}.`;
  return `${overlap}; ${active.name} is the most active, at ${level}.`;
}

/** The legend's name for a bubble with no open-roles count: one reason when all share it. */
function missingLabel(points: CompetitorSummary[]): string {
  const states = points.map(boardState).filter((s) => s !== "measured");
  if (states.every((s) => s === "none")) return NO_JOB_BOARD;
  if (states.every((s) => s === "pending")) return PENDING;
  if (states.every((s) => s === "stale")) return NOT_READABLE;
  return states.includes("stale") ? "No current open-roles count" : "No open-roles count yet";
}

/** A bubble's own reason for a missing open-roles count. */
const missingOf = (row: CompetitorSummary) => ({ measured: undefined, pending: PENDING, stale: NOT_READABLE, none: NO_JOB_BOARD })[boardState(row)];

const LEVEL_WORD = { high: "High", medium: "Medium", low: "Low", insufficient: "Insufficient data" } as const;

/** The tooltip's note: the threat level, then the trend the level does not show. */
function detailOf(row: CompetitorSummary, rules: TrendRules): string {
  const trend = row.threat.momentum_trend;
  const momentum = trend === null ? "no trend baseline yet" : `trend ${ratio(trend)}, ${trendWord(trend, rules)}`;
  return `${LEVEL_WORD[row.threat.level]} threat to JFrog · ${momentum}`;
}

/** The footnote: what each measure is, the weeks the level averages and where its trend is (on a young deployment). */
function noteOf(rows: CompetitorSummary[], rules: IntelRules, today: string): string {
  const t = rules.trend;
  const n = levelWeeks(rows, t);
  const overlap = `Overlap: the severity-weighted threat impacts on JFrog products in its public signals of the last ${plural(rules.overlap.lookback_days, "day")} (${weightWords(rules.severity_weights)}).`;
  const roles = "Bubble area: open roles on its public job board.";
  if (youngCollection(rules.collected_since, today, t)) {
    const span = n < t.recent_weeks ? `the ${plural(n, "week")} collected` : `the last ${plural(n, "week")}`;
    return `${overlap} Weekly momentum: its mean weekly signal importance over ${span}; no trend shows until all ${t.weeks} weeks are collected. ${roles}`;
  }
  return `${overlap} Weekly momentum: its mean weekly signal importance over the last ${plural(n, "week")}; the trend against the ${t.baseline_weeks} weeks before is in the tooltip and the table. ${roles}`;
}

type LandscapeProps = {
  /** The competitors on screen (filtered). */
  rows: CompetitorSummary[];
  /** Every tier on the page, lowest first: the colour and shape slots. */
  tiers: number[];
  /** A filter narrows `rows`: the empty state then speaks of these competitors, not of all. */
  filtered?: boolean;
  /** The app-zone day the page reads as of. */
  today: string;
  className?: string;
};

export function LandscapeChart({ rows, tiers, filtered = false, today, className }: LandscapeProps) {
  const headId = useId();
  const navigate = useNavigate();
  const rules = useRules();
  const { plotted, insufficient } = landscapeOf(rows, rules.trend);
  const maxX = Math.max(4, ...plotted.map((r) => r.overlap!));
  const maxY = Math.max(1, ...plotted.map((r) => momentumLevel(r, rules.trend)!));
  const placedWhen = `A competitor is placed once it has ${rules.threat.min_signals} public signals in ${plural(rules.threat.lookback_days, "day")} or a job board listed in full.`;
  const points: BubblePoint[] = plotted.map((r) => ({
    key: r.slug,
    label: r.name,
    x: r.overlap!,
    y: momentumLevel(r, rules.trend)!,
    size: r.open_roles,
    missingLabel: missingOf(r),
    group: String(r.tier),
    detail: detailOf(r, rules.trend),
    href: `/competitors/${r.slug}`,
  }));

  return (
    <section aria-labelledby={headId} className={cn("flex flex-col gap-4", className)}>
      {/* The one ink-ruled head; the visible title is the chart's. */}
      <SectionHead id={headId} title="Landscape" srOnly className="-mb-4 pb-0" />
      <BubbleScatter
        title="Competitive landscape"
        summary={points.length > 0 ? summarize(plotted, rules.trend) : ""}
        empty={
          filtered
            ? { title: "None of these competitors can be plotted yet", hint: placedWhen }
            : { title: "Not enough evidence to plot the landscape yet", hint: placedWhen }
        }
        points={points}
        x={{ label: "Overlap with JFrog", format: formatOverlap, domain: [0, maxX * 1.08] }}
        y={{ label: "Weekly momentum", format: formatMomentum, domain: [0, maxY * 1.08] }}
        size={{ label: "Open roles", missingLabel: missingLabel(plotted) }}
        groups={tiers.map((t) => ({ key: String(t), label: `Tier ${t}` }))}
        quadrants={{ topRight: "High overlap, active", bottomLeft: "Low overlap, quiet" }}
        note={noteOf(rows, rules, today)}
        onNavigate={navigate}
      />
      {insufficient.length > 0 ? (
        <ul aria-label="Not plotted" className="flex flex-col gap-2">
          <li className="flex flex-wrap items-center gap-x-2 gap-y-1.5 text-meta text-muted-foreground">
            <span>
              <span className="font-medium text-foreground">Insufficient data</span>
              <span>, not plotted:</span>
            </span>
            {insufficient.map((r) => (
              <CompetitorChip key={r.slug} slug={r.slug} name={r.name} />
            ))}
          </li>
        </ul>
      ) : null}
    </section>
  );
}
