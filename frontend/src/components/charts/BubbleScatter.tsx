import { scaleLinear, scaleSqrt } from "d3-scale";
import { symbol, symbolCircle, symbolDiamond, symbolSquare, symbolTriangle, type SymbolType } from "d3-shape";
import { cn } from "@/lib/utils";
import { ChartFrame, SvgMark, ValueAxis, type FrameProps, type LegendItem } from "./ChartFrame";
import { formatValue, OTHER_COLOR, PAINT, SERIES_COLORS, spaceTicks, SURFACE, textWidth, UNKNOWN_DASH, useChartWidth, useMarks, type ChartColor, type Surface } from "./scales";
import { ChartTooltip, type SwatchShape } from "./tooltip";

/*
 * Two measures per entity (the competitor landscape: overlap against momentum), a bubble each,
 * its area a third measure. Groups (up to four; the palette is validated on all pairs for its
 * first four slots) wear a colour AND a shape, so a group never rests on colour. The biggest
 * bubbles are named on the chart where a label fits; every bubble names itself on hover/focus.
 */

export type BubblePoint = {
  key: string;
  label: string;
  x: number;
  y: number;
  /**
   * The bubble's area (open roles). Null or absent while `size` is set means the size is not
   * known (no job board): a hollow, dashed, neutral marker, never a small filled bubble.
   */
  size?: number | null;
  /** Why this point's size is not known, when it differs from `size.missingLabel` (a board found but not fetched yet). */
  missingLabel?: string;
  group?: string;
  detail?: string;
  href?: string;
};

export type Axis = { label: string; format?: (n: number) => string; domain?: [number, number] };

export type BubbleScatterProps = FrameProps & {
  points: BubblePoint[];
  x: Axis;
  y: Axis;
  /** The third measure, as area. `missingLabel` names a point whose size is not known: "No job board". */
  size?: { label: string; format?: (n: number) => string; missingLabel?: string };
  /**
   * Named groups in slot order. The kit has four categorical colours and shapes plus a neutral
   * "Other" and no more: a point with another group, or past the fourth, falls to "Other".
   */
  groups?: { key: string; label: string }[];
  /** Reference lines (an average, a threshold), named on the chart. */
  xRef?: { value: number; label: string };
  yRef?: { value: number; label: string };
  /** Corner captions for the quadrants the reference lines cut. */
  quadrants?: { topLeft?: string; topRight?: string; bottomLeft?: string; bottomRight?: string };
  height?: number;
  onNavigate?: (href: string) => void;
};

const SHAPES: { type: SymbolType; swatch: SwatchShape }[] = [
  { type: symbolCircle, swatch: "dot" },
  { type: symbolSquare, swatch: "rect" },
  { type: symbolDiamond, swatch: "diamond" },
  { type: symbolTriangle, swatch: "triangle" },
];

export function BubbleScatter({ points, x, y, size, groups = [], xRef, yRef, quadrants, height = 340, onNavigate, surface = "background", ...frame }: BubbleScatterProps) {
  const isEmpty = points.length === 0;
  const named = groups.slice(0, 4);
  const slots = new Map(named.map((g, i) => [g.key, { color: SERIES_COLORS[i]!, shape: i }]));
  const style = (group?: string) => (group !== undefined ? slots.get(group) : undefined) ?? { color: named.length ? OTHER_COLOR : SERIES_COLORS[0]!, shape: 0 };
  const hasOther = named.length > 0 && points.some((p) => !named.some((g) => g.key === p.group));
  const missing = size?.missingLabel ?? "Size unknown";
  const unknown = (p: BubblePoint) => size !== undefined && (p.size === null || p.size === undefined);
  const missingOf = (p: BubblePoint) => p.missingLabel ?? missing;
  // The legend names the groups drawn; a group keeps its slot whether or not the others are on screen.
  const items: LegendItem[] = [
    ...named.flatMap((g, i) => (points.some((p) => p.group === g.key) ? [{ key: g.key, label: g.label, color: SERIES_COLORS[i]!, shape: SHAPES[i]!.swatch }] : [])),
    ...(hasOther ? [{ key: "__other", label: "Other", color: OTHER_COLOR, shape: "dot" as const }] : []),
    ...(points.some(unknown) ? [{ key: "__unknown", label: missing, color: OTHER_COLOR, shape: "dashed-ring" as const }] : []),
  ];
  const legend = items.length ? items : undefined;
  const fx = x.format ?? formatValue;
  const fy = y.format ?? formatValue;
  const fs = size?.format ?? formatValue;
  const groupName = (key?: string) => named.find((g) => g.key === key)?.label;
  const table = {
    columns: ["Name", x.label, y.label, ...(size ? [size.label] : []), ...(named.length ? ["Group"] : [])],
    rows: points.map((p) => [p.label, fx(p.x), fy(p.y), ...(size ? [unknown(p) ? missingOf(p) : fs(p.size!)] : []), ...(named.length ? [groupName(p.group) ?? "Other"] : [])]),
  };
  return (
    <ChartFrame {...frame} surface={surface} empty={isEmpty ? frame.empty : null} legend={isEmpty ? undefined : legend} table={isEmpty ? undefined : table}>
      {isEmpty ? null : (
        <BubblePlot
          points={points}
          x={x}
          y={y}
          size={size}
          unknown={unknown}
          missingOf={missingOf}
          fx={fx}
          fy={fy}
          fs={fs}
          style={style}
          groupName={groupName}
          xRef={xRef}
          yRef={yRef}
          quadrants={quadrants}
          height={height}
          onNavigate={onNavigate}
          surface={surface}
        />
      )}
    </ChartFrame>
  );
}

