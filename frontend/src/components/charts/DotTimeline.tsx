import { useMemo } from "react";
import { cn } from "@/lib/utils";
import { ChartFrame, SvgMark, TimeAxis, ValueAxis, type FrameProps, type LegendItem } from "./ChartFrame";
import { allIntegers, formatDate, formatTick, formatValue, PAINT, SURFACE, textWidth, timeScale, timeTicks, useChartWidth, useMarks, valueScale, valueTicks, type ChartColor, type Surface } from "./scales";
import { ChartTooltip } from "./tooltip";

/*
 * Reported figures over time (headcount): a dot per figure, as stated, and never a line between
 * them, since nobody measured the months in between. Two sources for the same month are two
 * dots, side by side, each with its source; a figure whose sources disagree is drawn hollow and
 * says so. Events that are not on the value axis (a layoff, whose number counts something else)
 * sit as markers along the top, on time only. Every shape here is a circle, a line or a polygon.
 */

export type TimelinePoint = {
  key: string;
  x: Date;
  y: number;
  /** The source or context, shown with the value: "TechCrunch, 3 Jun 2025". */
  detail?: string;
  /** The figure's sources state different values: drawn hollow, labelled in the tooltip. */
  disputed?: boolean;
};

export type TimelineEvent = {
  key: string;
  x: Date;
  /** The event's name: "Layoff". */
  label: string;
  detail?: string;
};

export type DotTimelineProps = FrameProps & {
  points: TimelinePoint[];
  events?: TimelineEvent[];
  yFormat?: (n: number) => string;
  yTickFormat?: (n: number) => string;
  xFormat?: (d: Date) => string;
  /** What a value counts, for the legend and tooltip: "Reported headcount". */
  valueLabel?: string;
  /** The events' legend name: "Layoff reported". */
  eventLabel?: string;
  height?: number;
  /** Zero-based by default; a figure is a level, and the eye compares levels. */
  zero?: boolean;
};

const M = { top: 30, right: 14, bottom: 28 };
const POINT: ChartColor = "chart-1";
const EVENT: ChartColor = "chart-3";

export function DotTimeline({ points, events = [], yFormat = formatValue, yTickFormat = formatTick, xFormat = formatDate, valueLabel = "Reported figure", eventLabel = "Event", height = 220, zero = true, surface = "background", ...frame }: DotTimelineProps) {
  const isEmpty = points.length === 0 && events.length === 0;
  const legend: LegendItem[] = [
    ...(points.length ? [{ key: "point", label: valueLabel, color: POINT, shape: "dot" as const }] : []),
    ...(points.some((p) => p.disputed) ? [{ key: "disputed", label: "Sources disagree", color: POINT, shape: "ring" as const }] : []),
    ...(events.length ? [{ key: "event", label: eventLabel, color: EVENT, shape: "triangle" as const }] : []),
  ];
  const table = {
    columns: ["Date", "Value", "Source"],
    rows: [
      ...points.map((p) => ({ t: p.x.getTime(), row: [xFormat(p.x), `${yFormat(p.y)}${p.disputed ? " (sources disagree)" : ""}`, p.detail ?? "—"] })),
      ...events.map((e) => ({ t: e.x.getTime(), row: [xFormat(e.x), e.label, e.detail ?? "—"] })),
    ]
      .sort((a, b) => a.t - b.t)
      .map((r) => r.row),
  };
  return (
    <ChartFrame {...frame} surface={surface} empty={isEmpty ? frame.empty : null} legend={isEmpty || legend.length < 2 ? undefined : legend} table={isEmpty ? undefined : table}>
      {isEmpty ? null : <DotPlot points={points} events={events} yFormat={yFormat} yTickFormat={yTickFormat} xFormat={xFormat} valueLabel={valueLabel} height={height} zero={zero} surface={surface} />}
    </ChartFrame>
  );
}

type PlotProps = {
  points: TimelinePoint[];
  events: TimelineEvent[];
  yFormat: (n: number) => string;
  yTickFormat: (n: number) => string;
  xFormat: (d: Date) => string;
  valueLabel: string;
  height: number;
  zero: boolean;
  surface: Surface;
};

type Mark = { kind: "point"; p: TimelinePoint; t: number } | { kind: "event"; e: TimelineEvent; t: number };

