import { useMemo } from "react";
import { cn } from "@/lib/utils";
import { ChartFrame, SvgMark, TimeAxis, type FrameProps } from "./ChartFrame";
import { formatDate, PAINT, SURFACE, textWidth, timeScale, timeTicks, useChartWidth, useMarks, type ChartColor, type Surface } from "./scales";
import { ChartTooltip } from "./tooltip";

/*
 * Moves over time, one lane per kind (Launches, Pricing, Money…) or per competitor: a dot per
 * event in its lane on a shared time axis. The lane, not a colour, says what kind of move it is;
 * a dot's size is its importance. Every lane is printed, empty ones too, so "nothing in Pricing"
 * reads as an answer. A dot can open the event (a link through `onNavigate`).
 */

export type Lane = { key: string; label: string };
export type LaneEvent = {
  key: string;
  lane: string;
  x: Date;
  title: string;
  /** Importance 0..1: the dot's area. */
  size?: number;
  /** A line of context for the tooltip. */
  detail?: string;
};

export type SwimlaneProps = FrameProps & {
  lanes: Lane[];
  events: LaneEvent[];
  /** The time window; default the events' own span. */
  domain?: [Date, Date];
  xFormat?: (d: Date) => string;
  /** Where a dot leads: `analysisHref(id)`. */
  href?: (event: LaneEvent) => string;
  onNavigate?: (href: string) => void;
  color?: ChartColor;
};

const LANE_H = 34;
const M = { top: 6, right: 14, bottom: 28 };

export function Swimlane({ lanes, events, domain, xFormat = formatDate, href, onNavigate, color = "chart-1", surface = "background", ...frame }: SwimlaneProps) {
  const known = useMemo(() => new Set(lanes.map((l) => l.key)), [lanes]);
  // Only the events in a known lane and inside the window are drawn (and counted as data).
  const from = domain?.[0].getTime() ?? -Infinity;
  const to = domain?.[1].getTime() ?? Infinity;
  const shown = useMemo(
    () => events.filter((e) => known.has(e.lane) && e.x.getTime() >= from && e.x.getTime() <= to).sort((a, b) => a.x.getTime() - b.x.getTime()),
    [events, known, from, to],
  );
  const isEmpty = shown.length === 0;
  const laneName = new Map(lanes.map((l) => [l.key, l.label]));
  const table = {
    columns: ["Date", "Lane", "Event"],
    rows: shown.map((e) => [xFormat(e.x), laneName.get(e.lane) ?? e.lane, e.title]),
  };
  return (
    <ChartFrame {...frame} surface={surface} empty={isEmpty ? frame.empty : null} table={isEmpty ? undefined : table}>
      {isEmpty ? null : <LanePlot lanes={lanes} events={shown} domain={domain} xFormat={xFormat} href={href} onNavigate={onNavigate} color={color} surface={surface} />}
    </ChartFrame>
  );
}

type PlotProps = {
  lanes: Lane[];
  events: LaneEvent[];
  domain?: [Date, Date];
  xFormat: (d: Date) => string;
  href?: (event: LaneEvent) => string;
  onNavigate?: (href: string) => void;
  color: ChartColor;
  surface: Surface;
};

/**
 * a dot's radius over the sizes in view, from R_MIN to R_MAX, so real importances (about 40
 * to 90) read as different sizes; all alike (or one dot) sit at the middle. Area grows with size.
 */
const R_MIN = 3;
const R_MAX = 9;
function radiusScale(sizes: number[]): (size?: number) => number {
  const clamp = (s?: number) => Math.max(0, Math.min(1, s ?? 0.5));
  const lo = Math.min(...sizes.map(clamp));
  const hi = Math.max(...sizes.map(clamp));
  if (!(hi > lo)) return () => (R_MIN + R_MAX) / 2;
  const area = (s: number) => (s - lo) / (hi - lo);
  return (size) => R_MIN + Math.sqrt(area(clamp(size))) * (R_MAX - R_MIN);
}

