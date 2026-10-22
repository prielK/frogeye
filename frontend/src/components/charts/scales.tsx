import { bisectCenter } from "d3-array";
import { scaleLinear, scaleUtc, type ScaleLinear, type ScaleTime } from "d3-scale";
import { symbol, symbolCircle, symbolDiamond, symbolSquare, symbolTriangle, type SymbolType } from "d3-shape";
import { useCallback, useEffect, useLayoutEffect, useRef, useState, type KeyboardEvent } from "react";
import { DAY } from "@/lib/days";
import { formatCompact, formatCount } from "@/lib/format";
import { plainDay } from "@/lib/time";

/*
 * The chart kit's shared parts: the colour roles, the date and number defaults, scales and
 * ticks, the top-N fold, the responsive width and the keyboard model. No components live here
 * (they are in ChartFrame and tooltip), so this module stays a plain helper set.
 */

// ---- colour roles --------------------------------------------------------------------

/**
 * The only colours a chart mark may wear. `chart-1..4` are the categorical slots, assigned in
 * order and never cycled; `chart-5` is "Other" (a recessive grey); the meaning tokens are for
 * meaning only (pressure, overlap tone), never "series 5"; `meter` is a value against a maximum.
 */
export type ChartColor = "chart-1" | "chart-2" | "chart-3" | "chart-4" | "chart-5" | "threat" | "opportunity" | "neutral" | "warning" | "meter";

/** Literal class names per role, so Tailwind sees every one of them. */
export const PAINT: Record<ChartColor, { stroke: string; fill: string; wash: string; tint: string; soft: string; bg: string; border: string }> = {
  "chart-1": { stroke: "stroke-chart-1", fill: "fill-chart-1", wash: "fill-chart-1/10", tint: "fill-chart-1/20", soft: "fill-chart-1/45", bg: "bg-chart-1", border: "border-chart-1" },
  "chart-2": { stroke: "stroke-chart-2", fill: "fill-chart-2", wash: "fill-chart-2/10", tint: "fill-chart-2/20", soft: "fill-chart-2/45", bg: "bg-chart-2", border: "border-chart-2" },
  "chart-3": { stroke: "stroke-chart-3", fill: "fill-chart-3", wash: "fill-chart-3/10", tint: "fill-chart-3/20", soft: "fill-chart-3/45", bg: "bg-chart-3", border: "border-chart-3" },
  "chart-4": { stroke: "stroke-chart-4", fill: "fill-chart-4", wash: "fill-chart-4/10", tint: "fill-chart-4/20", soft: "fill-chart-4/45", bg: "bg-chart-4", border: "border-chart-4" },
  "chart-5": { stroke: "stroke-chart-5", fill: "fill-chart-5", wash: "fill-chart-5/10", tint: "fill-chart-5/20", soft: "fill-chart-5/45", bg: "bg-chart-5", border: "border-chart-5" },
  threat: { stroke: "stroke-threat", fill: "fill-threat", wash: "fill-threat/10", tint: "fill-threat/20", soft: "fill-threat/45", bg: "bg-threat", border: "border-threat" },
  opportunity: { stroke: "stroke-opportunity", fill: "fill-opportunity", wash: "fill-opportunity/10", tint: "fill-opportunity/20", soft: "fill-opportunity/45", bg: "bg-opportunity", border: "border-opportunity" },
  neutral: { stroke: "stroke-neutral", fill: "fill-neutral", wash: "fill-neutral/10", tint: "fill-neutral/20", soft: "fill-neutral/45", bg: "bg-neutral", border: "border-neutral" },
  warning: { stroke: "stroke-warning", fill: "fill-warning", wash: "fill-warning/10", tint: "fill-warning/20", soft: "fill-warning/45", bg: "bg-warning", border: "border-warning" },
  meter: { stroke: "stroke-meter", fill: "fill-meter", wash: "fill-meter/10", tint: "fill-meter/20", soft: "fill-meter/45", bg: "bg-meter", border: "border-meter" },
};

/**
 * The kit has exactly four categorical colours plus a neutral "Other". A named series can
 * only ever wear one of the four; `chart-5` is never a named series.
 */
export type SeriesColor = "chart-1" | "chart-2" | "chart-3" | "chart-4";
/** The categorical order. The palette validates all pairs for these four in both themes. */
export const SERIES_COLORS: readonly SeriesColor[] = ["chart-1", "chart-2", "chart-3", "chart-4"];
export const OTHER_COLOR: ChartColor = "chart-5";
/** Named series a chart draws before the rest fold into "Other (n)": four, never more. */
export const MAX_SERIES = 4;
/** How many named series a chart may keep: 1 to 4. */
export type SeriesCap = 1 | 2 | 3 | 4;

