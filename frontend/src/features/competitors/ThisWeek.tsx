import { ChevronRightIcon } from "lucide-react";
import { useId } from "react";
import { Link } from "react-router";
import { CompetitorChip } from "@/components/common/CompetitorChip";
import { EmptyState } from "@/components/common/EmptyState";
import { Num } from "@/components/common/Num";
import { analysisHref } from "@/components/signal/param";
import { SectionHead } from "@/components/layout/SectionHead";
import { ImportanceMeter } from "@/features/signals/SignalRow";
import { plainDay } from "@/lib/time";
import { cn } from "@/lib/utils";
import { thisWeek, weekStart, type CompetitorSummary, type Move } from "./useCompetitorIntel";

/*
 * The week's biggest moves: each competitor whose latest move fell in the last 7 local
 * days, most important first, five at most. A ledger, not cards: one line per move in the
 * Explorer's grammar (importance leads, as a number over its quiet meter), then the company,
 * the serif headline and where and when it happened. Each line is ONE link to the move's full
 * analysis, so the company chip inside it is text (DESIGN.md: nothing interactive in a link).
 */

/** The ledger's columns from `md`: importance, company, headline, lane and day, the chevron. */
const GRID = "md:grid-cols-[2.75rem_9.5rem_minmax(0,1fr)_auto_1rem] md:items-center md:gap-x-5";

const LINE = cn(
  "group/move relative isolate grid min-w-0 cursor-pointer grid-cols-[auto_minmax(0,1fr)] items-center gap-x-3 gap-y-1.5 py-3",
  GRID,
  // The wash bleeds sideways between the hairlines, as on the Briefing's story rows.
  "before:pointer-events-none before:absolute before:inset-y-0 before:-inset-x-3 before:-z-10 before:rounded-md before:content-['']",
  "before:transition-colors before:duration-150 before:ease-out-quart hover:before:bg-accent/70 active:before:bg-accent",
  "focus-visible:outline-none focus-visible:before:ring-2 focus-visible:before:ring-ring",
);

function MoveLine({ competitor, move }: { competitor: CompetitorSummary; move: Move }) {
  const titleId = useId();
  const metaId = useId();
  return (
    <li>
      <Link to={analysisHref(move.signal_id)} aria-labelledby={titleId} aria-describedby={metaId} className={LINE}>
        {/* The Explorer's meter; the line's meta names it to screen readers. */}
        <ImportanceMeter aria-hidden value={move.importance} className="pt-0 sm:hidden md:flex" />
        <CompetitorChip slug={competitor.slug} name={competitor.name} plain className="row-start-2 w-fit md:row-start-auto" />
        <span
          id={titleId}
          className="col-span-2 row-start-1 font-headline text-title text-balance text-foreground decoration-1 underline-offset-[0.18em] group-hover/move:underline md:col-span-1 md:row-start-auto"
        >
          {move.title}
        </span>
        <span id={metaId} className="row-start-2 flex flex-wrap items-center gap-x-2 text-meta whitespace-nowrap text-muted-foreground md:row-start-auto md:justify-end">
          <span className="font-medium text-foreground">{move.lane}</span>
          <span aria-hidden>·</span>
          {/* One format for lane · day on the list, the table's */}
          <span>{plainDay(move.day, "dayMonth")}</span>
          <span className="md:sr-only">
            <span aria-hidden className="md:hidden">
              ·{" "}
            </span>
            Importance <Num value={move.importance} className="font-semibold text-foreground" />
          </span>
        </span>
        <ChevronRightIcon
          aria-hidden
          className="hidden size-4 text-muted-foreground transition-[color,translate] duration-150 ease-out-quart group-hover/move:translate-x-0.5 group-hover/move:text-foreground md:block"
        />
      </Link>
    </li>
  );
}

/** "This week": the strip of the biggest moves, or "No major moves this week". */
export function ThisWeek({ rows, today }: { rows: CompetitorSummary[]; today: string }) {
  const headId = useId();
  const moves = thisWeek(rows, today);
  const range = `${plainDay(weekStart(today), "dayMonth")} – ${plainDay(today, "dayMonth")}`;
  return (
    <section aria-labelledby={headId} className="flex flex-col">
      <SectionHead id={headId} title="This week" meta={`${range} · each competitor's latest move, most important first`} className="pb-1" />
      {moves.length === 0 ? (
        <EmptyState
          className="mt-3 max-w-none"
          title="No major moves this week"
          hint="No tracked competitor filed a public signal in the last 7 days. Each one's latest move is in the table below."
        />
      ) : (
        <ol aria-label="Biggest moves this week" className="flex flex-col divide-y">
          {moves.map(({ competitor, move }) => (
            <MoveLine key={competitor.slug} competitor={competitor} move={move} />
          ))}
        </ol>
      )}
    </section>
  );
}
