import type { ReactNode } from "react";
import { cn } from "@/lib/utils";
import { markerPath, PAINT, type ChartColor, type MarkerShape } from "./scales";

/*
 * The chart tooltip: exact values, one hover or focus away (the table twin and the printed
 * summary carry them too, so it never gates a value). A floating popover surface, not the app's
 * inverted text tooltip, because its keys wear the chart colours, which are validated on the
 * card and page surfaces. Values lead, labels follow; series are keyed exactly like the legend.
 */

/**
 * A key's form: a line for lines, a square for areas and bars, a dot for points, a ring for
 * "sources disagree", a dashed ring for a value that is not known, a triangle for an event, a
 * diamond for a diamond group.
 */
export type SwatchShape = "line" | "rect" | "dot" | "ring" | "dashed-ring" | "triangle" | "diamond";

type SwatchProps = {
  color: ChartColor;
  shape?: SwatchShape;
  /** A series key: the slot's marker on a line ("line") or a tinted square ("rect"); "none" is the dashed "Other". */
  marker?: MarkerShape | "none";
  dashed?: boolean;
  className?: string;
};

/** A key that mirrors its mark, so a legend or tooltip row never rests on colour alone. */
export function Swatch({ color, shape = "rect", marker, dashed, className }: SwatchProps) {
  const paint = PAINT[color];
  if (marker !== undefined) {
    // A series key, 18 x 12: the line (dashed for "Other") or the layer's tint, and the marker on it.
    return (
      <svg aria-hidden viewBox="0 0 18 12" data-shape={marker} className={cn("h-3 w-[18px] shrink-0 overflow-visible", className)}>
        {shape === "rect" ? <rect x={1} y={1} width={16} height={10} rx={2} className={paint.tint} /> : null}
        <line x1={1} x2={17} y1={6} y2={6} className={paint.stroke} strokeWidth={2} strokeLinecap="round" strokeDasharray={dashed ? "3 2.5" : undefined} />
        {marker !== "none" ? <path d={markerPath(marker, 3.4)} transform="translate(9 6)" className={paint.fill} /> : null}
      </svg>
    );
  }
  if (shape === "triangle") {
    return (
      <svg aria-hidden viewBox="0 0 10 10" className={cn("size-2.5 shrink-0", className)}>
        <polygon points="0,1 10,1 5,9" className={paint.fill} />
      </svg>
    );
  }
  return (
    <span
      aria-hidden
      className={cn(
        "inline-block shrink-0",
        shape === "line" && "h-0.5 w-3 rounded-full",
        shape === "rect" && "size-2.5 rounded-[2px]",
        shape === "diamond" && "size-2 rotate-45 rounded-[1px]",
        shape === "dot" && "size-2.5 rounded-full",
        (shape === "ring" || shape === "dashed-ring") && "size-2.5 rounded-full border-2 bg-transparent",
        shape === "dashed-ring" && "border-dashed",
        shape === "ring" || shape === "dashed-ring" ? paint.border : paint.bg,
        className,
      )}
    />
  );
}

export type TooltipRow = {
  key: string;
  value: ReactNode;
  label?: ReactNode;
  color?: ChartColor;
  shape?: SwatchShape;
  marker?: MarkerShape | "none";
  dashed?: boolean;
  /** A total or subtotal: no key, set apart by a hairline. */
  total?: boolean;
};

type ChartTooltipProps = {
  /** Anchor in px from the plot container's left (or a CSS length for HTML marks). */
  x: number | string;
  /** The mark's top edge: the tooltip sits above it. */
  y: number;
  /**
   * The mark's bottom edge. Near the top of the plot the tooltip flips below the mark and hangs
   * from here, so it never covers the mark or its label. Defaults to `y`.
   */
  bottom?: number | string;
  /**
   * "auto" (default) sits above the mark and flips below it near the top of the plot; "side"
   * sits beside a crosshair at `y` (the plot's top), on whichever side has more room and never
   * wider than that room (its rows wrap), so it never covers the column of points and never runs
   * past the chart; HTML rows choose above or below.
   */
  placement?: "auto" | "above" | "below" | "side";
  /** The container's width, to keep the tooltip inside it. */
  containerWidth: number;
  heading?: ReactNode;
  rows?: TooltipRow[];
  note?: ReactNode;
};

export function ChartTooltip({ x, y, bottom, placement = "auto", containerWidth, heading, rows = [], note }: ChartTooltipProps) {
  const keyed = rows.some((row) => row.color);
  const at = typeof x === "number" ? x : null;
  const side = at === null ? "center" : at < 112 ? "start" : at > containerWidth - 112 ? "end" : "center";
  const below = placement === "below" || (placement === "auto" && y < 76);
  const beside = placement === "side" && at !== null;
  const roomRight = at === null ? 0 : containerWidth - at;
  const toRight = at !== null && roomRight >= at;
  // 12px off the crosshair and 4px short of the chart's edge.
  const room = beside ? Math.min(256, (toRight ? roomRight : at!) - 16) : undefined;
  const tx = beside ? (toRight ? "12px" : "calc(-100% - 12px)") : side === "start" ? "-12px" : side === "end" ? "calc(-100% + 12px)" : "-50%";
  const ty = beside ? "0px" : below ? "10px" : "calc(-100% - 12px)";
  return (
    <div
      role="tooltip"
      className={cn(
        "pointer-events-none absolute z-(--z-tooltip) w-max max-w-64 rounded-md border bg-popover px-2.5 py-2 text-meta text-popover-foreground shadow-floating",
        "animate-in duration-100 ease-out-quart fade-in-0",
      )}
      style={{ left: x, top: !beside && below ? (bottom ?? y) : y, transform: `translate(${tx}, ${ty})`, maxWidth: room }}
    >
      {heading ? <p className="font-medium text-popover-foreground">{heading}</p> : null}
      {rows.length > 0 ? (
        <ul className={cn("flex flex-col gap-0.5", heading ? "mt-1" : null)}>
          {rows.map((row) => (
            <li key={row.key} className={cn("flex items-center gap-2", row.total && "mt-0.5 border-t pt-1")}>
              {row.color ? (
                <Swatch color={row.color} shape={row.shape} marker={row.marker} dashed={row.dashed} />
              ) : keyed && !row.total ? (
                <span aria-hidden className="w-3 shrink-0" />
              ) : null}
              <span className={cn("font-semibold text-popover-foreground", typeof row.value === "string" && row.value.length > 24 ? "min-w-0" : "whitespace-nowrap")}>{row.value}</span>
              {row.label ? <span className="min-w-0 text-muted-foreground">{row.label}</span> : null}
            </li>
          ))}
        </ul>
      ) : null}
      {note ? <p className={cn("text-muted-foreground", heading || rows.length > 0 ? "mt-1" : null)}>{note}</p> : null}
    </div>
  );
}
