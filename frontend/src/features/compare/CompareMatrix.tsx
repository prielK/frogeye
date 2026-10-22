import { useCallback, useLayoutEffect, useRef, useState } from "react";
import { Link } from "react-router";
import { INLINE_LINK } from "@/components/common/links";
import { OVERLAP_CHIP } from "@/features/intel/levels";
import { ThreatChip, ThreatMark } from "@/features/intel/ThreatChip";
import { dayMonth } from "@/features/intel/words";
import { RELATIONSHIP } from "@/features/competitors/useCompetitorIntel";
import { PressureChip } from "@/features/products/PressureChip";
import { useRules } from "@/lib/intelReference";
import { cn } from "@/lib/utils";
import { cellKey, cellWord, productsLine, type CompareCell, type CompareCompetitor, type CompareProduct } from "./useCompare";

/*
 * The matrix (from `md`): a JFrog product per row, a competitor per column, so the whole market
 * reads on one screen. The row heads (the product and its pressure) and the column heads (the
 * competitor, its tier and its overall threat) stay in view while the matrix scrolls inside its
 * frame, and the pointer's row and column light up together, so a cell is always read against
 * both of its heads. A cell is ONE button that opens the pairing's sheet; a blank cell (no product
 * of theirs mapped) is not a control.
 */

type Hover = { row: string; col: string } | null;

/** A cell's threat: the product page's chip, or its quiet words when no threat is named. */
export function CellThreat({ cell }: { cell: CompareCell }) {
  if (cell.threat_level === "none") {
    return (
      <span data-overlap="none" className={cn("text-meta", cell.evidence > 0 ? "text-foreground" : "text-muted-foreground")}>
        {cellWord(cell)}
      </span>
    );
  }
  return (
    <ThreatChip level={OVERLAP_CHIP[cell.threat_level]} data-overlap={cell.threat_level}>
      {cellWord(cell)}
    </ThreatChip>
  );
}

function Cell({ cell, product, competitor, hover, onHover, onOpen }: { cell: CompareCell | undefined; product: CompareProduct; competitor: CompareCompetitor; hover: Hover; onHover: (h: Hover) => void; onOpen: (key: string) => void }) {
  const lit = hover !== null && (hover.row === product.slug || hover.col === competitor.slug);
  const base = cn("border-b border-l p-0 align-top transition-colors duration-150 ease-out-quart", lit && "bg-accent/45");
  if (!cell) {
    return (
      <td className={base} onPointerEnter={() => onHover({ row: product.slug, col: competitor.slug })}>
        <span aria-hidden className="block px-3 py-3 text-meta text-muted-foreground select-none">
          —
        </span>
        <span className="sr-only">No product of theirs mapped</span>
      </td>
    );
  }
  const move = cell.last_move;
  return (
    <td className={base}>
      <button
        type="button"
        data-cell={cellKey(product.slug, competitor.slug)}
        aria-label={`${product.name} vs ${competitor.name}: ${cellWord(cell)}${cell.threat_level === "none" ? "" : " threat"}. ${productsLine(cell)}.${move ? ` Latest move ${dayMonth(move.day)}.` : ""}`}
        onClick={() => onOpen(cellKey(product.slug, competitor.slug))}
        onPointerEnter={() => onHover({ row: product.slug, col: competitor.slug })}
        onFocus={() => onHover({ row: product.slug, col: competitor.slug })}
        className="flex h-full min-h-[4.25rem] w-full cursor-pointer flex-col items-start gap-1.5 px-2.5 py-3 text-left transition-colors duration-150 ease-out-quart hover:bg-accent focus-visible:bg-accent focus-visible:ring-2 focus-visible:ring-ring focus-visible:outline-none focus-visible:ring-inset"
      >
        <span className="flex w-full flex-wrap items-center justify-between gap-x-1.5 gap-y-1">
          <CellThreat cell={cell} />
          {move ? (
            <time dateTime={move.day} className="text-meta whitespace-nowrap text-muted-foreground">
              {dayMonth(move.day)}
            </time>
          ) : null}
        </span>
        <span className="line-clamp-2 text-meta text-pretty text-muted-foreground">{productsLine(cell)}</span>
      </button>
    </td>
  );
}