/**
 * Each slot's marker shape, so series stay apart without colour at every width: the
 * dots along a line or a layer's edge, its legend key and its tooltip key all wear it. "Other"
 * has no shape; it is the dashed line.
 */
export type MarkerShape = "circle" | "square" | "triangle" | "diamond";
export const SERIES_SHAPES: Record<SeriesColor, MarkerShape> = { "chart-1": "circle", "chart-2": "square", "chart-3": "triangle", "chart-4": "diamond" };
/** The dash pattern of the "Other" line. */
export const OTHER_DASH = "5 4";
/** The dash pattern of a value that is not known (an undisclosed amount, an unknown size). */
export const UNKNOWN_DASH = "3 2";

const SYMBOLS: Record<MarkerShape, SymbolType> = { circle: symbolCircle, square: symbolSquare, triangle: symbolTriangle, diamond: symbolDiamond };

/** A marker's outline centred on 0,0, with the area of a circle of radius `r`. */
export function markerPath(shape: MarkerShape, r: number): string {
  return symbol(SYMBOLS[shape], Math.PI * r * r)() ?? "";
}

/**
 * Which points of a series carry a marker: every point when they are at least `gap` px apart,
 * else a regular sample that keeps them that far apart, always ending on the last point.
 */
export function markerIndices(px: number[], gap = 44): number[] {
  const out: number[] = [];
  let last = -Infinity;
  px.forEach((x, i) => {
    if (x - last >= gap) {
      out.push(i);
      last = x;
    }
  });
  const end = px.length - 1;
  if (end >= 0 && out.at(-1) !== end) {
    if (out.length > 1 && px[end]! - px[out.at(-1)!]! < gap / 2) out.pop();
    out.push(end);
  }
  return out;
}

/** The index of the position in sorted `px` nearest to `x` (the crosshair's day). */
export function nearestIndex(px: number[], x: number): number {
  return Math.max(0, Math.min(px.length - 1, bisectCenter(px, x)));
}

/** What the chart sits on: the 2px ring around dots and the hollow fill take its colour. */
export type Surface = "background" | "card";
export const SURFACE: Record<Surface, { ring: string; fill: string; bg: string }> = {
  background: { ring: "stroke-background", fill: "fill-background", bg: "bg-background" },
  card: { ring: "stroke-card", fill: "fill-card", bg: "bg-card" },
};

// ---- dates and numbers ----------------------------------------------------------------------


/**
 * A plain day ("2026-10-17", as the API sends it) as the UTC midnight the charts place on a
 * time axis. Days are never shifted into a zone: `dateDay` gives the same day back.
 */
export function dayDate(day: string): Date {
  const [y, m, d] = day.split("-").map(Number);
  return new Date(Date.UTC(y!, (m ?? 1) - 1, d ?? 1));
}

export function dateDay(date: Date): string {
  return date.toISOString().slice(0, 10);
}

/** "17 Oct 2026": a day exactly, for tooltips and tables. */
export const formatDate = (date: Date) => plainDay(dateDay(date), "date");
/** Exact values (tooltips, tables, direct labels): "1,240". */
export const formatValue = formatCount;
/** Axis ticks: "1.2K". */
export const formatTick = formatCompact;
/** A signed change with a real minus: "+3", "−2", "0". */
export const formatDelta = (n: number, format: (n: number) => string = formatCount) =>
  n > 0 ? `+${format(n)}` : n < 0 ? `−${format(-n)}` : format(0);

/** Rough width of `text` set in `text-meta` (13px Schibsted Grotesk), for label layout only. */
export function textWidth(text: string): number {
  return text.length * 7.1;
}

// ---- series ---------------------------------------------------------------------------------

export type TimePoint = { x: Date; y: number };
/**
 * A named series. `color` pins one of the four slots; there is no fifth. With `members` it is an
 * "Other" the caller built (its `points` are what to draw, e.g. its members' sum from the first
 * day every member is measured): the kit draws it as its own Other, outside the named cap,
 * names its members in the legend and unfolds them in the table twin. A caller that builds its
 * own Other passes at most `maxSeries` named series beside it.
 */
export type TimeSeries = { key: string; label: string; points: TimePoint[]; color?: SeriesColor; members?: TimeSeries[] };
export type DrawnSeries = {
  key: string;
  label: string;
  color: ChartColor;
  /** The slot's marker shape; null for "Other", which is dashed instead. */
  shape: MarkerShape | null;
  dashed: boolean;
  /** Sorted by time. */
  points: TimePoint[];
  /** y by x timestamp. */
  values: Map<number, number>;
  /** The labels of the series folded into this one ("Other"); empty otherwise. */
  folded: string[];
};