function DotPlot({ points, events, yFormat, yTickFormat, xFormat, valueLabel, height, zero, surface }: PlotProps) {
  const { ref, width } = useChartWidth();
  // Reading order: by time, a day's events after its figures.
  const ordered = useMemo<Mark[]>(
    () =>
      [...points.map((p) => ({ kind: "point" as const, p, t: p.x.getTime() })), ...events.map((e) => ({ kind: "event" as const, e, t: e.x.getTime() }))].sort(
        (a, b) => a.t - b.t || (a.kind === b.kind ? 0 : a.kind === "point" ? -1 : 1),
      ),
    [points, events],
  );
  const marks = useMarks(ordered.length, ordered.length - 1);
  const values = points.map((p) => p.y);
  const integer = allIntegers(values);
  const plotH = height - M.top - M.bottom;
  const y = valueScale(values.length ? Math.max(...values) : 1, plotH, { min: zero || !values.length ? 0 : Math.min(...values), integer });
  // Events alone have no value: a bare time axis, never a scale nobody measured.
  const yTicks = values.length ? valueTicks(y, plotH, integer) : [];
  const left = Math.ceil(Math.max(0, ...yTicks.map((t) => textWidth(yTickFormat(t))))) + 12;
  const plotW = Math.max(48, width - left - M.right);
  const x = timeScale(
    ordered.map((m) => m.t),
    plotW,
    0.04,
  );
  // A single day's figures get that day as their only tick (no padded day around it); otherwise the regular time grid.
  const oneDay = new Set(ordered.map((m) => m.t)).size === 1 ? [ordered[0]!.t] : undefined;

  // Same-day figures sit side by side, 11px apart, so both stay visible and focusable.
  const offsets = new Map<string, number>();
  const byDay = new Map<number, TimelinePoint[]>();
  for (const p of points) byDay.set(p.x.getTime(), [...(byDay.get(p.x.getTime()) ?? []), p]);
  for (const group of byDay.values()) group.forEach((p, i) => offsets.set(p.key, (i - (group.length - 1) / 2) * 11));

  const active = marks.active === null ? null : ordered[marks.active]!;
  const pos = (m: Mark) => (m.kind === "point" ? { cx: x(m.p.x) + (offsets.get(m.p.key) ?? 0), cy: y(m.p.y) } : { cx: x(m.e.x), cy: -14 });
  const describe = (m: Mark) =>
    m.kind === "point"
      ? `${xFormat(m.p.x)}: ${yFormat(m.p.y)} ${valueLabel.toLowerCase()}${m.p.disputed ? ", sources disagree" : ""}${m.p.detail ? `, ${m.p.detail}` : ""}`
      : `${xFormat(m.e.x)}: ${m.e.label}${m.e.detail ? `, ${m.e.detail}` : ""}`;

  return (
    <div ref={ref} className="relative w-full" data-plot>
      <svg width={width} height={height} className="block overflow-visible [&_*]:transition-none">
        <g transform={`translate(${left}, ${M.top})`}>
          {yTicks.length ? (
            <ValueAxis ticks={yTicks} y={y} width={plotW} format={yTickFormat} />
          ) : (
            <line x1={0} x2={plotW} y1={plotH} y2={plotH} className="stroke-input" shapeRendering="crispEdges" aria-hidden />
          )}
          <TimeAxis ticks={timeTicks(x, plotW, oneDay)} top={plotH} />
          <g data-chart-marks>
            {ordered.map((m, i) => {
              const { cx, cy } = pos(m);
              const on = marks.active === i;
              return (
                <SvgMark key={`${m.kind}-${m.kind === "point" ? m.p.key : m.e.key}`} marks={marks} index={i} kind={m.kind} label={describe(m)}>
                  <circle cx={cx} cy={cy} r={12} className="fill-transparent" />
                  {on && marks.focused ? <circle cx={cx} cy={cy} r={10} className="fill-none stroke-ring" strokeWidth={2} /> : null}
                  {m.kind === "point" ? (
                    <circle cx={cx} cy={cy} r={on ? 6 : 5} className={m.p.disputed ? cn(SURFACE[surface].fill, PAINT[POINT].stroke) : cn(PAINT[POINT].fill, SURFACE[surface].ring)} strokeWidth={2} />
                  ) : (
                    <polygon points={`${cx - 6},${cy - 5} ${cx + 6},${cy - 5} ${cx},${cy + 5}`} className={PAINT[EVENT].fill} transform={on ? `translate(${cx} ${cy}) scale(1.2) translate(${-cx} ${-cy})` : undefined} />
                  )}
                </SvgMark>
              );
            })}
          </g>
        </g>
      </svg>
      {active ? (
        <ChartTooltip
          x={left + pos(active).cx}
          y={M.top + pos(active).cy - 7}
          bottom={M.top + pos(active).cy + 7}
          containerWidth={width}
          heading={xFormat(active.kind === "point" ? active.p.x : active.e.x)}
          rows={
            active.kind === "point"
              ? [{ key: "v", value: yFormat(active.p.y), label: valueLabel, color: POINT, shape: active.p.disputed ? "ring" : "dot" }]
              : [{ key: "e", value: active.e.label, color: EVENT, shape: "triangle" }]
          }
          note={
            active.kind === "point"
              ? [active.p.disputed ? "Sources disagree." : null, active.p.detail].filter(Boolean).join(" ") || undefined
              : active.e.detail
          }
        />
      ) : null}
    </div>
  );
}