function LanePlot({ lanes, events, domain, xFormat, href, onNavigate, color, surface }: PlotProps) {
  const { ref, width } = useChartWidth();
  const marks = useMarks(events.length, events.length - 1);
  const labelW = Math.min(width < 480 ? 84 : 128, Math.max(56, Math.ceil(Math.max(...lanes.map((l) => textWidth(l.label)))) + 14));
  const plotW = Math.max(48, width - labelW - M.right);
  const plotH = lanes.length * LANE_H;
  const height = M.top + plotH + M.bottom;
  const x = domain ? timeScale([domain[0].getTime(), domain[1].getTime()], plotW) : timeScale(events.map((e) => e.x.getTime()), plotW, 0.03);
  const laneIndex = new Map(lanes.map((l, i) => [l.key, i]));
  const radius = radiusScale(events.map((e) => e.size ?? 0.5));

  // Within a lane, a dot that would overlap the one before it moves up or down a step.
  const placed = new Map<string, { cx: number; cy: number; r: number }>();
  const last = new Map<string, { cx: number; r: number; k: number }>();
  for (const e of events) {
    const cx = x(e.x);
    const r = radius(e.size);
    const base = laneIndex.get(e.lane)! * LANE_H + LANE_H / 2;
    const prev = last.get(e.lane);
    const k = prev && cx - prev.cx < prev.r + r ? prev.k + 1 : 0;
    const shift = [0, -7, 7, -12, 12][k % 5]!;
    placed.set(e.key, { cx, cy: base + shift, r });
    last.set(e.lane, { cx, r, k });
  }

  const active = marks.active === null ? null : events[marks.active]!;
  const laneLabel = (key: string) => lanes.find((l) => l.key === key)?.label ?? key;

  return (
    <div ref={ref} className="relative w-full" data-plot>
      <svg width={width} height={height} className="block overflow-visible [&_*]:transition-none">
        <g transform={`translate(0, ${M.top})`}>
          {lanes.map((lane, i) => (
            <g key={lane.key} aria-hidden>
              {i > 0 ? <line x1={0} x2={labelW + plotW} y1={i * LANE_H} y2={i * LANE_H} className="stroke-border" shapeRendering="crispEdges" /> : null}
              <text x={0} y={i * LANE_H + LANE_H / 2} dy="0.32em" className="fill-muted-foreground text-meta">
                {truncate(lane.label, labelW - 10)}
              </text>
            </g>
          ))}
          <line x1={labelW} x2={labelW + plotW} y1={plotH} y2={plotH} className="stroke-input" shapeRendering="crispEdges" aria-hidden />
          <g transform={`translate(${labelW}, 0)`}>
            <TimeAxis ticks={timeTicks(x, plotW)} top={plotH} />
            {active ? <line x1={placed.get(active.key)!.cx} x2={placed.get(active.key)!.cx} y1={0} y2={plotH} className={marks.focused ? "stroke-ring" : "stroke-input"} strokeWidth={1} shapeRendering="crispEdges" aria-hidden /> : null}
            <g data-chart-marks>
              {events.map((e, i) => {
                const { cx, cy, r } = placed.get(e.key)!;
                const on = marks.active === i;
                return (
                  <SvgMark key={e.key} marks={marks} index={i} label={`${e.title}, ${laneLabel(e.lane)}, ${xFormat(e.x)}`} href={href?.(e)} onNavigate={onNavigate}>
                    <circle cx={cx} cy={cy} r={Math.max(12, r + 4)} className="fill-transparent" />
                    {on && marks.focused ? <circle cx={cx} cy={cy} r={r + 4} className="fill-none stroke-ring" strokeWidth={2} /> : null}
                    <circle cx={cx} cy={cy} r={on ? r + 1 : r} className={cn(PAINT[color].fill, SURFACE[surface].ring)} strokeWidth={2} />
                  </SvgMark>
                );
              })}
            </g>
          </g>
        </g>
      </svg>
      {active ? (
        <ChartTooltip
          x={labelW + placed.get(active.key)!.cx}
          y={M.top + placed.get(active.key)!.cy - placed.get(active.key)!.r - 2}
          bottom={M.top + placed.get(active.key)!.cy + placed.get(active.key)!.r + 2}
          containerWidth={width}
          heading={xFormat(active.x)}
          rows={[{ key: "t", value: active.title, label: laneLabel(active.lane) }]}
          note={[active.detail, href ? "Open the analysis" : null].filter(Boolean).join(" · ") || undefined}
        />
      ) : null}
    </div>
  );
}

function truncate(label: string, room: number): string {
  const fit = Math.floor(room / 7.1);
  return label.length <= fit ? label : `${label.slice(0, Math.max(1, fit - 1))}…`;
}