type PlotProps = Pick<BubbleScatterProps, "points" | "x" | "y" | "size" | "xRef" | "yRef" | "quadrants" | "onNavigate"> & {
  unknown: (p: BubblePoint) => boolean;
  missingOf: (p: BubblePoint) => string;
  fx: (n: number) => string;
  fy: (n: number) => string;
  fs: (n: number) => string;
  style: (group?: string) => { color: ChartColor; shape: number };
  groupName: (key?: string) => string | undefined;
  height: number;
  surface: Surface;
};

const M = { top: 32, right: 16, bottom: 44 };

function extent(values: number[], ref?: number): [number, number] {
  const all = ref === undefined ? values : [...values, ref];
  const lo = Math.min(...all);
  const hi = Math.max(...all);
  const pad = (hi - lo || Math.abs(hi) || 1) * 0.08;
  return [lo - pad, hi + pad];
}

function BubblePlot({ points, x, y, size, unknown, missingOf, fx, fy, fs, style, groupName, xRef, yRef, quadrants, height, onNavigate, surface }: PlotProps) {
  const { ref, width } = useChartWidth();
  // Big bubbles drawn first, so small ones stay on top and reachable; the keyboard follows the given order.
  const drawOrder = [...points].sort((a, b) => (b.size ?? 0) - (a.size ?? 0));
  const readOrder = points;
  const marks = useMarks(points.length, 0);
  const plotH = height - M.top - M.bottom;
  const yScale = scaleLinear()
    .domain(y.domain ?? extent(points.map((p) => p.y), yRef?.value))
    .range([plotH, 0])
    .nice(5);
  const yTicks = yScale.ticks(Math.max(3, Math.floor(plotH / 56)));
  const left = Math.ceil(Math.max(...yTicks.map((t) => textWidth(fy(t))))) + 12;
  const plotW = Math.max(60, width - left - M.right);
  const xScale = scaleLinear()
    .domain(x.domain ?? extent(points.map((p) => p.x), xRef?.value))
    .range([0, plotW])
    .nice(5);
  const xTicks = spaceTicks(
    xScale.ticks(Math.max(2, Math.floor(plotW / 90))).map((t) => ({ x: xScale(t), label: fx(t) })),
    plotW,
  );
  const sizes = points.map((p) => p.size ?? 0);
  const maxR = width < 480 ? 16 : 24;
  const r = scaleSqrt().domain([0, Math.max(1, ...sizes)]).range([5, maxR]);
  const radius = (p: BubblePoint) => (unknown(p) ? 7 : p.size === null || p.size === undefined ? 6 : r(p.size));
  const pos = (p: BubblePoint) => ({ cx: xScale(p.x), cy: yScale(p.y), rad: radius(p) });

  // Annotations take their place first (top quadrant captions in the margin, the rest inside on a
  // surface halo), clear of every bubble: each tries its places in turn (a reference line's label
  // right of the plot above the line, then below it, then at the left) and is left out when none
  // is free. Then the biggest bubbles are named, right of the bubble or else left of it, only where
  // the name collides with nothing. Every bubble still names itself on hover and focus.
  type Box = [number, number, number, number];
  type Text = { key: string; x: number; y: number; anchor: "start" | "end"; text: string };
  const overlaps = (a: Box, b: Box) => a[0] < b[2] && a[2] > b[0] && a[1] < b[3] && a[3] > b[1];
  const boxOf = (t: Text): Box => {
    const w = textWidth(t.text);
    return t.anchor === "start" ? [t.x, t.y - 8, t.x + w, t.y + 8] : [t.x - w, t.y - 8, t.x, t.y + 8];
  };
  const bubbles = new Map(
    drawOrder.map((p) => {
      const { cx, cy, rad } = pos(p);
      return [p.key, [cx - rad, cy - rad, cx + rad, cy + rad] as Box];
    }),
  );
  const taken: Box[] = [];
  const notes: Text[] = [];
  const note = (...places: Text[]) => {
    for (const t of places) {
      const box = boxOf(t);
      if (box[0] < -left || box[2] > plotW + M.right) continue;
      if (taken.some((o) => overlaps(o, box)) || [...bubbles.values()].some((b) => overlaps(b, box))) continue;
      notes.push(t);
      taken.push(box);
      return;
    }
  };
  taken.push(boxOf({ key: "y", x: -left, y: -20, anchor: "start", text: y.label }));
  if (quadrants?.topRight) note({ key: "q-tr", x: plotW, y: -20, anchor: "end", text: quadrants.topRight });
  if (quadrants?.topLeft) note({ key: "q-tl", x: -left + textWidth(y.label) + 20, y: -20, anchor: "start", text: quadrants.topLeft });
  if (quadrants?.bottomLeft) note({ key: "q-bl", x: 6, y: plotH - 10, anchor: "start", text: quadrants.bottomLeft });
  if (quadrants?.bottomRight) note({ key: "q-br", x: plotW - 6, y: plotH - 10, anchor: "end", text: quadrants.bottomRight });
  if (yRef) {
    const at = yScale(yRef.value);
    note(
      { key: "ref-y", x: plotW, y: at - 9, anchor: "end", text: yRef.label },
      { key: "ref-y", x: plotW, y: at + 10, anchor: "end", text: yRef.label },
      { key: "ref-y", x: 6, y: at - 9, anchor: "start", text: yRef.label },
      { key: "ref-y", x: 6, y: at + 10, anchor: "start", text: yRef.label },
    );
  }
  if (xRef) note({ key: "ref-x", x: xScale(xRef.value) + 4, y: 10, anchor: "start", text: xRef.label });

  const labels: Text[] = [];
  for (const p of drawOrder) {
    const { cx, cy, rad } = pos(p);
    for (const anchor of ["start", "end"] as const) {
      const t: Text = { key: p.key, x: anchor === "start" ? cx + rad + 4 : cx - rad - 4, y: cy, anchor, text: p.label };
      const box = boxOf(t);
      const inside = box[0] >= 0 && box[2] <= plotW && box[1] >= 0 && box[3] <= plotH;
      const clear = [...bubbles].every(([key, b]) => key === p.key || !overlaps(b, box)) && !taken.some((o) => overlaps(o, box));
      if (inside && clear) {
        labels.push(t);
        taken.push(box);
        break;
      }
    }
  }
  const halo = cn("[paint-order:stroke]", SURFACE[surface].ring);

  const active = marks.active === null ? null : readOrder[marks.active]!;
  const describe = (p: BubblePoint) =>
    [p.label, `${x.label} ${fx(p.x)}`, `${y.label} ${fy(p.y)}`, size ? `${size.label} ${unknown(p) ? missingOf(p).toLowerCase() : fs(p.size!)}` : null, groupName(p.group)].filter(Boolean).join(", ");
  const glyph = (p: BubblePoint, rad: number) =>
    symbol()
      .type(SHAPES[style(p.group).shape]!.type)
      .size(Math.PI * rad * rad)() ?? "";

  return (
    <div ref={ref} className="relative w-full" data-plot>
      <svg width={width} height={height} className="block overflow-visible [&_*]:transition-none">
        <g transform={`translate(${left}, ${M.top})`}>
          <ValueAxis ticks={yTicks} y={yScale} width={plotW} format={fy} />
          <g aria-hidden>
            <line x1={0} x2={plotW} y1={plotH} y2={plotH} className="stroke-input" shapeRendering="crispEdges" />
            {xTicks.map((t) => (
              <text key={t.label} x={t.x} y={plotH + 18} textAnchor={t.anchor} className="fill-muted-foreground text-meta" data-tick="x">
                {t.label}
              </text>
            ))}
            <text x={plotW} y={plotH + 38} textAnchor="end" className="fill-muted-foreground text-meta font-medium">
              {x.label}
            </text>
            <text x={-left} y={-20} dy="0.32em" className="fill-muted-foreground text-meta font-medium">
              {y.label}
            </text>
            {xRef ? <line x1={xScale(xRef.value)} x2={xScale(xRef.value)} y1={0} y2={plotH} className="stroke-input" shapeRendering="crispEdges" /> : null}
            {yRef ? <line x1={0} x2={plotW} y1={yScale(yRef.value)} y2={yScale(yRef.value)} className="stroke-input" shapeRendering="crispEdges" /> : null}
          </g>
          <g aria-hidden>
            {drawOrder.map((p) => {
              const { cx, cy, rad } = pos(p);
              const { color } = style(p.group);
              const on = active?.key === p.key;
              return (
                unknown(p) ? (
                  // Size not known: hollow, dashed, neutral, at a fixed size; its group keeps its shape.
                  <path
                    key={p.key}
                    d={glyph(p, on ? rad + 1.5 : rad)}
                    transform={`translate(${cx}, ${cy})`}
                    className={cn(SURFACE[surface].fill, PAINT[OTHER_COLOR].stroke)}
                    strokeWidth={1.5}
                    strokeDasharray={UNKNOWN_DASH}
                    data-bubble
                    data-unknown
                  />
                ) : (
                  <path
                    key={p.key}
                    d={glyph(p, on ? rad + 1.5 : rad)}
                    transform={`translate(${cx}, ${cy})`}
                    className={cn(on ? PAINT[color].fill : PAINT[color].soft, PAINT[color].stroke)}
                    strokeWidth={1.5}
                    data-bubble
                  />
                )
              );
            })}
            {notes.map((n) => (
              <text key={n.key} x={n.x} y={n.y} dy="0.32em" textAnchor={n.anchor} className={cn("fill-muted-foreground text-meta", halo)} strokeWidth={3} strokeLinejoin="round">
                {n.text}
              </text>
            ))}
            {labels.map((l) => (
              <text key={l.key} x={l.x} y={l.y} dy="0.32em" textAnchor={l.anchor} className={cn("pointer-events-none fill-foreground text-meta", halo)} strokeWidth={3} strokeLinejoin="round">
                {l.text}
              </text>
            ))}
          </g>
          <g data-chart-marks>
            {readOrder.map((p, i) => {
              const { cx, cy, rad } = pos(p);
              return (
                <SvgMark key={p.key} marks={marks} index={i} label={describe(p)} href={p.href} onNavigate={onNavigate}>
                  <circle cx={cx} cy={cy} r={Math.max(12, rad + 2)} className="fill-transparent" />
                  {marks.active === i && marks.focused ? <circle cx={cx} cy={cy} r={rad + 5} className="fill-none stroke-ring" strokeWidth={2} /> : null}
                </SvgMark>
              );
            })}
          </g>
        </g>
      </svg>
      {active ? (
        <ChartTooltip
          x={left + pos(active).cx}
          y={M.top + pos(active).cy - pos(active).rad - 2}
          bottom={M.top + pos(active).cy + pos(active).rad + 2}
          containerWidth={width}
          heading={active.label}
          rows={[
            { key: "x", value: fx(active.x), label: x.label },
            { key: "y", value: fy(active.y), label: y.label },
            ...(size
              ? [
                  unknown(active)
                    ? { key: "s", value: missingOf(active), label: size.label, color: OTHER_COLOR, shape: "dashed-ring" as const }
                    : { key: "s", value: fs(active.size!), label: size.label },
                ]
              : []),
            ...(groupName(active.group) ? [{ key: "g", value: groupName(active.group)!, color: style(active.group).color, shape: SHAPES[style(active.group).shape]!.swatch }] : []),
          ]}
          note={active.detail}
        />
      ) : null}
    </div>
  );
}
