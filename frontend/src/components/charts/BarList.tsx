import { useMemo, type ReactNode } from "react";
import { Num } from "@/components/common/Num";
import { formatPercent } from "@/lib/format";
import { cn } from "@/lib/utils";
import { ChartFrame, type FrameProps } from "./ChartFrame";
import { formatDelta, formatValue, linkClick, OTHER_COLOR, PAINT, useChartWidth, useMarks, type ChartColor, type Marks } from "./scales";
import { ChartTooltip } from "./tooltip";

/*
 * Magnitude across categories (departments, tags, locations): one row per category, largest
 * first, the exact value printed at the bar's tip and an optional signed change beside it whose
 * direction is a drawn arrow plus its sign, never colour. Past `max` rows the tail folds into one
 * "Other (n)" row, so 13 departments or 37 tags stay legible at 390px.
 */

export type BarItem = {
  key: string;
  label: string;
  value: number;
  /** A signed change over `deltaLabel`'s window; null or absent when not measured. */
  delta?: number | null;
  href?: string;
  /** The bar the page is filtered to: a held wash, its label in medium, `aria-current` on its link. */
  current?: boolean;
  /** A row that means something (the Briefing's stance rows: threat, opportunity, neutral) wears its meaning token; its label still says it in words. */
  color?: ChartColor;
  /** A quiet word printed after the value, in its tooltip and in the table: the cases a rate counts ("9/22"). */
  aside?: string;
};

export type BarListProps = FrameProps & {
  items: BarItem[];
  /** Rows before the rest fold into "Other" (default 8). */
  max?: number;
  valueFormat?: (n: number) => string;
  /** The change's window, for the tooltip: "in 30 days". */
  deltaLabel?: string;
  /** What a value counts, for the tooltip: "open roles". */
  unit?: string;
  /** One series: every bar wears the same slot (default chart-1). */
  color?: ChartColor;
  /**
   * The value a full bar stands for, when the values have a fixed scale (a rate's 1): bars are
   * drawn against it instead of the largest named row, and a value's tooltip leaves out its share
   * of all shown, which a rate does not have.
   */
  extent?: number;
  onNavigate?: (href: string) => void;
};

type Row = BarItem & { other?: string[] };

export function BarList({ items, max = 8, valueFormat = formatValue, deltaLabel, unit, color = "chart-1", extent, onNavigate, surface = "background", ...frame }: BarListProps) {
  const rows = useMemo(() => foldBars(items, max), [items, max]);
  const isEmpty = rows.length === 0;
  const hasDelta = items.some((i) => i.delta !== undefined && i.delta !== null);
  const table = {
    columns: ["Category", unit ? capitalise(unit) : "Value", ...(hasDelta ? [deltaLabel ? `Change ${deltaLabel}` : "Change"] : [])],
    rows: [...items].sort((a, b) => b.value - a.value).map((i) => [i.label, i.aside ? `${valueFormat(i.value)} · ${i.aside}` : valueFormat(i.value), ...(hasDelta ? [i.delta === undefined || i.delta === null ? "—" : formatDelta(i.delta, valueFormat)] : [])]),
  };
  return (
    <ChartFrame {...frame} surface={surface} empty={isEmpty ? frame.empty : null} table={isEmpty ? undefined : table}>
      {isEmpty ? null : <Bars rows={rows} valueFormat={valueFormat} deltaLabel={deltaLabel} unit={unit} color={color} hasDelta={hasDelta} extent={extent} onNavigate={onNavigate} />}
    </ChartFrame>
  );
}

const capitalise = (s: string) => s.charAt(0).toUpperCase() + s.slice(1);

/** Largest first; past `max` rows the rest sum into "Other (n)" (folding a single row would only hide its name). */
function foldBars(items: BarItem[], max: number): Row[] {
  const sorted = [...items].sort((a, b) => b.value - a.value);
  if (sorted.length <= max + 1) return sorted;
  const rest = sorted.slice(max);
  const deltas = rest.map((r) => r.delta);
  const delta = deltas.every((d) => d !== undefined && d !== null) ? deltas.reduce<number>((s, d) => s + d!, 0) : null;
  return [
    ...sorted.slice(0, max),
    { key: "__other", label: `Other (${rest.length})`, value: rest.reduce((s, r) => s + r.value, 0), delta, other: rest.map((r) => r.label) },
  ];
}

type BarsProps = {
  rows: Row[];
  valueFormat: (n: number) => string;
  deltaLabel?: string;
  unit?: string;
  color: ChartColor;
  hasDelta: boolean;
  extent?: number;
  onNavigate?: (href: string) => void;
};

/**
 * The break in an "Other" bar cut short: two slanted gaps near its end, cut out of the bar
 * with a mask, so whatever lies behind shows through them, a row's hover wash included (painted
 * strokes in the surface colour read as pale slashes on it).
 */
const BREAK =
  "[mask-image:linear-gradient(114deg,#000_calc(100%_-_20px),transparent_calc(100%_-_20px)_calc(100%_-_17px),#000_calc(100%_-_17px)_calc(100%_-_15px),transparent_calc(100%_-_15px)_calc(100%_-_12px),#000_calc(100%_-_12px))]";

