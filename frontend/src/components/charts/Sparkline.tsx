import { scaleLinear } from "d3-scale";
import { line as d3line } from "d3-shape";
import { useCallback, useRef, useState, type KeyboardEvent, type PointerEvent } from "react";
import { cn } from "@/lib/utils";
import { formatDate, formatValue, PAINT, SURFACE, timeScale, useTouchHold, type ChartColor, type Surface, type TimePoint } from "./scales";
import { ChartTooltip } from "./tooltip";

/*
 * A trend in a table cell (13 weeks of momentum): a recessive grey line ending in a dot in the
 * accent slot, zero-based. It is one image with one tab stop; hover finds the nearest point,
 * the arrow keys walk the points and the tooltip says the exact value. It never stands alone:
 * the cell prints the current value beside it. No data is said in words, never a flat line, and
 * the words are named by `label`: null is "Insufficient data" (not enough evidence to measure),
 * [] is "No data yet" (measured, nothing recorded).
 */

/** How soon after a press a focus is the press's own (a tap's focus follows its pointerleave). */
const PRESS_FOCUS_MS = 1000;

export type SparklineProps = {
  /** What the line shows, for its accessible name: "Signals per week, last 13 weeks". */
  label: string;
  values: TimePoint[] | null;
  format?: (n: number) => string;
  xFormat?: (d: Date) => string;
  /** Said instead of a line when `values` is null: not enough evidence. Default "Insufficient data". */
  emptyLabel?: string;
  /** Said when `values` is []: measured, nothing recorded yet. Default "No data yet". */
  noDataLabel?: string;
  width?: number;
  height?: number;
  color?: ChartColor;
  surface?: Surface;
  className?: string;
};

export function Sparkline({
  label,
  values,
  format = formatValue,
  xFormat = formatDate,
  emptyLabel = "Insufficient data",
  noDataLabel = "No data yet",
  width = 88,
  height = 24,
  color = "chart-1",
  surface = "background",
  className,
}: SparklineProps) {
  const [active, setActive] = useState<number | null>(null);
  // A finger's readout stays up after it lifts, until the next tap elsewhere.
  const [held, setHeld] = useState(false);
  const release = useCallback(() => {
    setHeld(false);
    setActive(null);
  }, []);
  useTouchHold(held, release);
  // on a touch screen focus lands after pointerdown and pointerleave; a focus that close after a
  // press keeps the point pressed, where a keyboard focus starts at the last week.
  const pressedAt = useRef<number | null>(null);
  if (!values || values.length === 0) {
    const text = values === null ? emptyLabel : noDataLabel;
    return (
      <span role="img" aria-label={`${label}: ${text}`} className={cn("text-meta whitespace-nowrap text-muted-foreground", className)}>
        {text}
      </span>
    );
  }
  const points = [...values].sort((a, b) => a.x.getTime() - b.x.getTime());
  const pad = 4;
  const x = timeScale(
    points.map((p) => p.x.getTime()),
    width - pad * 2,
  );
  const top = Math.max(...points.map((p) => p.y));
  const y = scaleLinear()
    .domain([0, top > 0 ? top : 1])
    .range([height - pad, pad]);
  const path = d3line<TimePoint>()
    .x((p) => x(p.x) + pad)
    .y((p) => y(p.y))(points);
  const last = points.at(-1)!;
  const peak = points.reduce((best, p) => (p.y > best.y ? p : best), points[0]!);
  const name = `${label}: ${format(points[0]!.y)} to ${format(last.y)}, peak ${format(peak.y)} (${xFormat(peak.x)})`;
  const shown = active === null ? null : points[active]!;

  const nearest = (event: PointerEvent<SVGSVGElement>) => {
    const box = event.currentTarget.getBoundingClientRect();
    const at = event.clientX - box.left - pad;
    let best = 0;
    points.forEach((p, i) => {
      if (Math.abs(x(p.x) - at) < Math.abs(x(points[best]!.x) - at)) best = i;
    });
    setActive(best);
  };
  const keys = (event: KeyboardEvent<SVGSVGElement>) => {
    const step = event.key === "ArrowRight" ? 1 : event.key === "ArrowLeft" ? -1 : 0;
    if (event.key === "Home" || event.key === "End") {
      event.preventDefault();
      setActive(event.key === "Home" ? 0 : points.length - 1);
    } else if (step) {
      event.preventDefault();
      setActive((i) => Math.max(0, Math.min(points.length - 1, (i ?? points.length - 1) + step)));
    } else if (event.key === "Escape") {
      setActive(null);
    }
  };

  return (
    <span className={cn("relative inline-flex align-middle", className)}>
      <svg
        role="img"
        aria-label={name}
        tabIndex={0}
        width={width}
        height={height}
        className="block overflow-visible rounded-sm outline-none [&_*]:transition-none focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-ring"
        onFocus={(event) => {
          // The press's own focus keeps its point, once; any later focus starts at the last week.
          const pressed = pressedAt.current !== null && event.timeStamp - pressedAt.current < PRESS_FOCUS_MS;
          pressedAt.current = null;
          if (!pressed) setActive(points.length - 1);
        }}
        onBlur={() => setActive(null)}
        onPointerMove={nearest}
        onPointerDown={(event) => {
          pressedAt.current = event.timeStamp;
          nearest(event);
        }}
        onPointerLeave={(event) => (event.pointerType === "touch" ? setHeld(true) : setActive(null))}
        onKeyDown={keys}
      >
        <path d={path ?? ""} data-line className={cn("fill-none", PAINT["chart-5"].stroke)} strokeWidth={1.5} strokeLinejoin="round" strokeLinecap="round" />
        {shown ? <line x1={x(shown.x) + pad} x2={x(shown.x) + pad} y1={0} y2={height} className="stroke-input" strokeWidth={1} /> : null}
        <circle cx={x((shown ?? last).x) + pad} cy={y((shown ?? last).y)} r={3} className={cn(PAINT[color].fill, SURFACE[surface].ring)} strokeWidth={1.5} />
      </svg>
      {shown ? <ChartTooltip x={x(shown.x) + pad} y={0} bottom={height} containerWidth={width} heading={xFormat(shown.x)} rows={[{ key: "v", value: format(shown.y), color, shape: "dot" }]} /> : null}
    </span>
  );
}
