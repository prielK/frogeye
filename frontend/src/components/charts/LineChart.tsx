import { area as d3area, line as d3line } from "d3-shape";
import { useMemo } from "react";
import { cn } from "@/lib/utils";
import { ChartFrame, PLOT_TOUCH, PlotHit, SeriesMarker, SvgMark, TimeAxis, ValueAxis, type FrameProps, type LegendItem } from "./ChartFrame";
import {
  allIntegers,
  foldSeries,
  formatDate,
  formatTick,
  formatValue,
  markerIndices,
  MAX_SERIES,
  OTHER_DASH,
  PAINT,
  seriesTable,
  SURFACE,
  textWidth,
  timeScale,
  timeTicks,
  unionX,
  useChartWidth,
  useMarks,
  valueScale,
  valueTicks,
  type DrawnSeries,
  type SeriesCap,
  type Surface,
  type TimePoint,
  type TimeSeries,
} from "./scales";
import { ChartTooltip } from "./tooltip";

/*
 * Change over time for one to four named series (more fold into "Other (n)"): 2px lines and a
 * soft wash under a single series. Series stay apart without colour at every width: each wears
 * its slot's marker shape (circle, square, triangle, diamond) along the line and at its end, and
 * "Other" is dashed; the legend and tooltip keys repeat them. A crosshair snaps to the nearest
 * day from anywhere on the plot and one tooltip lists every series there. Zero-based by default.
 */

export type LineChartProps = FrameProps & {
  /**
   * Up to four named series; more fold into "Other (n)". The kit has four categorical colours plus
   * a neutral "Other" and no more: named series wear `chart-1..4` in the order given (an
   * explicit `color` pins a slot), the fold wears `chart-5`, dashed. Keep the order stable: colour
   * follows it.
   */
  series: TimeSeries[];
  /** Exact values (tooltip, table, end label). Default "1,240". */
  yFormat?: (n: number) => string;
  /** Axis ticks. Default "1.2K". */
  yTickFormat?: (n: number) => string;
  /** A point's day, exactly. Default "17 Oct 2026". */
  xFormat?: (d: Date) => string;
  height?: number;
  /** A soft wash under the line; default on for a single series. */
  area?: boolean;
  /** Start the value axis at zero (default). Off only where the change, not the level, is the point. */
  zero?: boolean;
  /** Named series before the fold, 1 to 4 (the kit has four colours plus a neutral "Other"). */
  maxSeries?: SeriesCap;
};

const M = { top: 12, bottom: 28 };
/** The widest end-label gutter, in px; a longer name leaves the line ends unlabelled. */
const END_GUTTER = 132;

export function LineChart({ series, yFormat = formatValue, yTickFormat = formatTick, xFormat = formatDate, height = 220, area, zero = true, maxSeries = MAX_SERIES, surface = "background", ...frame }: LineChartProps) {
  const drawn = useMemo(() => foldSeries(series, maxSeries), [series, maxSeries]);
  const isEmpty = drawn.length === 0;
  const legend: LegendItem[] | undefined =
    drawn.length > 1
      ? drawn.map((s) => ({ key: s.key, label: s.label, color: s.color, shape: "line", marker: s.shape ?? "none", dashed: s.dashed, title: s.folded.length ? s.folded.join(", ") : undefined }))
      : undefined;
  return (
    <ChartFrame {...frame} surface={surface} empty={isEmpty ? frame.empty : null} legend={legend} table={isEmpty ? undefined : seriesTable(series, xFormat, yFormat)}>
      {isEmpty ? null : <LinePlot drawn={drawn} yFormat={yFormat} yTickFormat={yTickFormat} xFormat={xFormat} height={height} area={area ?? drawn.length === 1} zero={zero} surface={surface} />}
    </ChartFrame>
  );
}

type PlotProps = {
  drawn: DrawnSeries[];
  yFormat: (n: number) => string;
  yTickFormat: (n: number) => string;
  xFormat: (d: Date) => string;
  height: number;
  area: boolean;
  zero: boolean;
  surface: Surface;
};

