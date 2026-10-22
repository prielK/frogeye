import { ArrowUpRightIcon, ChevronRightIcon } from "lucide-react";
import { useId } from "react";
import { Link } from "react-router";
import { Num } from "@/components/common/Num";
import { RelativeTime } from "@/components/common/RelativeTime";
import { Classification } from "@/components/signal/Classification";
import { CompanyChips } from "@/components/signal/CompanyChips";
import { EvidenceGrade } from "@/components/signal/EvidenceGrade";
import type { SignalCard } from "@/components/signal/query";
import { Badge } from "@/components/ui/badge";
import { hostOf, isWebUrl } from "@/lib/format";
import { cn } from "@/lib/utils";
import { KINDS, type RetrievalKind, type SearchHit } from "./useSignals";

/** Rows share the Feed's grammar: a lead column, then a serif title, an excerpt and a meta line. */
export const SIGNAL_GRID = "sm:grid-cols-[4.75rem_minmax(0,1fr)]";
/** Search everything's lead column holds the kind badge; "JFrog docs" needs the wider one. */
export const HIT_GRID = "sm:grid-cols-[5.75rem_minmax(0,1fr)]";
const ROW = "group relative grid gap-x-5 py-4";
const TITLE = "font-headline text-title";
/** The whole row is the hit target; controls inside it sit above this layer (relative z-10). */
const COVER = cn(
  "cursor-pointer text-left decoration-1 underline-offset-[0.2em] group-hover:underline",
  "scroll-mt-24 scroll-mb-8 after:absolute after:inset-y-0 after:-inset-x-3 after:content-['']",
  "focus-visible:outline-none focus-visible:after:rounded-md focus-visible:after:ring-2 focus-visible:after:ring-ring",
);

/*
 * Importance (dataviz): one value against a fixed maximum of 100, so a meter, not a chart.
 * The number leads (tabular, it aligns down the column); the bar is a quiet 4px reading of
 * the same value on a common 40px scale, so rows compare at a glance. One series and one hue
 * (--meter, validated >= 3:1 on the page ground in both themes), the track is --meter-track
 * (a reference extent; the value is always printed), square baseline, rounded data end.
 */
export function ImportanceMeter({ value, className, ...rest }: { value: number; className?: string; "aria-hidden"?: boolean }) {
  const share = Math.max(0, Math.min(100, value));
  return (
    <div {...rest} className={cn("hidden flex-col items-start gap-[0.4375rem] pt-1.5 sm:flex", className)}>
      <p className="text-body leading-none font-semibold text-foreground">
        <span className="sr-only">Importance </span>
        <Num value={value} />
      </p>
      <span aria-hidden className="relative h-1 w-10 rounded-r-full bg-meter-track">
        {share > 0 ? (
          <span className="absolute inset-y-0 left-0 rounded-r-full bg-meter" style={{ width: `max(2px, ${share}%)` }} />
        ) : null}
      </span>
    </div>
  );
}

type SignalRowProps = {
  signal: SignalCard;
  /** The Explorer: the title is a button that opens the shared drawer. */
  onOpen?: (id: number) => void;
  /** Elsewhere (the competitor deep dive): the title is ONE link to this address, its analysis page. */
  href?: string;
  /** Filter to a company from its chip; without it the chips link to the profiles. `activeCompetitor` is the one already filtered to. */
  onCompetitor?: (slug: string) => void;
  activeCompetitor?: string;
  /** A company not repeated as a chip (the page's own). */
  exclude?: string;
};

/** The row's chevron: shown at rest (DESIGN.md: a hover-only cue is no affordance); it nudges and darkens on hover, and lets a click through to the row's cover. */
const CHEVRON = cn(
  "pointer-events-none absolute top-[1.375rem] right-0 size-4 text-muted-foreground",
  "transition-[color,translate] duration-150 ease-out-quart group-hover:translate-x-0.5 group-hover:text-foreground",
);

/**
 * One signal in the Explorer: importance first (the list's order), the serif title opening
 * the shared drawer, its so-what (the analysis summary the quick look opens on, else
 * the first-take impact), then classification, companies, source, time and the trust line
 * (grade + approval state). The chevron says at rest that the row opens.
 */