export const OTHER_KEY = "__other";

const total = (s: TimeSeries) => s.points.reduce((sum, p) => sum + p.y, 0);

/**
 * Series to draw: at most `max` named ones (1 to 4; anything larger is four) — the largest by
 * total — in the order given, coloured and shaped by slot in that order (an explicit `color`
 * wins), and the rest summed per x into "Other (n)". Order is the caller's to keep stable, so
 * colour follows the entity, not its rank. A named series never wears the "Other" grey. A
 * caller-built Other (`members`) is drawn as that Other, its points as given, its members counted
 * and named in it.
 */
export function foldSeries(series: TimeSeries[], max: number = MAX_SERIES): DrawnSeries[] {
  const cap = Math.max(1, Math.min(MAX_SERIES, Math.floor(max)));
  const built = series.filter((s) => s.members && s.points.length > 0);
  const live = series.filter((s) => !s.members && s.points.length > 0);
  let kept = live;
  let rest: TimeSeries[] = [];
  if (live.length > cap) {
    const keep = new Set([...live].sort((a, b) => total(b) - total(a)).slice(0, cap).map((s) => s.key));
    kept = live.filter((s) => keep.has(s.key));
    rest = live.filter((s) => !keep.has(s.key));
  }
  // An explicit slot wins once; the others take the free slots in order. Four series, four slots.
  const pinned = new Map<string, SeriesColor>();
  for (const s of kept) if (s.color && ![...pinned.values()].includes(s.color)) pinned.set(s.key, s.color);
  const free = SERIES_COLORS.filter((c) => ![...pinned.values()].includes(c));
  const drawn: DrawnSeries[] = kept.map((s) => {
    const points = [...s.points].sort((a, b) => a.x.getTime() - b.x.getTime());
    const color = pinned.get(s.key) ?? free.shift()!;
    return { key: s.key, label: s.label, color, shape: SERIES_SHAPES[color], dashed: false, points, values: new Map(points.map((p) => [p.x.getTime(), p.y])), folded: [] };
  });
  if (rest.length > 0 || built.length > 0) {
    const sums = new Map<number, number>();
    for (const s of [...rest, ...built]) for (const p of s.points) sums.set(p.x.getTime(), (sums.get(p.x.getTime()) ?? 0) + p.y);
    const points = [...sums].sort(([a], [b]) => a - b).map(([x, y]) => ({ x: new Date(x), y }));
    const members = [...rest, ...built.flatMap((s) => s.members!)];
    drawn.push({ key: OTHER_KEY, label: `Other (${members.length})`, color: OTHER_COLOR, shape: null, dashed: true, points, values: sums, folded: members.map((s) => s.label) });
  }
  return drawn;
}

/** A chart's table twin: a header row and the rows of exact values, as text. */
export type ChartTableData = { columns: string[]; rows: string[][] };

/** Rows per day, a column per original series (folded ones too, a caller-built Other's members): the exact values. */
export function seriesTable(series: TimeSeries[], xFormat: (d: Date) => string, yFormat: (n: number) => string): ChartTableData {
  const live = series.flatMap((s) => s.members ?? [s]).filter((s) => s.points.length > 0);
  const maps = live.map((s) => new Map(s.points.map((p) => [p.x.getTime(), p.y])));
  return {
    columns: ["Date", ...live.map((s) => s.label)],
    rows: unionX(live).map((x) => [xFormat(new Date(x)), ...maps.map((m) => (m.has(x) ? yFormat(m.get(x)!) : "—"))]),
  };
}

/** Every distinct x across the series, ascending. */
export function unionX(series: { points: TimePoint[] }[]): number[] {
  return [...new Set(series.flatMap((s) => s.points.map((p) => p.x.getTime())))].sort((a, b) => a - b);
}

// ---- scales and ticks -----------------------------------------------------------------------

/** A time scale over `xs`; a single day gets a day either side so it sits in the middle. */
export function timeScale(xs: number[], width: number, pad = 0): ScaleTime<number, number> {
  let lo = Math.min(...xs);
  let hi = Math.max(...xs);
  if (lo === hi) {
    lo -= DAY;
    hi += DAY;
  }
  const extra = (hi - lo) * pad;
  return scaleUtc().domain([new Date(lo - extra), new Date(hi + extra)]).range([0, width]);
}