function Bars({ rows, valueFormat, deltaLabel, unit, color, hasDelta, extent, onNavigate }: BarsProps) {
  const { ref, width } = useChartWidth();
  const marks = useMarks(rows.length, 0);
  // Bars scale to the largest NAMED row, so a big "Other" never shrinks the rows the reader came
  // for to slivers; an "Other" bar larger than that is cut short with a break mark.
  // A fixed scale (`extent`, a rate's 1) draws every bar against it instead.
  const top = extent ?? Math.max(0, ...rows.filter((r) => !r.other).map((r) => r.value));
  const sum = rows.reduce((s, r) => s + r.value, 0);
  // Room after the longest bar for its value and change, so a label never sits on a bar.
  const reserve = hasDelta || rows.some((r) => r.aside) ? "7rem" : "4.5rem";
  return (
    // The label column grows with the chart (a container query), so a wide chart never truncates "Software composition analysis".
    <div ref={ref} className="@container relative w-full" data-plot>
      <ul data-chart-marks className="flex flex-col">
        {rows.map((row, i) => {
          const clipped = Boolean(row.other) && row.value > top;
          const share = top > 0 ? Math.min(1, row.value / top) : 0;
          const detail = describe(row, extent === undefined ? sum : null, valueFormat, unit, deltaLabel);
          const note = [row.other ? `Includes ${listNames(row.other)}.` : null, clipped ? "Its bar is cut short: it is larger than every named row." : null].filter(Boolean).join(" ");
          return (
            <li
              key={row.key}
              data-current={row.current ? "" : undefined}
              className={cn(
                "relative grid grid-cols-[minmax(0,9rem)_minmax(0,1fr)] items-center gap-x-3 rounded-sm py-1 transition-colors duration-150 ease-out-quart hover:bg-accent/60 @lg:grid-cols-[minmax(0,11rem)_minmax(0,1fr)] @2xl:grid-cols-[minmax(0,15rem)_minmax(0,1fr)]",
                row.current && "bg-accent hover:bg-accent",
              )}
              onPointerEnter={() => marks.onPointerEnter(i)}
              onPointerLeave={marks.onPointerLeave}
            >
              <span title={row.other ? row.other.join(", ") : row.label} className={cn("truncate pl-1 text-body", row.other ? "text-muted-foreground" : "text-foreground", row.current && "font-medium")}>
                {row.label}
              </span>
              <BarMark marks={marks} index={i} label={`${row.label}: ${detail.text}`} href={row.href} current={row.current} onNavigate={onNavigate}>
                <span
                  aria-hidden
                  data-bar-fill
                  data-share={share}
                  data-break={clipped ? "" : undefined}
                  className={cn("relative h-2 shrink-0 rounded-r-[4px]", PAINT[row.other ? OTHER_COLOR : (row.color ?? color)].bg, clipped && BREAK)}
                  style={{ width: `calc((100% - ${reserve}) * ${share})`, minWidth: row.value > 0 ? 2 : 0 }}
                />
                <Num value={row.value} format={valueFormat} className="text-meta font-medium text-foreground" />
                {row.aside ? <span className="text-meta whitespace-nowrap text-muted-foreground">{row.aside}</span> : null}
                {row.delta !== undefined && row.delta !== null ? <Delta value={row.delta} format={valueFormat} /> : null}
              </BarMark>
              {marks.active === i ? (
                <ChartTooltip x="62%" y={0} bottom="100%" containerWidth={width} heading={row.label} rows={detail.rows} note={note || undefined} placement={i === 0 ? "below" : "above"} />
              ) : null}
            </li>
          );
        })}
      </ul>
    </div>
  );
}

function BarMark({
  marks,
  index,
  label,
  href,
  current,
  onNavigate,
  children,
}: {
  marks: Marks;
  index: number;
  label: string;
  href?: string;
  current?: boolean;
  onNavigate?: (href: string) => void;
  children: ReactNode;
}) {
  const common = {
    "data-mark": "",
    tabIndex: marks.tabIndex(index),
    "aria-label": label,
    className: "flex min-w-0 items-center gap-2 rounded-sm py-1",
    onFocus: () => marks.onFocus(index),
    onBlur: marks.onBlur,
    onKeyDown: marks.onKeyDown,
  };
  return href ? (
    <a {...common} href={href} aria-current={current ? "true" : undefined} onClick={linkClick(href, onNavigate)}>
      {children}
    </a>
  ) : (
    <div {...common} role="img">
      {children}
    </div>
  );
}

/** "+3" with a drawn up arrow, "−2" with a down arrow; no change is a plain "0". */
function Delta({ value, format }: { value: number; format: (n: number) => string }) {
  const direction = value > 0 ? "up" : value < 0 ? "down" : null;
  return (
    <span data-direction={direction ?? undefined} className="inline-flex items-center gap-0.5 text-meta whitespace-nowrap text-muted-foreground">
      {direction ? (
        <svg aria-hidden viewBox="0 0 8 8" className="size-2">
          <polygon points={direction === "up" ? "4,0.5 7.5,7 0.5,7" : "0.5,1 7.5,1 4,7.5"} className="fill-current" />
        </svg>
      ) : null}
      {formatDelta(value, format)}
    </span>
  );
}

/** A row's tooltip; `sum` null leaves out the share of all shown (a fixed scale's values are not parts of a whole). */
function describe(row: Row, sum: number | null, format: (n: number) => string, unit?: string, deltaLabel?: string) {
  const share = sum !== null && sum > 0 ? formatPercent(row.value / sum) : "—";
  const rows = [
    { key: "value", value: format(row.value), label: [unit, row.aside ? `(${row.aside})` : null].filter(Boolean).join(" ") || undefined },
    ...(sum === null ? [] : [{ key: "share", value: share, label: "of all shown" }]),
    ...(row.delta !== undefined && row.delta !== null ? [{ key: "delta", value: formatDelta(row.delta, format), label: deltaLabel ?? "change" }] : []),
  ];
  const text = rows.map((r) => [r.value, r.label].filter(Boolean).join(" ")).join(", ");
  return { rows, text };
}

function listNames(names: string[]) {
  return names.length <= 6 ? names.join(", ") : `${names.slice(0, 5).join(", ")} and ${names.length - 5} more`;
}
