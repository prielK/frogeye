import { area as d3area, line as d3line, stack } from "d3-shape";
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
  type TimeSeries,
} from "./scales";
import { ChartTooltip } from "./tooltip";

/*
 * Part-to-whole over time: the top four categories stacked in the order given, everything else
 * as one "Other" layer on top. Each layer is a light tint edged by a 1.5px line in its
 * colour; layers stay apart without colour at every width: each edge carries its slot's marker
 * shape, "Other" is dashed, a layer tall enough is named inside its band at the right edge, and
 * the legend repeats the shapes. A day missing from a series counts as zero.
 */

export type StackedAreaProps = FrameProps & {
  /**
   * The layers, in a stable order (colour follows it). The kit has four categorical colours plus
   * a neutral "Other" and no more: the top four by total (at most `maxSeries`) are named in
   * `chart-1..4`, the rest are summed into one dashed "Other (n)" layer in `chart-5`.
   */
  series: TimeSeries[];
  yFormat?: (n: number) => string;
  yTickFormat?: (n: number) => string;
  xFormat?: (d: Date) => string;
  height?: number;
  /** Named layers before the fold, 1 to 4 (the kit has four colours plus a neutral "Other"). */
  maxSeries?: SeriesCap;
  /** The tooltip's total row label. */
  totalLabel?: string;
};

const M = { top: 12, right: 12, bottom: 28 };
/** A band is named inside itself only when the name fits this tall. */
const LABEL_BAND = 16;

export function StackedArea({ series, yFormat = formatValue, yTickFormat = formatTick, xFormat = formatDate, height = 220, maxSeries = MAX_SERIES, totalLabel = "Total", surface = "background", ...frame }: StackedAreaProps) {
  const drawn = useMemo(() => foldSeries(series, maxSeries), [series, maxSeries]);
  const isEmpty = drawn.length === 0;
  const legend: LegendItem[] | undefined =
    drawn.length > 1
      ? drawn.map((s) => ({ key: s.key, label: s.label, color: s.color, shape: "rect", marker: s.shape ?? "none", dashed: s.dashed, title: s.folded.length ? s.folded.join(", ") : undefined }))
      : undefined;
  return (
    <ChartFrame {...frame} surface={surface} empty={isEmpty ? frame.empty : null} legend={legend} table={isEmpty ? undefined : seriesTable(series, xFormat, yFormat)}>
      {isEmpty ? null : <StackPlot drawn={drawn} yFormat={yFormat} yTickFormat={yTickFormat} xFormat={xFormat} height={height} totalLabel={totalLabel} surface={surface} />}
    </ChartFrame>
  );
}

type Row = { x: number } & Record<string, number>;

type PlotProps = { drawn: DrawnSeries[]; yFormat: (n: number) => string; yTickFormat: (n: number) => string; xFormat: (d: Date) => string; height: number; totalLabel: string; surface: Surface };

