import { Swatch } from "@/components/charts/tooltip";
import { RelativeTime } from "@/components/common/RelativeTime";
import { analysisHref } from "@/components/signal/param";
import type { SignalCard } from "@/components/signal/query";
import { ExternalLink } from "@/components/signal/SourceLink";
import { MetaLine } from "@/features/intel/grammar";
import { dayLabel } from "@/features/intel/words";
import { SignalRow } from "@/features/signals/SignalRow";
import { cn } from "@/lib/utils";
import { factHeadline, factValue, sourceDay, sourceName } from "../profileWords";
import type { FactSource, LedgerRow } from "../useCompetitorProfile";

/*
 * The competitor deep dive's own pieces; the grammar both deep dives share (sections, block heads,
 * quiet notes, "How this is computed", cited sources) is in features/intel/grammar:
 * - reported figures are a ledger: the as-of day in a term column, the figure in the source's
 *   words, the head source's verified quote, then every source as a link with its date;
 * - a scope toggle, a source's name and its freshness.
 */

/** "Sources disagree", with the kit's hollow ring (the same mark as on the charts). */
export function Disagree({ className }: { className?: string }) {
  return (
    <span className={cn("inline-flex items-center gap-1.5 text-meta font-medium text-foreground", className)}>
      <Swatch color="chart-1" shape="ring" />
      Sources disagree
    </span>
  );
}

/** A segmented toggle for a chart's scope (7/30/90 days, department/seniority/location). */
export function Toggle<T extends string | number>({ label, options, value, onChange }: { label: string; options: { value: T; label: string }[]; value: T; onChange: (value: T) => void }) {
  return (
    <div role="group" aria-label={label} className="inline-flex shrink-0 rounded-md border border-input bg-card p-0.5">
      {options.map((o) => {
        const on = o.value === value;
        return (
          <button
            key={String(o.value)}
            type="button"
            aria-pressed={on}
            onClick={() => onChange(o.value)}
            className={cn(
              "h-7 cursor-pointer rounded-[5px] px-2.5 text-meta font-medium whitespace-nowrap text-muted-foreground transition-colors duration-150 ease-out-quart",
              "hover:text-foreground focus-visible:ring-2 focus-visible:ring-ring focus-visible:outline-none",
              on && "bg-secondary text-foreground",
            )}
          >
            {o.label}
          </button>
        );
      })}
    </div>
  );
}

/** A source as a link: its outlet (or page) name, opening in a new tab; text when it has no address. */
export function SourceName({ source, className }: { source: FactSource; className?: string }) {
  const name = sourceName(source);
  // No hover arrow: on a dense line of names its reserved space reads as a gap after each one.
  return source.url ? (
    <ExternalLink href={source.url} arrow={false} className={cn("font-medium text-foreground", className)}>
      {name}
    </ExternalLink>
  ) : (
    <span className={cn("font-medium text-foreground", className)}>{name}</span>
  );
}

/**
 * Reported figures, each with every source: the as-of day, the figure in the source's words
 * ("Series B · $35M"), "Sources disagree" when they do, the head source's verified quote, then
 * each source linked with its date and, when it states another value, that value.
 */
export function FactLedger({ label, rows, className }: { label: string; rows: LedgerRow[]; className?: string }) {
  return (
    <ul aria-label={label} className={cn("flex flex-col divide-y", className)}>
      {rows.map((row) => {
        const head = row.sources[0];
        const own = factValue(row);
        return (
          <li key={row.key} className="grid gap-x-6 gap-y-1 py-3 first:pt-1 last:pb-0 sm:grid-cols-[7.5rem_minmax(0,1fr)]">
            <span className="text-meta text-muted-foreground sm:pt-0.5">{row.as_of ? dayLabel(row.as_of) : "No date given"}</span>
            <div className="flex min-w-0 flex-col gap-1">
              <p className="flex flex-wrap items-baseline gap-x-3 gap-y-0.5">
                <span className="text-body font-medium text-foreground">{factHeadline(row)}</span>
                {row.sources_disagree ? <Disagree /> : null}
              </p>
              {head?.quote ? <p className="line-clamp-3 max-w-[72ch] text-body text-muted-foreground">“{head.quote}”</p> : null}
              <p className="flex flex-wrap items-center gap-x-2 gap-y-1 text-meta text-muted-foreground">
                {row.sources.map((s, i) => {
                  const theirs = factValue(s);
                  const day = sourceDay(s);
                  // The day the row already gives is not repeated; another source's own day is.
                  const shown = day && !(row.as_of && day === dayLabel(row.as_of)) ? day : null;
                  return (
                    <span key={s.fact_id} className="inline-flex flex-wrap items-center gap-x-1.5">
                      {i > 0 ? <span aria-hidden>·</span> : null}
                      <SourceName source={s} />
                      {shown ? <span>{shown}</span> : null}
                      {theirs && own && theirs !== own ? <span>states {theirs}</span> : null}
                    </span>
                  );
                })}
              </p>
            </div>
          </li>
        );
      })}
    </ul>
  );
}

/**
 * Signals in the Explorer's row grammar (`SignalRow`), each ONE link to its full analysis:
 * importance leads, then the serif title, its so-what and the meta line (stance and type, other
 * companies, source, time, trust line). The page's own company is not repeated as a chip.
 */
export function SignalLinks({ label, signals, exclude }: { label: string; signals: SignalCard[]; exclude: string }) {
  return (
    <ul aria-label={label} className="flex flex-col divide-y">
      {signals.map((s) => (
        <li key={s.id}>
          <SignalRow signal={s} href={analysisHref(s.id)} exclude={exclude} />
        </li>
      ))}
    </ul>
  );
}

/** "Sonatype on Greenhouse · fetched 6h ago": a collection source and its last fetch. */
export function Freshness({ name, fetched, verb = "fetched" }: { name: string; fetched: string | null; verb?: string }) {
  return (
    <MetaLine
      parts={[
        <span key="n">{name}</span>,
        fetched ? (
          <span key="f">
            {/* Mid-line, so "Yesterday" reads "yesterday"; dates and "6h ago" are unchanged. */}
            {verb} <RelativeTime iso={fetched} className="inline-block first-letter:lowercase" />
          </span>
        ) : (
          <span key="f">not fetched yet</span>
        ),
      ]}
    />
  );
}
