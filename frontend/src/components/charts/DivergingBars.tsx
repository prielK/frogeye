import { scaleBand, scaleLinear } from "d3-scale";
import { plainDay } from "@/lib/time";
import { cn } from "@/lib/utils";
import { ChartFrame, PLOT_TOUCH, PlotHit, SvgMark, type FrameProps } from "./ChartFrame";
import { barPath, dateDay, formatDate, PAINT, spaceTicks, textWidth, useChartWidth, useMarks, type ChartColor } from "./scales";
import { ChartTooltip } from "./tooltip";

/*
 * Two opposing measures per period on one baseline (a product's pressure: opportunity above,
 * threat below). The side, the word at each end of the axis and its arrow carry the meaning;
 * the meaning tokens only confirm it. One scale for both sides, so a tall bar is a big value on
 * either. Columns are at most 24px, rounded at the data end, square at the baseline.
 */

export type DivergingRow = { key: string; x: Date; above: number; below: number };
type Side = { label: string; color: ChartColor };

export type DivergingBarsProps = FrameProps & {
  rows: DivergingRow[];
  sides: { above: Side; below: Side };
  valueFormat?: (n: number) => string;
  /** The period, exactly: default "Week of 14 Sep 2026". */
  xFormat?: (d: Date) => string;
  /** Axis labels: default "14 Sep". */
  tickFormat?: (d: Date) => string;
  height?: number;
};

const oneDecimal = new Intl.NumberFormat("en-US", { maximumFractionDigits: 1 });
const M = { top: 22, right: 8, bottom: 46 };

export function DivergingBars({
  rows,
  sides,
  valueFormat = (n) => oneDecimal.format(n),
  xFormat = (d) => `Week of ${formatDate(d)}`,
  tickFormat = (d) => plainDay(dateDay(d), "dayMonth"),
  height = 240,
  ...frame
}: DivergingBarsProps) {
  const isEmpty = rows.length === 0 || rows.every((r) => r.above === 0 && r.below === 0);
  const table = {
    columns: ["Period", sides.above.label, sides.below.label],
    rows: rows.map((r) => [xFormat(r.x), valueFormat(r.above), valueFormat(r.below)]),
  };
  return (
    <ChartFrame {...frame} empty={isEmpty ? frame.empty : null} table={isEmpty ? undefined : table}>
      {isEmpty ? null : <DivergingPlot rows={rows} sides={sides} valueFormat={valueFormat} xFormat={xFormat} tickFormat={tickFormat} height={height} />}
    </ChartFrame>
  );
}

type PlotProps = { rows: DivergingRow[]; sides: { above: Side; below: Side }; valueFormat: (n: number) => string; xFormat: (d: Date) => string; tickFormat: (d: Date) => string; height: number };