export function SignalRow({ signal, onOpen, href, onCompetitor, activeCompetitor, exclude }: SignalRowProps) {
  const titleId = useId();
  const excerpt = signal.summary?.trim() || signal.impact?.trim() || signal.fact?.trim();
  const companies = exclude ? signal.competitors.filter((c) => c !== exclude) : signal.competitors;
  return (
    <article aria-labelledby={titleId} className={cn(ROW, SIGNAL_GRID)}>
      <ImportanceMeter value={signal.importance} />
      <div className="flex min-w-0 flex-col">
        <h3 id={titleId} className={cn(TITLE, "pr-6")}>
          {href ? (
            <Link to={href} data-row-target className={COVER}>
              {signal.title}
            </Link>
          ) : (
            <button type="button" data-row-target onClick={() => onOpen?.(signal.id)} className={COVER}>
              {signal.title}
            </button>
          )}
        </h3>
        <ChevronRightIcon aria-hidden className={CHEVRON} />
        {excerpt ? <p className="mt-1 line-clamp-2 max-w-[75ch] text-body text-muted-foreground">{excerpt}</p> : null}
        <div className="mt-2.5 flex flex-wrap items-center gap-x-3 gap-y-1.5 text-meta text-muted-foreground">
          <span className="sm:hidden">
            Importance <Num value={signal.importance} className="font-semibold text-foreground" />
          </span>
          <Classification signal={signal} compact />
          {companies.length > 0 ? <CompanyChips slugs={companies} max={3} onSelect={onCompetitor} active={activeCompetitor} className="relative z-10" /> : null}
          {signal.primary_source ? <span className="font-medium text-foreground">{signal.primary_source}</span> : null}
          <RelativeTime iso={signal.first_seen_at} className="relative z-10" />
          {/* Above the row's cover, so its tooltip is reachable. */}
          <EvidenceGrade grade={signal.grade} reliability={signal.reliability} credibility={signal.credibility} status={signal.status} className="relative z-10" />
        </div>
      </div>
    </article>
  );
}

const KIND_BADGE: Record<RetrievalKind, "outline" | "secondary" | "primary"> = {
  article_chunk: "outline",
  signal: "secondary",
  jfrog_kb: "primary",
};

/** The snippet with the searched words set in the foreground's weight: emphasis, not colour. */
function Snippet({ text, query }: { text: string; query: string }) {
  const words = [...new Set(query.toLowerCase().match(/[\p{L}\p{N}][\p{L}\p{N}.-]*/gu) ?? [])].filter((w) => w.length >= 2);
  if (words.length === 0) return text;
  const pattern = new RegExp(`(${words.map((w) => w.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")).join("|")})`, "giu");
  return text.split(pattern).map((part, i) =>
    i % 2 === 1 ? (
      <strong key={i} className="font-semibold text-foreground">
        {part}
      </strong>
    ) : (
      part
    ),
  );
}

/**
 * One "Search everything" result. The kind badge leads in the lead column; an article or a
 * JFrog doc links out (new tab), a signal opens the shared drawer.
 */
export function SearchHitRow({ hit, query, onOpenSignal }: { hit: SearchHit; query: string; onOpenSignal: (id: number) => void }) {
  const titleId = useId();
  const kind = KINDS.find((k) => k.value === hit.kind);
  const external = hit.kind !== "signal" && hit.url && isWebUrl(hit.url) ? hit.url : null;
  return (
    <article aria-labelledby={titleId} className={cn(ROW, HIT_GRID)}>
      <div className="pb-1.5 sm:pt-1 sm:pb-0">
        <Badge variant={KIND_BADGE[hit.kind as RetrievalKind] ?? "outline"}>{kind?.label ?? hit.kind}</Badge>
      </div>
      <div className="flex min-w-0 flex-col">
        <h3 id={titleId} className={cn(TITLE, "pr-6")}>
          {hit.kind === "signal" ? (
            <button type="button" data-row-target onClick={() => onOpenSignal(hit.ref_id)} className={COVER}>
              {hit.title}
            </button>
          ) : external ? (
            <a href={external} target="_blank" rel="noopener noreferrer" data-row-target className={cn(COVER, "group/title")}>
              <span>{hit.title}</span>
              <ArrowUpRightIcon
                aria-hidden
                className="ml-0.5 inline size-3.5 -translate-y-px text-muted-foreground opacity-0 transition-opacity duration-150 group-hover:opacity-100 group-focus-visible/title:opacity-100"
              />
              <span className="sr-only"> (opens in a new tab)</span>
            </a>
          ) : (
            <span className="text-foreground">{hit.title}</span>
          )}
        </h3>
        {hit.kind === "signal" ? <ChevronRightIcon aria-hidden className={CHEVRON} /> : null}
        {hit.snippet ? (
          <p className="mt-1 line-clamp-2 max-w-[75ch] text-body text-muted-foreground">
            <Snippet text={hit.snippet} query={query} />
          </p>
        ) : null}
        <div className="mt-2.5 flex flex-wrap items-center gap-x-3 gap-y-1.5 text-meta text-muted-foreground">
          {external ? <span className="font-medium text-foreground">{hostOf(external)}</span> : null}
          <CompanyChips slugs={hit.competitors} max={3} className="relative z-10" />
          {/* JFrog docs carry no date; an empty dash would read as missing data. */}
          {hit.published_at ? <RelativeTime iso={hit.published_at} className="relative z-10" /> : null}
        </div>
      </div>
    </article>
  );
}
