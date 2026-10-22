import { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { sourceOutlet, type CitedSource } from "@/components/signal/query";
import { SourceLink, SourceMeta } from "@/components/signal/SourceLink";
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";

/*
 * Citations: every factual item carries `[n]` markers that point into the page's numbered
 * Sources list. A marker is a real link (keyboard-focusable, named "Source 2: DevOps.com, …",
 * the outlet and title one hover away); following it scrolls the source into view, moves
 * focus there so Tab continues to its link, and washes it in `accent` for a moment.
 */

const HIGHLIGHT_MS = 2400;

type Citations = {
  byNumber: Map<number, CitedSource>;
  highlighted: number | null;
  jump: (n: number) => void;
};

const CitationContext = createContext<Citations | null>(null);

const sourceId = (n: number) => `source-${n}`;

export function CitationProvider({ sources, children }: { sources: CitedSource[]; children: ReactNode }) {
  const [highlighted, setHighlighted] = useState<number | null>(null);
  const timer = useRef<number | undefined>(undefined);
  const byNumber = useMemo(() => new Map(sources.map((s) => [s.n, s])), [sources]);
  useEffect(() => () => window.clearTimeout(timer.current), []);

  const jump = useCallback((n: number) => {
    const target = document.getElementById(sourceId(n));
    if (!target) return;
    const still = window.matchMedia?.("(prefers-reduced-motion: reduce)").matches;
    target.scrollIntoView({ block: "center", behavior: still ? "auto" : "smooth" });
    target.focus({ preventScroll: true });
    setHighlighted(n);
    window.clearTimeout(timer.current);
    timer.current = window.setTimeout(() => setHighlighted(null), HIGHLIGHT_MS);
  }, []);

  const value = useMemo(() => ({ byNumber, highlighted, jump }), [byNumber, highlighted, jump]);
  return <CitationContext value={value}>{children}</CitationContext>;
}

function Marker({ source, onJump }: { source: CitedSource; onJump: (n: number) => void }) {
  const outlet = sourceOutlet(source);
  return (
    <Tooltip>
      <TooltipTrigger asChild>
        <a
          href={`#${sourceId(source.n)}`}
          aria-label={`Source ${source.n}: ${outlet}, ${source.title}`}
          onClick={(event) => {
            event.preventDefault();
            onJump(source.n);
          }}
          data-numeric
          className="relative -top-1 rounded-xs px-px text-meta font-medium text-muted-foreground no-underline transition-colors duration-150 ease-out-quart hover:text-primary-ink focus-visible:text-primary-ink focus-visible:ring-2 focus-visible:ring-ring focus-visible:outline-none"
        >
          [{source.n}]
        </a>
      </TooltipTrigger>
      <TooltipContent className="max-w-72 text-left">
        <span className="font-semibold">{outlet}</span> · {source.title}
      </TooltipContent>
    </Tooltip>
  );
}

/**
 * The `[n]` markers after a cited item. They hang on the item's last word (no break before
 * them) so a marker never starts a line alone. Numbers missing from the Sources list are skipped.
 */
export function Cites({ cites }: { cites: number[] }) {
  const citations = useContext(CitationContext);
  const shown = citations ? cites.filter((n) => citations.byNumber.has(n)) : [];
  if (!citations || shown.length === 0) return null;
  return (
    <span className="whitespace-nowrap">
      {" "}
      {shown.map((n) => (
        <Marker key={n} source={citations.byNumber.get(n)!} onJump={citations.jump} />
      ))}
    </span>
  );
}

/**
 * One numbered entry of a Sources list: the target a `[n]` marker jumps to (focusable, washed in
 * `accent` while it is followed), its number in the lead column and its content beside it. The
 * analysis page's list and the competitor deep dive's (sources of another shape) share it.
 */
export function SourceItem({ n, children }: { n: number; children: ReactNode }) {
  const citations = useContext(CitationContext);
  return (
    <li id={sourceId(n)} tabIndex={-1} data-highlighted={citations?.highlighted === n ? "true" : undefined} className="group/source scroll-mt-24 py-2 outline-none">
      {/* The wash spreads past the column with a box-shadow, so it never widens the page. */}
      <div className="grid grid-cols-[1.75rem_minmax(0,1fr)] gap-x-2 rounded-sm py-1 transition-[background-color,box-shadow] duration-700 ease-out-quart group-data-[highlighted=true]/source:bg-accent group-data-[highlighted=true]/source:shadow-[0_0_0_0.5rem_var(--accent)] group-data-[highlighted=true]/source:duration-150">
        <span data-numeric className="text-body font-semibold text-muted-foreground group-data-[highlighted=true]/source:text-primary-ink">
          {n}
        </span>
        <div className="flex min-w-0 flex-col gap-0.5">{children}</div>
      </div>
    </li>
  );
}

/** The page's numbered Sources: outlet, title (linked), date and "Primary"; cited JFrog docs and signals follow the documents. */
export function SourcesList({ sources }: { sources: CitedSource[] }) {
  if (sources.length === 0) return <p className="text-body text-muted-foreground">No source documents are linked.</p>;
  return (
    <ol className="flex flex-col divide-y">
      {sources.map((s) => (
        <SourceItem key={s.n} n={s.n}>
          <SourceLink source={s} className="w-fit text-body font-medium text-foreground" />
          <SourceMeta source={s} />
        </SourceItem>
      ))}
    </ol>
  );
}