function StackPlot({ drawn, yFormat, yTickFormat, xFormat, height, totalLabel, surface }: PlotProps) {
  const { ref, width } = useChartWidth();
  const xs = unionX(drawn);
  const marks = useMarks(xs.length, xs.length - 1);
  const keys = drawn.map((s) => s.key);
  const rows: Row[] = xs.map((t) => Object.assign({ x: t }, Object.fromEntries(drawn.map((s) => [s.key, s.values.get(t) ?? 0]))) as Row);
  const layers = stack<Row>().keys(keys)(rows);
  const totals = rows.map((r) => keys.reduce((sum, k) => sum + r[k]!, 0));
  const integer = allIntegers(drawn.flatMap((s) => s.points.map((p) => p.y)));
  const plotH = height - M.top - M.bottom;
  const y = valueScale(Math.max(...totals), plotH, { integer });
  const yTicks = valueTicks(y, plotH, integer);
  const left = Math.ceil(Math.max(...yTicks.map((t) => textWidth(yTickFormat(t))))) + 12;
  const plotW = Math.max(48, width - left - M.right);
  const x = timeScale(xs, plotW);
  const positions = xs.map((t) => x(new Date(t)));
  const at = markerIndices(positions);
  const many = drawn.length > 1;

  type Band = [number, number] & { data: Row };
  const fill = d3area<Band>()
    .x((d) => x(new Date(d.data.x)))
    .y0((d) => y(d[0]))
    .y1((d) => y(d[1]));
  const edge = d3line<Band>()
    .x((d) => x(new Date(d.data.x)))
    .y((d) => y(d[1]));

  const active = marks.active;
  const tooltipRows = (i: number) => [
    ...[...drawn].reverse().map((s) => ({ key: s.key, color: s.color, shape: "rect" as const, marker: many ? (s.shape ?? ("none" as const)) : undefined, dashed: s.dashed, value: yFormat(rows[i]![s.key]!), label: s.label })),
    { key: "__total", total: true, value: yFormat(totals[i]!), label: totalLabel },
  ];
  const describe = (i: number) => `${xFormat(new Date(xs[i]!))}: ${tooltipRows(i).map((r) => `${r.label} ${r.value}`).join(", ")}`;
  const single = xs.length === 1;
  const lastI = xs.length - 1;

  return (
    <div ref={ref} className={cn("relative w-full", PLOT_TOUCH)} data-plot>
      <svg width={width} height={height} className="block overflow-visible [&_*]:transition-none">
        <g transform={`translate(${left}, ${M.top})`}>
          <ValueAxis ticks={yTicks} y={y} width={plotW} format={yTickFormat} />
          <TimeAxis ticks={timeTicks(x, plotW, xs)} top={plotH} />
          {layers.map((layer, li) => {
            const s = drawn[li]!;
            const bands = layer as unknown as Band[];
            return (
              <g key={s.key} data-layer data-color={s.color} data-shape={s.shape ?? "none"} aria-hidden>
                {single ? (
                  // One day has no extent: draw the day as a stacked column instead.
                  <rect x={positions[0]! - 6} width={12} y={y(bands[0]![1])} height={Math.max(0, y(bands[0]![0]) - y(bands[0]![1]))} className={PAINT[s.color].fill} />
                ) : (
                  <>
                    <path d={fill(bands) ?? ""} className={PAINT[s.color].tint} />
                    <path d={edge(bands) ?? ""} className={cn("fill-none", PAINT[s.color].stroke)} strokeWidth={1.5} strokeLinejoin="round" strokeDasharray={s.dashed ? OTHER_DASH : undefined} />
                  </>
                )}
                {many && s.shape ? at.map((i) => <SeriesMarker key={i} shape={s.shape!} x={positions[i]!} y={y(bands[i]![1])} r={3.5} color={s.color} surface={surface} />) : null}
              </g>
            );
          })}
          {/* Names inside the bands that are tall enough, at the right edge, on a surface halo. */}
          {many && !single
            ? layers.map((layer, li) => {
                const s = drawn[li]!;
                const band = (layer as unknown as Band[])[lastI]!;
                const top = y(band[1]);
                const h = y(band[0]) - top;
                if (h < LABEL_BAND || textWidth(s.label) > plotW * 0.6) return null;
                return (
                  <text
                    key={s.key}
                    data-band-label
                    x={plotW - 10}
                    y={top + h / 2}
                    dy="0.32em"
                    textAnchor="end"
                    className={cn("pointer-events-none fill-foreground text-meta [paint-order:stroke]", SURFACE[surface].ring)}
                    strokeWidth={3}
                    strokeLinejoin="round"
                    aria-hidden
                  >
                    {s.label}
                  </text>
                );
              })
            : null}
          {active !== null ? <line x1={positions[active]} x2={positions[active]} y1={0} y2={plotH} className={marks.focused ? "stroke-ring" : "stroke-muted-foreground"} strokeWidth={1} shapeRendering="crispEdges" /> : null}
          <PlotHit width={plotW} height={plotH} positions={positions} marks={marks} />
          <g data-chart-marks>
            {xs.map((t, i) => (
              <SvgMark key={t} marks={marks} index={i} label={describe(i)}>
                <rect x={positions[i]! - 1} y={0} width={2} height={plotH} className="pointer-events-none fill-transparent" />
              </SvgMark>
            ))}
          </g>
        </g>
      </svg>
      {active !== null ? (
        <ChartTooltip x={left + positions[active]!} y={M.top} placement="side" containerWidth={width} heading={xFormat(new Date(xs[active]!))} rows={tooltipRows(active)} />
      ) : null}
    </div>
  );
}
