import { curveStepAfter, line as d3line } from "d3-shape";
import { useMemo } from "react";
import { DAY } from "@/lib/days";
import { cn } from "@/lib/utils";
import { ChartFrame, SvgMark, TimeAxis, ValueAxis, type FrameProps, type LegendItem } from "./ChartFrame";
import { barPath, formatDate, PAINT, SURFACE, textWidth, timeScale, timeTicks, UNKNOWN_DASH, useChartWidth, useMarks, valueScale, valueTicks, type Surface } from "./scales";
import { ChartTooltip, type TooltipRow } from "./tooltip";

/*
 * A running total that moves in steps (funding raised): the total as a step line that rises at
 * each dated step and a thin column per step for its own amount, on one axis (one unit). It
 * never prints a total it does not know:
 * - a step whose amount was not disclosed adds nothing and is a DASHED ring (the kit's "value not
 *   known" idiom); from it on, the total is a lower bound, "≥ $22M", and the line is the
 *   "Disclosed total";
 * - before the first disclosed amount there is no total at all (never "≥ $0"): the line starts at
 *   the first step that states one, and earlier rounds sit as dashed rings on the date axis;
 * - when no step states an amount there is no money axis and no total at all: a bare time axis,
 *   the dashed rings and the words "Amounts not disclosed";
 * - a step whose sources disagree is a hollow SOLID ring, as in DotTimeline.
 */

export type Step = {
  key: string;
  x: Date;
  /** The step's own amount; null when it was not disclosed. */
  amount: number | null;
  label: string;
  /** Its sources state different values (`FundingRoundOut.sources_disagree`). */
  disputed?: boolean;
  /** A line of context for the tooltip: lead investor, source. */
  detail?: string;
};

export type StepLineProps = FrameProps & {
  steps: Step[];
  /** Exact amounts: "$45M". */
  valueFormat: (n: number) => string;
  /** Axis ticks; default `valueFormat`, so the unit stays on the axis. */
  tickFormat?: (n: number) => string;
  xFormat?: (d: Date) => string;
  /** Carry the final total to this day (today); default a little past the last step. */
  until?: Date;
  height?: number;
  /** Legend names. */
  amountLabel?: string;
  /** The total when every step states its amount. */
  totalLabel?: string;
  /** The total when some step does not: a lower bound. */
  disclosedLabel?: string;
};

const M = { top: 14, bottom: 28 };
const TOTAL = "chart-1" as const;
const AMOUNT = "chart-2" as const;
const NONE_DISCLOSED = "Amounts not disclosed";

/**
 * Running totals of the stated amounts. Before the first stated amount there is no total (`text`
 * null: nothing is known, so nothing is printed); after any undisclosed step it is a lower bound, "≥".
 */
function totalsOf(steps: Step[], valueFormat: (n: number) => string) {
  const firstKnown = steps.findIndex((s) => s.amount !== null);
  const firstUnknown = steps.findIndex((s) => s.amount === null);
  let sum = 0;
  return steps.map((s, i) => {
    sum += s.amount ?? 0;
    const known = firstKnown !== -1 && i >= firstKnown;
    const bound = known && firstUnknown !== -1 && firstUnknown <= i;
    return { value: sum, known, bound, text: known ? `${bound ? "≥ " : ""}${valueFormat(sum)}` : null };
  });
}