type MatrixProps = {
  products: CompareProduct[];
  competitors: CompareCompetitor[];
  cells: Map<string, CompareCell>;
  young: boolean;
  onOpen: (key: string) => void;
  labelledBy: string;
};

/** The frame's far edges fade while there is more to scroll to that way, so the columns and rows out of view are never a secret. */
function useEdges() {
  const ref = useRef<HTMLDivElement>(null);
  const [more, setMore] = useState({ x: false, y: false });
  const measure = useCallback(() => {
    const el = ref.current;
    if (!el) return;
    const x = el.scrollLeft + el.clientWidth < el.scrollWidth - 1;
    const y = el.scrollTop + el.clientHeight < el.scrollHeight - 1;
    setMore((m) => (m.x === x && m.y === y ? m : { x, y }));
  }, []);
  useLayoutEffect(() => {
    measure();
    const el = ref.current;
    if (!el || typeof ResizeObserver === "undefined") return;
    const observer = new ResizeObserver(measure);
    observer.observe(el);
    return () => observer.disconnect();
  }, [measure]);
  return { ref, more, measure };
}

const FADE = "2.5rem";

export function CompareMatrix({ products, competitors, cells, young, onOpen, labelledBy }: MatrixProps) {
  const [hover, setHover] = useState<Hover>(null);
  const since = useRules().collected_since;
  const { ref, more, measure } = useEdges();
  const masks = [
    more.x ? `linear-gradient(to right, #000 calc(100% - ${FADE}), transparent)` : null,
    more.y ? `linear-gradient(to bottom, #000 calc(100% - ${FADE}), transparent)` : null,
  ].filter(Boolean);
  return (
    // The frame scrolls both ways, so the heads can stay in view; it never widens the page.
    <div
      ref={ref}
      onScroll={measure}
      data-more-x={more.x ? "" : undefined}
      data-more-y={more.y ? "" : undefined}
      style={masks.length ? { maskImage: masks.join(", "), maskComposite: "intersect", WebkitMaskComposite: "source-in" } : undefined}
      className="relative isolate max-h-[max(30rem,calc(100dvh-10rem))] overflow-auto overscroll-x-contain"
      onPointerLeave={() => setHover(null)}
    >
      <table aria-labelledby={labelledBy} className="w-max min-w-full border-separate border-spacing-0 text-body">
        <thead>
          <tr>
            <th scope="col" className="sticky top-0 left-0 z-[2] w-[13.5rem] min-w-[13.5rem] border-b border-foreground bg-background px-3 pt-1 pb-3 text-left align-bottom text-meta font-medium text-muted-foreground first:pl-0">
              JFrog product
            </th>
            {competitors.map((c) => (
              <th
                key={c.slug}
                scope="col"
                className={cn(
                  "sticky top-0 z-[1] w-[9rem] min-w-[9rem] border-b border-l border-b-foreground bg-background px-3 pt-1 pb-3 text-left align-bottom font-normal transition-colors duration-150 ease-out-quart",
                  hover?.col === c.slug && "bg-accent",
                )}
              >
                <span className="flex flex-col items-start gap-1">
                  <Link to={`/competitors/${c.slug}`} className={cn(INLINE_LINK, "text-body font-semibold text-foreground")}>
                    {c.name}
                  </Link>
                  <span className="text-meta text-muted-foreground">{`Tier ${c.tier} · ${RELATIONSHIP[c.relationship] ?? c.relationship}`}</span>
                  <ThreatMark threat={c.threat} />
                </span>
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {products.map((p) => (
            <tr key={p.slug}>
              <th scope="row" className={cn("sticky left-0 z-[1] border-b bg-background py-3 pr-3 text-left align-top font-normal transition-colors duration-150 ease-out-quart", hover?.row === p.slug && "bg-accent")}>
                <span className="flex flex-col items-start gap-1.5">
                  <Link to={`/products/${p.slug}`} className={cn(INLINE_LINK, "text-body font-semibold text-foreground")}>
                    {p.name}
                  </Link>
                  <PressureChip product={p} collectedSince={since} young={young} />
                </span>
              </th>
              {competitors.map((c) => (
                <Cell key={c.slug} cell={cells.get(cellKey(p.slug, c.slug))} product={p} competitor={c} hover={hover} onHover={setHover} onOpen={onOpen} />
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
