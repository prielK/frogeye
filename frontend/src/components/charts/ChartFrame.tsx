import { useEffect, useId, useRef, type ReactNode } from "react";
import { EmptyState } from "@/components/common/EmptyState";
import { cn } from "@/lib/utils";
import { linkClick, markerPath, nearestIndex, PAINT, SURFACE, type ChartColor, type ChartTableData, type MarkerShape, type Marks, type Surface, type Tick } from "./scales";
import { Swatch, type SwatchShape } from "./tooltip";

/*
 * The frame every chart wears: a <figure> named by its title, the printed one-line summary in
 * the caption above the plot (so-what first), the legend, the plot, a footnote and a "Show the
 * numbers" table twin, or, for empty data, a real empty state in place of the plot.
 */

export type EmptyCopy = { title: ReactNode; hint?: ReactNode };
export type LegendItem = {
  key: string;
  label: ReactNode;
  color: ChartColor;
  shape?: SwatchShape;
  /** A series key: the slot's marker shape on its line or tint ("none": the dashed "Other"). */
  marker?: MarkerShape | "none";
  dashed?: boolean;
  title?: string;
};
export type { ChartTableData };

/** The props every chart shares with its frame. `empty` is required: no chart ships without one. */
export type FrameProps = {
  /** What is measured: "Open roles". */
  title: ReactNode;
  /** The one-line takeaway, printed above the plot: "142 open now, up 12 in 30 days". */
  summary: ReactNode;
  /** Shown instead of the plot when there is nothing to draw. */
  empty: EmptyCopy;
  /** Controls that scope this chart (a 7/30/90 toggle), top right. */
  actions?: ReactNode;
  /** A footnote under the plot: method, sources, what is left out. */
  note?: ReactNode;
  /** What the chart sits on (the dots' surface ring). Default "background". */
  surface?: Surface;
  className?: string;
};

type ChartFrameProps = Omit<FrameProps, "empty"> & {
  /** When set, the empty state replaces the plot, the legend and the summary. */
  empty?: EmptyCopy | null;
  legend?: LegendItem[];
  table?: ChartTableData;
  children?: ReactNode;
};

export function ChartFrame({ title, summary, empty, legend, actions, note, table, surface = "background", className, children }: ChartFrameProps) {
  const titleId = useId();
  return (
    <figure aria-labelledby={titleId} data-surface={surface} className={cn("grid min-w-0 grid-cols-1 content-start gap-x-6 gap-y-3 sm:grid-cols-[minmax(0,1fr)_auto]", className)}>
      <figcaption className="flex min-w-0 flex-col gap-0.5 sm:col-start-1 sm:row-start-1">
        <span id={titleId} className="text-body font-semibold text-foreground">
          {title}
        </span>
        {empty ? null : <span className="text-body text-pretty text-muted-foreground">{summary}</span>}
      </figcaption>
      {actions ? <div className="flex flex-wrap items-start gap-2 sm:col-start-2 sm:row-start-1 sm:justify-end">{actions}</div> : null}
      <div className="flex min-w-0 flex-col gap-3 sm:col-span-2">
        {empty ? (
          <EmptyState title={empty.title} hint={empty.hint} className="max-w-none" />
        ) : (
          <>
            {legend && legend.length > 0 ? <ChartLegend items={legend} /> : null}
            {children}
            {note || table ? (
              <div className="flex flex-col gap-2">
                {note ? <p className="max-w-[72ch] text-meta text-pretty text-muted-foreground">{note}</p> : null}
                {table ? <ChartTable data={table} /> : null}
              </div>
            ) : null}
          </>
        )}
      </div>
    </figure>
  );
}

/** Identity is never colour alone: every series is named beside a key that mirrors its mark. */
export function ChartLegend({ items }: { items: LegendItem[] }) {
  return (
    <ul aria-label="Legend" className="flex flex-wrap items-center gap-x-4 gap-y-1">
      {items.map((item) => (
        <li key={item.key} title={item.title} className="flex min-w-0 items-center gap-1.5 text-meta text-muted-foreground">
          <Swatch color={item.color} shape={item.shape} marker={item.marker} dashed={item.dashed} />
          <span className="truncate">{item.label}</span>
        </li>
      ))}
    </ul>
  );
}

const NUMERIC = /^(?:[−+-]?[$€£]?[\d.,]+(?:[KMBT%×])?(?: \(sources disagree\))?|—)$/;