function LinePlot({ drawn, yFormat, yTickFormat, xFormat, height, area, zero, surface }: PlotProps) {
  const { ref, width } = useChartWidth();
  const xs = unionX(drawn);
  const marks = useMarks(xs.length, xs.length - 1);
  const all = drawn.flatMap((s) => s.points.map((p) => p.y));
  const integer = allIntegers(all);
  const plotH = height - M.top - M.bottom;
  const y = valueScale(Math.max(...all), plotH, { min: zero ? Math.min(0, ...all) : Math.min(...all), integer });
  const yTicks = valueTicks(y, plotH, integer);
  const left = Math.ceil(Math.max(...yTicks.map((t) => textWidth(yTickFormat(t))))) + 12;

  // Direct labels at the line ends when there is room, they do not collide and every one fits
  // the end gutter: a name too long for it would be clipped or run past the plot, so then none
  // is drawn and the legend (which names every series) carries identity alone.
  const ends = drawn.map((s) => ({ s, last: s.points.at(-1)!, text: drawn.length === 1 ? yFormat(s.points.at(-1)!.y) : s.label }));
  const endYs = ends.map((e) => y(e.last.y)).sort((a, b) => a - b);
  const collide = endYs.some((v, i) => i > 0 && v - endYs[i - 1]! < 16);
  const lastX = xs.at(-1)!;
  const gutter = Math.ceil(Math.max(...ends.map((e) => textWidth(e.text)))) + 18;
  const direct = width >= 480 && !collide && gutter <= END_GUTTER && ends.every((e) => e.last.x.getTime() === lastX);
  const right = direct ? gutter : 12;
  const plotW = Math.max(48, width - left - right);
  const x = timeScale(xs, plotW);
  const px = (t: number) => x(new Date(t));
  const positions = xs.map(px);
  const many = drawn.length > 1;

  const line = d3line<TimePoint>()
    .x((p) => x(p.x))
    .y((p) => y(p.y));
  const wash = d3area<TimePoint>()
    .x((p) => x(p.x))
    .y0(y(Math.max(0, y.domain()[0]!)))
    .y1((p) => y(p.y));

  const active = marks.active;
  const activeX = active === null ? null : xs[active]!;
  const rows = (t: number) =>
    drawn.map((s) => ({
      key: s.key,
      color: s.color,
      shape: "line" as const,
      marker: many ? (s.shape ?? ("none" as const)) : undefined,
      dashed: s.dashed,
      value: s.values.has(t) ? yFormat(s.values.get(t)!) : "—",
      label: many ? s.label : undefined,
    }));
  const describe = (t: number) =>
    `${xFormat(new Date(t))}: ${rows(t)
      .map((r) => (r.label ? `${r.label} ${r.value}` : r.value))
      .join(", ")}`;

  return (
    <div ref={ref} className={cn("relative w-full", PLOT_TOUCH)} data-plot>
      <svg width={width} height={height} className="block overflow-visible [&_*]:transition-none">
        <g transform={`translate(${left}, ${M.top})`}>
          <ValueAxis ticks={yTicks} y={y} width={plotW} format={yTickFormat} />
          <TimeAxis ticks={timeTicks(x, plotW, xs)} top={plotH} />
          {area && drawn.length === 1 && drawn[0]!.points.length > 1 ? <path d={wash(drawn[0]!.points) ?? ""} className={PAINT[drawn[0]!.color].wash} /> : null}
          {activeX !== null ? <line x1={px(activeX)} x2={px(activeX)} y1={0} y2={plotH} className={marks.focused ? "stroke-ring" : "stroke-muted-foreground"} strokeWidth={1} shapeRendering="crispEdges" /> : null}
          {drawn.map((s) => {
            const sx = s.points.map((p) => x(p.x));
            // One series: an end dot. Several: the slot's shape along the line, so a crossing can be followed without colour.
            const at = many && s.shape ? markerIndices(sx) : [s.points.length - 1];
            return (
              <g key={s.key} data-series={s.key} data-color={s.color} data-shape={s.shape ?? "none"} aria-hidden>
                {s.points.length > 1 ? (
                  <path
                    d={line(s.points) ?? ""}
                    data-line
                    data-color={s.color}
                    className={cn("fill-none", PAINT[s.color].stroke)}
                    strokeWidth={2}
                    strokeLinejoin="round"
                    strokeLinecap="round"
                    strokeDasharray={s.dashed ? OTHER_DASH : undefined}
                  />
                ) : null}
                {at.map((i) => {
                  const p = s.points[i]!;
                  return s.shape ? (
                    <SeriesMarker key={i} shape={s.shape} x={sx[i]!} y={y(p.y)} color={s.color} surface={surface} />
                  ) : (
                    <circle key={i} cx={sx[i]} cy={y(p.y)} r={3} className={cn(PAINT[s.color].fill, SURFACE[surface].ring)} strokeWidth={1.5} />
                  );
                })}
                {activeX !== null && s.values.has(activeX) ? (
                  s.shape ? (
                    <SeriesMarker shape={s.shape} x={px(activeX)} y={y(s.values.get(activeX)!)} r={5.5} color={s.color} surface={surface} />
                  ) : (
                    <circle cx={px(activeX)} cy={y(s.values.get(activeX)!)} r={4.5} className={cn(PAINT[s.color].fill, SURFACE[surface].ring)} strokeWidth={2} />
                  )
                ) : null}
              </g>
            );
          })}
          {direct
            ? ends.map((e) => (
                <text key={e.s.key} data-end-label x={plotW + 10} y={y(e.last.y)} dy="0.32em" className={cn("text-meta", drawn.length === 1 ? "fill-foreground font-medium" : "fill-muted-foreground")} aria-hidden>
                  {e.text}
                </text>
              ))
            : null}
          <PlotHit width={plotW} height={plotH} positions={positions} marks={marks} />
          <g data-chart-marks>
            {xs.map((t, i) => (
              // Keyboard stops, one per day; the pointer is served by PlotHit above.
              <SvgMark key={t} marks={marks} index={i} label={describe(t)}>
                <rect x={positions[i]! - 1} y={0} width={2} height={plotH} className="pointer-events-none fill-transparent" />
              </SvgMark>
            ))}
          </g>
        </g>
      </svg>
      {activeX !== null ? (
        <ChartTooltip
          x={left + px(activeX)}
          y={M.top}
          placement="side"
          containerWidth={width}
          heading={xFormat(new Date(activeX))}
          rows={rows(activeX)}
        />
      ) : null}
    </div>
  );
}
