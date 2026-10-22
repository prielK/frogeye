import { ChevronRightIcon } from "lucide-react";
import { useId } from "react";
import { Link } from "react-router";
import { CompanyChips } from "@/components/signal/CompanyChips";
import type { SignalCard } from "@/components/signal/query";
import type { Schemas } from "@/lib/api/client";
import { plural } from "@/lib/format";
import { cn } from "@/lib/utils";
import { firstSentence, ROW_LINK, STORY_TEXT, STORY_TITLE } from "./story";
import { StoryMeta } from "./StoryMeta";
import { FOCUS_RING } from "./useBriefing";
import { ImportanceMeter } from "./Weight";

type Section = Schemas["Section"];
type SectionItem = Schemas["SectionItem"];
type WatchItem = Schemas["WatchItem"];

/** A section head: sans, on an ink rule, so sections read apart from the hairlines between items. */
function SectionHead({ id, title, count }: { id: string; title: string; count?: string }) {
  return (
    <h2 id={id} className="flex items-baseline justify-between gap-4 border-t border-foreground pt-3 pb-1 text-body font-semibold">
      {title}
      {count ? <span className="text-meta font-normal text-muted-foreground">{count}</span> : null}
    </h2>
  );
}

/**
 * One story as a compact row, the whole row one link to its full analysis page: its importance
 * meter in a gutter, the serif
 * title, the first sentence of its summary, then the meta line (stance, trust line, companies,
 * outlet, time). A row filed before summaries existed reads its why-it-matters line instead,
 * unmarked; one copied from the stage-A first read says so. A chevron at the row's
 * end says it goes somewhere, at rest; hover washes the row and underlines its title.
 */
function StoryRow({ item, signal, href }: { item: SectionItem; signal: SignalCard | undefined; href: string }) {
  const titleId = useId();
  const legacy = !item.summary.trim();
  const text = legacy ? item.why_it_matters.trim() : firstSentence(item.summary);
  return (
    <article aria-labelledby={titleId}>
      <Link
        to={href}
        aria-labelledby={titleId}
        className={cn(
          ROW_LINK,
          // The importance leads in a gutter, as in the Signals Explorer; from 2xl the meta line takes a column of its own.
          "grid grid-cols-[2.75rem_minmax(0,1fr)] content-start gap-x-4 gap-y-1.5 py-4 pr-7 2xl:grid-cols-[2.75rem_minmax(0,1fr)_15rem] 2xl:gap-x-8",
        )}
      >
        {signal ? <ImportanceMeter value={signal.importance} size="row" className="row-span-3 pt-1 2xl:row-span-2" /> : null}
        <h3 id={titleId} className={cn(STORY_TITLE, "col-start-2")}>
          {item.one_liner}
        </h3>
        <ChevronRightIcon
          aria-hidden
          className={cn(
            "absolute top-[1.375rem] right-0 size-4 text-muted-foreground",
            "transition-[color,translate] duration-150 ease-out-quart group-hover/story:translate-x-0.5 group-hover/story:text-foreground",
          )}
        />
        {text ? <p className={cn(STORY_TEXT, "col-start-2 text-muted-foreground")}>{text}</p> : null}
        <StoryMeta
          signals={signal ? [signal] : []}
          firstRead={!legacy && !item.from_analysis}
          stance
          className="col-start-2 mt-1 2xl:col-start-3 2xl:row-span-2 2xl:row-start-1 2xl:mt-0 2xl:content-start 2xl:pt-1.5"
        />
      </Link>
    </article>
  );
}

/** A themed section of the briefing ("Competitive moves", "Industry pulse"...). */
export function BriefingSection({
  section,
  signals,
  hrefFor,
}: {
  section: Section;
  signals: Map<number, SignalCard>;
  /** The full analysis page of a signal. */
  hrefFor: (id: number) => string;
}) {
  const id = useId();
  if (section.items.length === 0) return null;
  return (
    <section aria-labelledby={id}>
      <SectionHead id={id} title={section.theme} count={plural(section.items.length, "story", "stories")} />
      <ol className="divide-y">
        {section.items.map((item) => (
          <li key={item.signal_id}>
            <StoryRow item={item} signal={signals.get(item.signal_id)} href={hrefFor(item.signal_id)} />
          </li>
        ))}
      </ol>
    </section>
  );
}

/** The close of the briefing: what to keep watching, and which tier-1 competitors were quiet. */
export function BriefingFooter({
  watchlist,
  quiet,
  signals,
  told = new Set(),
  hrefFor,
}: {
  watchlist: WatchItem[];
  quiet: string[];
  signals: Map<number, SignalCard>;
  /** The signals already shown as a card or a row: a watch note on one prints no headline. */
  told?: Set<number>;
  hrefFor: (id: number) => string;
}) {
  const watchId = useId();
  const quietId = useId();
  if (watchlist.length === 0 && quiet.length === 0) return null;
  const both = watchlist.length > 0 && quiet.length > 0;
  return (
    <footer className={cn("grid gap-x-12 gap-y-10", both ? "md:grid-cols-2" : "max-w-[46rem]")}>
      {watchlist.length > 0 ? (
        <section aria-labelledby={watchId} className="flex flex-col">
          <SectionHead id={watchId} title="Watchlist" />
          <ul className="divide-y">
            {watchlist.map((w) => {
              const signal = signals.get(w.signal_id);
              // the headline only for a story not already on the page, and not when the note quotes it
              const subline = signal && !told.has(w.signal_id) && !w.note.includes(signal.title) ? signal.title : null;
              return (
                <li key={`${w.signal_id}-${w.note}`}>
                  {/* The chevron shows at rest that the note opens its story, as a story row's does. */}
                  <Link
                    to={hrefFor(w.signal_id)}
                    className={cn("group relative flex w-full flex-col gap-0.5 rounded-xs py-3 pr-7 text-left", FOCUS_RING)}
                  >
                    <span className="text-body text-foreground decoration-1 underline-offset-[0.2em] group-hover:underline">{w.note}</span>
                    {subline ? <span className="text-meta text-muted-foreground">{subline}</span> : null}
                    <ChevronRightIcon
                      aria-hidden
                      className={cn(
                        "absolute top-4 right-0 size-4 text-muted-foreground",
                        "transition-[color,translate] duration-150 ease-out-quart group-hover:translate-x-0.5 group-hover:text-foreground",
                      )}
                    />
                  </Link>
                </li>
              );
            })}
          </ul>
        </section>
      ) : null}
      {quiet.length > 0 ? (
        <section aria-labelledby={quietId} className="flex flex-col gap-3">
          <SectionHead id={quietId} title="Quiet competitors" />
          <p className="text-meta text-muted-foreground">
            No new signals from these tier-1 competitors in this briefing's window.
          </p>
          <CompanyChips slugs={quiet} max={12} />
        </section>
      ) : null}
    </footer>
  );
}