/** The chart's table twin: every value it draws, exactly, without hovering. Figures align right, words left. */
export function ChartTable({ data }: { data: ChartTableData }) {
  const numeric = data.columns.map((_, i) => i > 0 && data.rows.length > 0 && data.rows.every((row) => NUMERIC.test(row[i] ?? "—") || row[i] === "Not disclosed"));
  return (
    <details className="group/table">
      <summary className="inline-flex cursor-pointer list-none items-center gap-1.5 rounded-sm text-meta text-muted-foreground transition-colors duration-150 ease-out-quart select-none hover:text-foreground [&::-webkit-details-marker]:hidden">
        <span aria-hidden className="inline-block size-0 border-y-4 border-l-[6px] border-y-transparent border-l-current transition-transform duration-150 ease-out-quart group-open/table:rotate-90" />
        <span className="group-open/table:hidden">Show the numbers</span>
        <span className="hidden group-open/table:inline">Hide the numbers</span>
      </summary>
      <div className="mt-2 max-h-72 overflow-auto rounded-md border">
        <table className="w-full border-collapse text-meta">
          <thead className="sticky top-0 bg-card">
            <tr>
              {data.columns.map((column, i) => (
                <th key={column} scope="col" className={cn("border-b px-3 py-1.5 font-medium whitespace-nowrap text-muted-foreground", numeric[i] ? "text-right" : "text-left")}>
                  {column}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {data.rows.map((row, r) => (
              <tr key={r} className="border-b last:border-b-0">
                {row.map((cell, i) =>
                  i === 0 ? (
                    <th key={i} scope="row" className="px-3 py-1 text-left font-normal whitespace-nowrap text-foreground">
                      {cell}
                    </th>
                  ) : (
                    <td key={i} className={cn("px-3 py-1 text-foreground", numeric[i] ? "text-right whitespace-nowrap" : "text-left")}>
                      {cell}
                    </td>
                  ),
                )}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </details>
  );
}

// ---- SVG building blocks --------------------------------------------------------------------

/** Horizontal hairline gridlines with their value labels in the left gutter; the zero line is the baseline. */
export function ValueAxis({ ticks, y, width, format }: { ticks: number[]; y: (v: number) => number; width: number; format: (v: number) => string }) {
  return (
    <g aria-hidden>
      {ticks.map((t) => (
        <g key={t}>
          <line x1={0} x2={width} y1={y(t)} y2={y(t)} className={t === 0 ? "stroke-input" : "stroke-border"} shapeRendering="crispEdges" />
          <text x={-8} y={y(t)} dy="0.32em" textAnchor="end" className="fill-muted-foreground text-meta" data-tick="y">
            {format(t)}
          </text>
        </g>
      ))}
    </g>
  );
}

/** Labels along the bottom of the plot. */
export function TimeAxis({ ticks, top }: { ticks: Tick[]; top: number }) {
  return (
    <g aria-hidden transform={`translate(0, ${top})`}>
      {ticks.map((t) => (
        <text key={`${t.x}-${t.label}`} x={t.x} y={18} textAnchor={t.anchor} className="fill-muted-foreground text-meta" data-tick="x">
          {t.label}
        </text>
      ))}
    </g>
  );
}

type SvgMarkProps = {
  marks: Marks;
  index: number;
  /** The mark's full description for screen readers (the tooltip's text). */
  label: string;
  kind?: string;
  href?: string;
  onNavigate?: (href: string) => void;
  children: ReactNode;
};

/**
 * A focusable, hoverable data mark: a link when it leads somewhere (a plain click goes through
 * `onNavigate`), else an image. Only one mark per chart is in the tab order.
 */
export function SvgMark({ marks, index, label, kind, href, onNavigate, children }: SvgMarkProps) {
  const common = {
    "data-mark": "",
    "data-kind": kind,
    tabIndex: marks.tabIndex(index),
    "aria-label": label,
    className: "cursor-default outline-none",
    onFocus: () => marks.onFocus(index),
    onBlur: marks.onBlur,
    onPointerEnter: () => marks.onPointerEnter(index),
    onPointerLeave: marks.onPointerLeave,
    onKeyDown: marks.onKeyDown,
  };
  if (href) {
    return (
      <a {...common} role="link" href={href} className="cursor-pointer outline-none" onClick={linkClick(href, onNavigate)}>
        {children}
      </a>
    );
  }
  return (
    <g {...common} role="img">
      {children}
    </g>
  );
}

/** A series' marker: its slot's shape, in its colour, ringed in the surface so it reads over lines. */
export function SeriesMarker({ shape, x, y, r = 4, color, surface }: { shape: MarkerShape; x: number; y: number; r?: number; color: ChartColor; surface: Surface }) {
  return <path data-marker data-shape={shape} d={markerPath(shape, r)} transform={`translate(${x} ${y})`} className={cn(PAINT[color].fill, SURFACE[surface].ring)} strokeWidth={1.5} aria-hidden />;
}

/** The class a pointer plot's wrapping element wears: a finger scrubs sideways and the page still scrolls (iOS Safari reads touch-action on HTML elements, not on an SVG child). */
export const PLOT_TOUCH = "touch-pan-y";

/**
 * The pointer layer of a line, area or column chart: the whole plot is the hit target and the
 * nearest position wins (no thin per-day strips). Its chart's wrapping element wears `PLOT_TOUCH`
 * (`touch-action: pan-y`), so a finger scrubs sideways while the page still scrolls vertically.
 */
export function PlotHit({ width, height, positions, marks }: { width: number; height: number; positions: number[]; marks: Marks }) {
  // The plot's left edge is read once per hover (enter, or a touch's down), never per move, so a
  // move costs no forced layout; `useMarks` keeps its state while the nearest day is unchanged.
  // A scroll or a resize can move the plot under a still pointer, so either clears the reading
  // and the next move takes it again.
  const left = useRef<number | null>(null);
  useEffect(() => {
    const stale = () => (left.current = null);
    window.addEventListener("scroll", stale, { capture: true, passive: true });
    window.addEventListener("resize", stale, { passive: true });
    return () => {
      window.removeEventListener("scroll", stale, { capture: true });
      window.removeEventListener("resize", stale);
    };
  }, []);
  const measure = (target: Element) => (left.current = target.getBoundingClientRect().left);
  const at = (event: { clientX: number; currentTarget: Element }) => {
    const x0 = left.current ?? measure(event.currentTarget);
    marks.onPointerAt(nearestIndex(positions, event.clientX - x0));
  };
  return (
    <rect
      data-hit
      x={0}
      y={0}
      width={width}
      height={height}
      className="fill-transparent"
      onPointerEnter={(event) => measure(event.currentTarget)}
      onPointerDown={(event) => {
        measure(event.currentTarget);
        at(event);
      }}
      onPointerMove={at}
      onPointerLeave={(event) => {
        left.current = null;
        marks.onPointerLeave(event);
      }}
    />
  );
}