export function StepLine({
  steps,
  valueFormat,
  tickFormat = valueFormat,
  xFormat = formatDate,
  until,
  height = 220,
  amountLabel = "Round amount",
  totalLabel = "Total raised",
  disclosedLabel = "Disclosed total",
  surface = "background",
  ...frame
}: StepLineProps) {
  const sorted = useMemo(() => [...steps].sort((a, b) => a.x.getTime() - b.x.getTime()), [steps]);
  const isEmpty = sorted.length === 0;
  const undisclosed = sorted.some((s) => s.amount === null);
  const noneDisclosed = !isEmpty && sorted.every((s) => s.amount === null);
  const lineLabel = undisclosed ? disclosedLabel : totalLabel;
  const legend: LegendItem[] = [
    ...(noneDisclosed
      ? []
      : [
          { key: "total", label: lineLabel, color: TOTAL, shape: "line" as const },
          { key: "amount", label: amountLabel, color: AMOUNT, shape: "rect" as const },
        ]),
    ...(undisclosed ? [{ key: "undisclosed", label: "Amount not disclosed", color: TOTAL, shape: "dashed-ring" as const }] : []),
    ...(sorted.some((s) => s.disputed && s.amount !== null) ? [{ key: "disputed", label: "Sources disagree", color: TOTAL, shape: "ring" as const }] : []),
  ];
  const totals = totalsOf(sorted, valueFormat);
  const table = {
    columns: ["Date", "Step", amountLabel, lineLabel],
    rows: sorted.map((s, i) => [
      xFormat(s.x),
      s.label,
      s.amount === null ? "Not disclosed" : `${valueFormat(s.amount)}${s.disputed ? " (sources disagree)" : ""}`,
      totals[i]!.text ?? "—",
    ]),
  };
  return (
    <ChartFrame {...frame} surface={surface} empty={isEmpty ? frame.empty : null} legend={isEmpty ? undefined : legend} table={isEmpty ? undefined : table}>
      {isEmpty ? null : (
        <StepPlot
          steps={sorted}
          valueFormat={valueFormat}
          tickFormat={tickFormat}
          xFormat={xFormat}
          until={until}
          height={noneDisclosed ? 96 : height}
          amountLabel={amountLabel}
          lineLabel={lineLabel}
          noneDisclosed={noneDisclosed}
          surface={surface}
        />
      )}
    </ChartFrame>
  );
}

type PlotProps = {
  steps: Step[];
  valueFormat: (n: number) => string;
  tickFormat: (n: number) => string;
  xFormat: (d: Date) => string;
  until?: Date;
  height: number;
  amountLabel: string;
  lineLabel: string;
  noneDisclosed: boolean;
  surface: Surface;
};