function DivergingPlot({ rows, sides, valueFormat, xFormat, tickFormat, height }: PlotProps) {
  const { ref, width } = useChartWidth();
  const marks = useMarks(rows.length, rows.length - 1);
  const plotH = height - M.top - M.bottom;
  const peak = Math.max(...rows.map((r) => Math.max(r.above, r.below)));
  const y = scaleLinear().domain([-peak, peak]).range([plotH, 0]).nice(4);
  const ticks = y.ticks(4);
  const left = Math.ceil(Math.max(...ticks.map((t) => textWidth(valueFormat(Math.abs(t)))))) + 12;
  const plotW = Math.max(48, width - left - M.right);
  const band = scaleBand<string>()
    .domain(rows.map((r) => r.key))
    .range([0, plotW])
    .paddingInner(0.3)
    .paddingOuter(0.15);
  const barW = Math.min(24, band.bandwidth());
  const zero = y(0);
  const xTicks = spaceTicks(
    rows.map((r) => ({ x: band(r.key)! + band.bandwidth() / 2, label: tickFormat(r.x) })),
    plotW,
  );

  const active = marks.active === null ? null : rows[marks.active]!;
  const tooltipRows = (r: DivergingRow) => [
    { key: "above", value: valueFormat(r.above), label: sides.above.label, color: sides.above.color, shape: "rect" as const },
    { key: "below", value: valueFormat(r.below), label: sides.below.label, color: sides.below.color, shape: "rect" as const },
  ];

  return (
    <div ref={ref} className={cn("relative w-full", PLOT_TOUCH)} data-plot>
      <svg width={width} height={height} className="block overflow-visible [&_*]:transition-none">
        <g transform={`translate(${left}, ${M.top})`}>
          <g aria-hidden>
            {ticks.map((t) => (
              <g key={t}>
                <line x1={0} x2={plotW} y1={y(t)} y2={y(t)} className={t === 0 ? "stroke-input" : "stroke-border"} shapeRendering="crispEdges" />
                <text x={-8} y={y(t)} dy="0.32em" textAnchor="end" className="fill-muted-foreground text-meta" data-tick="y">
                  {valueFormat(Math.abs(t))}
                </text>
              </g>
            ))}
            <SideLabel x={0} y={-10} up label={sides.above.label} color={sides.above.color} />
            <SideLabel x={0} y={plotH + 40} up={false} label={sides.below.label} color={sides.below.color} />
            {xTicks.map((t) => (
              <text key={`${t.x}`} x={t.x} y={plotH + 18} textAnchor={t.anchor} className="fill-muted-foreground text-meta" data-tick="x">
                {t.label}
              </text>
            ))}
          </g>
          <g aria-hidden>
            {rows.map((r) => {
              const cx = band(r.key)! + band.bandwidth() / 2;
              const on = active?.key === r.key;
              return (
                <g key={r.key} className={on || active === null ? "opacity-100" : "opacity-60"}>
                  {r.above > 0 ? <path data-bar="above" d={barPath(cx - barW / 2, y(r.above), barW, zero - y(r.above), "top")} className={PAINT[sides.above.color].fill} /> : null}
                  {r.below > 0 ? <path data-bar="below" d={barPath(cx - barW / 2, zero, barW, y(-r.below) - zero, "bottom")} className={PAINT[sides.below.color].fill} /> : null}
                </g>
              );
            })}
          </g>
          <PlotHit width={plotW} height={plotH} positions={rows.map((r) => band(r.key)! + band.bandwidth() / 2)} marks={marks} />
          <g data-chart-marks>
            {rows.map((r, i) => {
              const x0 = band(r.key)! - (band.step() - band.bandwidth()) / 2;
              return (
                <SvgMark key={r.key} marks={marks} index={i} label={`${xFormat(r.x)}: ${sides.above.label} ${valueFormat(r.above)}, ${sides.below.label} ${valueFormat(r.below)}`}>
                  <rect x={x0} y={0} width={band.step()} height={plotH} className="pointer-events-none fill-transparent" />
                  {marks.active === i && marks.focused ? <rect x={x0 + 1} y={1} width={band.step() - 2} height={plotH - 2} rx={4} className="fill-none stroke-ring" strokeWidth={2} /> : null}
                </SvgMark>
              );
            })}
          </g>
        </g>
      </svg>
      {active ? (
        <ChartTooltip
          x={left + band(active.key)! + band.bandwidth() / 2}
          y={M.top + y(Math.max(active.above, 0))}
          bottom={M.top + y(-Math.max(active.below, 0))}
          containerWidth={width}
          heading={xFormat(active.x)}
          rows={tooltipRows(active)}
        />
      ) : null}
    </div>
  );
}

/** The side's name with a drawn arrow pointing away from the baseline. */
function SideLabel({ x, y, up, label, color }: { x: number; y: number; up: boolean; label: string; color: ChartColor }) {
  return (
    <g transform={`translate(${x}, ${y})`}>
      <polygon points={up ? "0,3 5,-4 10,3" : "0,-4 10,-4 5,3"} className={PAINT[color].fill} />
      <text x={15} y={0} dy="0.32em" className="fill-foreground text-meta font-medium">
        {label}
      </text>
    </g>
  );
}
