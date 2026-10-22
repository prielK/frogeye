import { ArrowUpRightIcon, ScanSearchIcon } from "lucide-react";
import { useId } from "react";
import { CompetitorChip } from "@/components/common/CompetitorChip";
import { RelativeTime } from "@/components/common/RelativeTime";
import { Badge } from "@/components/ui/badge";
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";
import type { Competitor, DocumentOut } from "@/lib/api/client";
import { nameFromSlug } from "@/lib/competitors";
import { hostOf, isWebUrl, plural } from "@/lib/format";
import { cn } from "@/lib/utils";
import { scopeLabel, statusLabel } from "./useDocuments";

const MAX_CHIPS = 5;

const squash = (text: string) => text.toLowerCase().replace(/[^\p{L}\p{N}]+/gu, "");

/** True when the excerpt adds nothing to the headline, e.g. Google News "Title - Outlet" / "Title Outlet". */
function echoesTitle(title: string | null, excerpt: string) {
  if (!title) return false;
  const headline = squash(title.replace(/\s[-–—|]\s[^-–—|]+$/, ""));
  const body = squash(excerpt);
  return headline.length > 0 && body.startsWith(headline) && body.length - headline.length < 40;
}

type DocumentRowProps = {
  doc: DocumentOut;
  competitors: Map<string, Competitor>;
  /** The competitor the feed is already narrowed to; its chip is not offered as a filter again. */
  activeCompetitor?: string;
  onCompetitor: (slug: string) => void;
};

function MatchedTerms({ terms }: { terms: string[] }) {
  if (terms.length === 0) return null;
  return (
    <Tooltip>
      <TooltipTrigger asChild>
        <button
          type="button"
          className={cn(
            "inline-flex h-6 items-center gap-1 rounded-sm px-1.5 text-meta text-muted-foreground",
            "transition-colors duration-150 hover:bg-accent hover:text-foreground",
            "focus-visible:ring-2 focus-visible:ring-ring focus-visible:outline-none",
          )}
        >
          <ScanSearchIcon aria-hidden className="size-3.5" />
          {plural(terms.length, "matched term")}
        </button>
      </TooltipTrigger>
      <TooltipContent align="start" className="max-w-72 text-left">
        <span className="font-medium">Why it's in the feed: </span>
        {terms.map((term) => `“${term}”`).join(", ")}
      </TooltipContent>
    </Tooltip>
  );
}

/** One collected document: the headline links out; source, time and the evidence for its match sit beside it. */
export function DocumentRow({ doc, competitors, activeCompetitor, onCompetitor }: DocumentRowProps) {
  const titleId = useId();
  const quiet = doc.status === "irrelevant" || doc.status === "duplicate";
  const shown = doc.competitors.slice(0, MAX_CHIPS);
  const hidden = doc.competitors.slice(MAX_CHIPS);
  const nameOf = (slug: string) => competitors.get(slug)?.name ?? nameFromSlug(slug);
  const headline = doc.title?.trim() || `Untitled page on ${hostOf(doc.url)}`;

  return (
    <article aria-labelledby={titleId} className="grid gap-x-5 py-4 sm:grid-cols-[4.75rem_minmax(0,1fr)]">
      <div className="hidden pt-1.5 text-meta text-muted-foreground sm:block">
        <RelativeTime iso={doc.published_at} emptyLabel="Publication date unknown" />
      </div>

      <div className="flex min-w-0 flex-col">
        <h3 id={titleId} className="font-headline text-title font-semibold">
          {isWebUrl(doc.url) ? (
            <a
              href={doc.url}
              target="_blank"
              rel="noopener noreferrer"
              className={cn(
                "group/title rounded-xs decoration-1 underline-offset-[0.22em] hover:underline",
                "focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 focus-visible:ring-offset-background focus-visible:outline-none",
                quiet ? "text-muted-foreground" : "text-foreground",
              )}
            >
              <span>{headline}</span>
              <ArrowUpRightIcon
                aria-hidden
                className="ml-0.5 inline size-3.5 -translate-y-px align-baseline text-muted-foreground opacity-0 transition-opacity duration-150 group-hover/title:opacity-100 group-focus-visible/title:opacity-100"
              />
              <span className="sr-only"> (opens in a new tab)</span>
            </a>
          ) : (
            // Not an http(s) address (e.g. javascript: or a bare id): show the headline, never a link.
            <span className={quiet ? "text-muted-foreground" : "text-foreground"}>{headline}</span>
          )}
        </h3>

        {doc.excerpt && !echoesTitle(doc.title, doc.excerpt) ? (
          <p className="mt-1 line-clamp-2 max-w-[75ch] text-body text-muted-foreground">{doc.excerpt}</p>
        ) : null}

        <div className="mt-2.5 flex flex-wrap items-center gap-x-2 gap-y-1.5 text-meta text-muted-foreground">
          <span className="font-medium text-foreground">{doc.source_name}</span>
          <span className="sm:hidden">
            <span aria-hidden>· </span>
            <RelativeTime iso={doc.published_at} emptyLabel="Publication date unknown" />
          </span>
          {doc.scope_hint ? (
            <Badge variant="outline">{scopeLabel(doc.scope_hint)}</Badge>
          ) : null}
          {quiet ? <Badge variant="neutral">{statusLabel(doc.status)}</Badge> : null}

          {shown.length > 0 ? (
            <ul aria-label="Companies mentioned" className="flex flex-wrap items-center gap-1.5">
              {shown.map((slug) => (
                <li key={slug} className="min-w-0">
                  <CompetitorChip
                    slug={slug}
                    name={nameOf(slug)}
                    self={competitors.get(slug)?.is_self}
                    onSelect={slug === activeCompetitor ? undefined : () => onCompetitor(slug)}
                    actionLabel={`Show only ${nameOf(slug)}`}
                    plain
                  />
                </li>
              ))}
              {hidden.length > 0 ? (
                <li>
                  <Tooltip>
                    <TooltipTrigger asChild>
                      <button
                        type="button"
                        className="h-6 rounded-full px-1.5 text-meta font-medium text-muted-foreground hover:text-foreground focus-visible:ring-2 focus-visible:ring-ring focus-visible:outline-none"
                      >
                        +{hidden.length} more
                      </button>
                    </TooltipTrigger>
                    <TooltipContent>{hidden.map(nameOf).join(", ")}</TooltipContent>
                  </Tooltip>
                </li>
              ) : null}
            </ul>
          ) : null}

          <MatchedTerms terms={doc.matched_terms} />
        </div>
      </div>
    </article>
  );
}