/**
 * A value scale from zero (or `min`) to the data's maximum, rounded to clean ticks. Counts get
 * whole ticks only: half a posting is fake precision.
 */
export function valueScale(maxValue: number, height: number, { min = 0, integer = false }: { min?: number; integer?: boolean } = {}) {
  let hi = Math.max(maxValue, min);
  if (hi === min) hi = min + 1;
  if (integer && hi - min < 4) hi = min + Math.max(1, Math.ceil(hi - min));
  const scale = scaleLinear().domain([min, hi]).range([height, 0]).nice(Math.max(2, Math.floor(height / 44)));
  return scale;
}

export function valueTicks(scale: ScaleLinear<number, number>, height: number, integer: boolean): number[] {
  const ticks = scale.ticks(Math.max(2, Math.floor(height / 44)));
  return integer ? ticks.filter((t) => Number.isInteger(t)) : ticks;
}

/** Whole numbers only? (counts) */
export const allIntegers = (values: number[]) => values.every((v) => Number.isInteger(v));

export type Tick = { x: number; label: string; anchor: "start" | "middle" | "end" };

/**
 * Day ticks for a time axis, labelled "17 Oct" (or "Oct", "Jan 2027" over long spans), thinned
 * so no two labels touch and anchored inward at the edges so none is clipped.
 */
/** Up to this many distinct days, a time axis labels the series' own days (`timeTicks`). */
const FEW_POINTS = 6;

export function timeTicks(scale: ScaleTime<number, number>, width: number, xs?: number[]): Tick[] {
  const [a, b] = scale.domain() as [Date, Date];
  const long = b.getTime() - a.getTime() > 150 * DAY;
  // A short series (a young deployment's few collected weeks, a board first listed today) is
  // labelled at its own measured days: the scale's regular ticks would fall between them, and a
  // single day's padded scale (a day each side, for room) would name days nothing was measured on,
  // tomorrow among them. Labels that would collide are thinned below, as always.
  const own = xs ? [...new Set(xs)].sort((p, q) => p - q) : [];
  const raw = own.length > 0 && own.length <= FEW_POINTS ? own.map((x) => new Date(x)) : scale.ticks(Math.max(2, Math.floor(width / 92))).filter((t) => t.getUTCHours() === 0);
  let lastYear: string | null = null;
  const labelled = raw.map((t) => {
    const day = dateDay(t);
    if (!long) return { x: scale(t), label: plainDay(day, "dayMonth") };
    const [month, year] = plainDay(day, "monthYear").split(" ");
    const label = year !== lastYear ? `${month} ${year}` : month!;
    lastYear = year ?? null;
    return { x: scale(t), label };
  });
  return spaceTicks(labelled, width);
}

/** Thin labelled ticks so none overlap, then anchor the edge ones inward. */
export function spaceTicks(ticks: { x: number; label: string }[], width: number): Tick[] {
  const kept: Tick[] = [];
  let lastEnd = -Infinity;
  for (const t of ticks) {
    const w = textWidth(t.label);
    const anchor: Tick["anchor"] = t.x - w / 2 < 0 ? "start" : t.x + w / 2 > width ? "end" : "middle";
    const start = anchor === "start" ? t.x : anchor === "end" ? t.x - w : t.x - w / 2;
    if (start < lastEnd + 12) continue;
    kept.push({ ...t, anchor });
    lastEnd = start + w;
  }
  return kept;
}

/** A bar's outline with a 4px rounded data end and a square baseline (dataviz mark spec). */
export function barPath(x: number, y: number, w: number, h: number, end: "top" | "bottom"): string {
  const r = Math.max(0, Math.min(4, w / 2, h));
  if (h <= 0 || w <= 0) return "";
  if (end === "top") {
    return `M${x},${y + h}V${y + r}Q${x},${y} ${x + r},${y}H${x + w - r}Q${x + w},${y} ${x + w},${y + r}V${y + h}Z`;
  }
  return `M${x},${y}V${y + h - r}Q${x},${y + h} ${x + r},${y + h}H${x + w - r}Q${x + w},${y + h} ${x + w},${y + h - r}V${y}Z`;
}

// ---- layout ---------------------------------------------------------------------------------

/**
 * The width a chart has to draw in. It is read synchronously in a layout effect, so the first
 * paint is already at the real width (no frame at the fallback, no layout jump), then kept
 * current with a ResizeObserver. Where nothing is laid out (tests) it stays `fallback`.
 */