function StepPlot({ steps, valueFormat, tickFormat, xFormat, until, height, amountLabel, lineLabel, noneDisclosed, surface }: PlotProps) {
  const { ref, width } = useChartWidth();
  const marks = useMarks(steps.length, steps.length - 1);
  const totals = totalsOf(steps, valueFormat);
  const final = totals.at(-1)!;
  const plotH = height - M.top - M.bottom;
  const y = valueScale(final.value, plotH);
  // No stated amount: no money axis at all, never a $0 the data did not set.
  const yTicks = noneDisclosed ? [] : valueTicks(y, plotH, false);
  const left = noneDisclosed ? 12 : Math.ceil(Math.max(...yTicks.map((t) => textWidth(tickFormat(t))))) + 12;
  const endLabel = final.text === null ? "" : final.bound ? `${final.text} disclosed` : final.text;
  const right = !noneDisclosed && width >= 420 ? Math.ceil(textWidth(endLabel)) + 18 : 12;
  const plotW = Math.max(48, width - left - right);
  const first = steps[0]!.x.getTime();
  const last = steps.at(-1)!.x.getTime();
  const end = Math.max(until?.getTime() ?? 0, last + Math.max((last - first) * 0.06, 20 * DAY));
  const x = timeScale([first, end], plotW, 0.03);
  const xOf = (d: Date) => x(d);
  // Steps before any stated amount sit on the date axis; the rest on the total line.
  const cyOf = (i: number) => (noneDisclosed ? plotH / 2 : totals[i]!.known ? y(totals[i]!.value) : plotH);
  const firstKnown = Math.max(0, totals.findIndex((t) => t.known));

  // (x0, 0) → up to the first total, along to each next step, up again, and on to `end`.
  const path = [
    { x: steps[firstKnown]!.x, y: 0 },
    ...steps.slice(firstKnown).map((s, k) => ({ x: s.x, y: totals[firstKnown + k]!.value })),
    { x: new Date(end), y: final.value },
  ];
  const stepLine = d3line<{ x: Date; y: number }>()
    .x((p) => xOf(p.x))
    .y((p) => y(p.y))
    .curve(curveStepAfter);

  const colW = Math.max(4, Math.min(8, plotW / Math.max(steps.length * 4, 1)));
  const active = marks.active;
  const rowsFor = (i: number): TooltipRow[] => {
    const s = steps[i]!;
    return [
      s.amount === null
        ? { key: "amount", value: "Amount not disclosed", color: TOTAL, shape: "dashed-ring" }
        : { key: "amount", value: valueFormat(s.amount), label: amountLabel, color: AMOUNT, shape: "rect" },
      ...(totals[i]!.text === null ? [] : [{ key: "total", value: totals[i]!.text, label: lineLabel.toLowerCase(), color: TOTAL, shape: "line" as const }]),
    ];
  };
  const noteFor = (i: number) => [steps[i]!.disputed ? "Sources disagree." : null, steps[i]!.detail].filter(Boolean).join(" ") || undefined;
  const describe = (i: number) => {
    const s = steps[i]!;
    const amount = s.amount === null ? "amount not disclosed" : `${valueFormat(s.amount)} ${amountLabel.toLowerCase()}`;
    const total = totals[i]!.text;
    return `${s.label}, ${xFormat(s.x)}: ${amount}${total === null ? "" : `, ${total} ${lineLabel.toLowerCase()}`}${s.disputed ? ", sources disagree" : ""}`;
  };

  return (
    <div ref={ref} className="relative w-full" data-plot>
      <svg width={width} height={height} className="block overflow-visible [&_*]:transition-none">
        <g transform={`translate(${left}, ${M.top})`}>
          {noneDisclosed ? (
            <>
              <line x1={0} x2={plotW} y1={plotH} y2={plotH} className="stroke-input" shapeRendering="crispEdges" aria-hidden />
              <text x={0} y={2} dy="0.32em" className="fill-muted-foreground text-meta" aria-hidden>
                {NONE_DISCLOSED}
              </text>
            </>
          ) : (
            <ValueAxis ticks={yTicks} y={y} width={plotW} format={tickFormat} />
          )}
          <TimeAxis ticks={timeTicks(x, plotW)} top={plotH} />
          {noneDisclosed
            ? null
            : steps.map((s) =>
                s.amount !== null && s.amount > 0 ? (
                  // Just left of the riser, so the total's rise never splits its own column.
                  <path key={s.key} data-column d={barPath(xOf(s.x) - colW - 2, y(s.amount), colW, plotH - y(s.amount), "top")} className={PAINT[AMOUNT].fill} aria-hidden />
                ) : null,
              )}
          {noneDisclosed ? null : <path d={stepLine(path) ?? ""} data-line data-color={TOTAL} className={cn("fill-none", PAINT[TOTAL].stroke)} strokeWidth={2} strokeLinejoin="round" aria-hidden />}
          {right > 12 ? (
            <text x={plotW + 8} y={y(final.value)} dy="0.32em" data-end-label className="fill-foreground text-meta font-medium" aria-hidden>
              {endLabel}
            </text>
          ) : null}
          <g data-chart-marks>
            {steps.map((s, i) => {
              const cx = xOf(s.x);
              const cy = cyOf(i);
              const on = active === i;
              const state = s.amount === null ? "undisclosed" : s.disputed ? "disputed" : "disclosed";
              return (
                <SvgMark key={s.key} marks={marks} index={i} label={describe(i)}>
                  <circle cx={cx} cy={cy} r={14} className="fill-transparent" />
                  {on && marks.focused ? <circle cx={cx} cy={cy} r={9.5} className="fill-none stroke-ring" strokeWidth={2} /> : null}
                  <circle
                    data-state={state}
                    cx={cx}
                    cy={cy}
                    r={on ? 5.5 : 4.5}
                    className={state === "disclosed" ? cn(PAINT[TOTAL].fill, SURFACE[surface].ring) : cn(SURFACE[surface].fill, PAINT[TOTAL].stroke)}
                    strokeWidth={2}
                    strokeDasharray={state === "undisclosed" ? UNKNOWN_DASH : undefined}
                  />
                </SvgMark>
              );
            })}
          </g>
        </g>
      </svg>
      {active !== null ? (
        <ChartTooltip
          x={left + xOf(steps[active]!.x)}
          y={M.top + cyOf(active) - 7}
          bottom={M.top + cyOf(active) + 7}
          containerWidth={width}
          heading={`${steps[active]!.label} · ${xFormat(steps[active]!.x)}`}
          rows={rowsFor(active)}
          note={noteFor(active)}
        />
      ) : null}
    </div>
  );
}