export function useChartWidth(fallback = 640) {
  const ref = useRef<HTMLDivElement>(null);
  const [width, setWidth] = useState(fallback);
  useLayoutEffect(() => {
    const el = ref.current;
    if (!el) return;
    const measure = (w: number | undefined) => {
      if (w && w > 0) setWidth(Math.round(w));
    };
    measure(el.getBoundingClientRect().width);
    if (typeof ResizeObserver === "undefined") return;
    const observer = new ResizeObserver((entries) => measure(entries[0]?.contentRect.width));
    observer.observe(el);
    return () => observer.disconnect();
  }, []);
  return { ref, width };
}

// ---- keyboard and pointer -------------------------------------------------------------------

/** What a pointer's leave carries: a finger's leave comes right after it lifts. */
export type PointerLeave = { pointerType?: string };

/**
 * While `held`, the next tap anywhere (a pointerdown, caught before the page's own handlers)
 * calls `release`: on touch a readout stays up after the finger lifts, until then.
 * A tap on the same chart is released here and then moves the readout in its own handler.
 */
export function useTouchHold(held: boolean, release: () => void) {
  useEffect(() => {
    if (!held) return;
    document.addEventListener("pointerdown", release, true);
    return () => document.removeEventListener("pointerdown", release, true);
  }, [held, release]);
}

/**
 * One tab stop per chart (roving tabindex): the arrow keys move between marks in reading order,
 * Home and End jump to the ends, Escape hides the tooltip and keeps focus where it is. Hover and
 * focus show the same tooltip; `onPointerAt` serves a plot-level pointer that finds the nearest
 * mark itself (line and area charts). A mouse that leaves hides the tooltip; a finger that lifts
 * leaves it up until the next tap elsewhere (pointerleave follows pointerup on touch).
 */
export function useMarks(count: number, initial = 0) {
  const [stop, setStop] = useState(initial);
  const [active, setActive] = useState<{ index: number; by: "pointer" | "focus" | "touch" } | null>(null);
  const tabStop = Math.max(0, Math.min(stop, count - 1));
  // The same mark again (every pointermove over one day) keeps the same state: no re-render.
  const point = (i: number) => setActive((a) => (a && a.index === i ? a : { index: i, by: "pointer" }));
  const release = useCallback(() => setActive((a) => (a?.by === "touch" ? null : a)), []);
  useTouchHold(active?.by === "touch", release);
  return {
    active: active && active.index < count ? active.index : null,
    focused: active?.by === "focus",
    tabIndex: (i: number) => (i === tabStop ? 0 : -1),
    onFocus: (i: number) => {
      setStop(i);
      setActive({ index: i, by: "focus" });
    },
    onBlur: () => setActive((a) => (a?.by === "focus" ? null : a)),
    onPointerEnter: point,
    onPointerAt: point,
    onPointerLeave: (event?: PointerLeave) => setActive((a) => (a?.by !== "pointer" ? a : event?.pointerType === "touch" ? { ...a, by: "touch" } : null)),
    onKeyDown: (event: KeyboardEvent<Element>) => {
      if (event.key === "Escape") {
        setActive(null);
        return;
      }
      // At an end the focus cannot move, so a key after Escape shows the tooltip again in place.
      const next = moveFocus(event);
      if (next !== null && document.activeElement === event.currentTarget) setActive({ index: next, by: "focus" });
    },
  };
}

export type Marks = ReturnType<typeof useMarks>;

const STEP: Record<string, number> = { ArrowRight: 1, ArrowDown: 1, ArrowLeft: -1, ArrowUp: -1 };

/** Move focus for a navigation key; returns the index it landed on, or null for any other key. */
function moveFocus(event: KeyboardEvent<Element>): number | null {
  const target = event.currentTarget as HTMLElement;
  const step = STEP[event.key];
  if (step === undefined && event.key !== "Home" && event.key !== "End") return null;
  const all = [...(target.closest("[data-chart-marks]")?.querySelectorAll<HTMLElement>("[data-mark]") ?? [])];
  const at = all.indexOf(target);
  const next = event.key === "Home" ? 0 : event.key === "End" ? all.length - 1 : Math.max(0, Math.min(all.length - 1, at + step!));
  event.preventDefault();
  all[next]?.focus();
  return next;
}

/** A plain left click on a chart link goes through the app's router; modified clicks stay the browser's. */
export function linkClick(href: string, onNavigate?: (href: string) => void) {
  return (event: { button: number; metaKey: boolean; ctrlKey: boolean; shiftKey: boolean; altKey: boolean; preventDefault: () => void }) => {
    if (!onNavigate || event.button !== 0 || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;
    event.preventDefault();
    onNavigate(href);
  };
}
