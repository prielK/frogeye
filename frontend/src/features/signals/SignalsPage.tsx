import { useQueryClient } from "@tanstack/react-query";
import { ArrowRightIcon, InfoIcon, RotateCwIcon, SearchIcon, XIcon } from "lucide-react";
import { useCallback, useMemo, useRef, type ReactNode } from "react";
import { Link, useSearchParams } from "react-router";
import { EmptyState } from "@/components/common/EmptyState";
import { PageHeader } from "@/components/layout/PageHeader";
import { useSignalParam } from "@/components/signal/param";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";
import { ApiError } from "@/lib/api/client";
import { useAuth } from "@/lib/auth";
import { useCompetitors } from "@/lib/competitors";
import { plural } from "@/lib/format";
import { cn } from "@/lib/utils";
import { SignalFilters } from "./SignalFilters";
import { HIT_GRID, SearchHitRow, SIGNAL_GRID, SignalRow } from "./SignalRow";
import {
  hasFilters,
  parseFilters,
  searchTerm,
  toParams,
  useRowKeys,
  useSearchEverything,
  useSignals,
  type ExplorerFilters,
} from "./useSignals";

const KEY = "min-w-5 rounded-xs border border-input bg-card px-1 text-center font-sans text-meta text-foreground";

/** The line above a list: what the lead column holds, how it is ordered, and the keys. */
function ListHead({ lead, leadHint, grid, children }: { lead: string; leadHint?: string; grid: string; children: ReactNode }) {
  return (
    <div className={cn("mt-7 grid items-baseline gap-x-5 border-b pb-2 text-meta text-muted-foreground", grid)}>
      {leadHint ? (
        <Tooltip>
          <TooltipTrigger asChild>
            <button
              type="button"
              className="hidden w-fit rounded-xs font-medium decoration-dotted underline-offset-[0.25em] hover:text-foreground hover:underline focus-visible:ring-2 focus-visible:ring-ring focus-visible:outline-none sm:block"
            >
              {lead}
            </button>
          </TooltipTrigger>
          <TooltipContent align="start" className="max-w-72 text-left">
            {leadHint}
          </TooltipContent>
        </Tooltip>
      ) : (
        <span className="hidden font-medium sm:block">{lead}</span>
      )}
      <div className="flex flex-wrap items-baseline justify-between gap-x-6 gap-y-1">
        <p>{children}</p>
        <p className="hidden items-center gap-1 md:flex">
          <kbd className={KEY}>j</kbd>
          <kbd className={KEY}>k</kbd>
          <span className="ml-0.5">to move,</span>
          <kbd className={KEY}>Enter</kbd>
          <span>to open</span>
        </p>
      </div>
    </div>
  );
}

function RowsSkeleton({ label }: { label: string }) {
  return (
    <div role="status" aria-label={label} className="mt-7">
      <Skeleton className="mb-2 h-3.5 w-48" />
      <div className="divide-y border-t">
        {Array.from({ length: 6 }, (_, i) => (
          <div key={i} className={cn("grid gap-x-5 py-4", SIGNAL_GRID)}>
            <div className="hidden flex-col gap-2 pt-1 sm:flex">
              <Skeleton className="h-3.5 w-6" />
              <Skeleton className="h-1 w-10" />
            </div>
            <div className="flex flex-col gap-2">
              <Skeleton className={cn("h-5", i % 2 ? "w-3/5" : "w-4/5")} />
              <Skeleton className="h-3.5 w-full max-w-[75ch]" />
              <div className="mt-1 flex gap-2">
                <Skeleton className="h-5 w-28" />
                <Skeleton className="h-5 w-20 rounded-full" />
                <Skeleton className="h-5 w-24" />
              </div>
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}

function RetryButton({ onRetry, busy }: { onRetry: () => void; busy: boolean }) {
  return (
    <Button variant="outline" size="sm" onClick={onRetry} loading={busy}>
      <RotateCwIcon />
      Try again
    </Button>
  );
}

const rateLimited = (error: unknown) => error instanceof ApiError && error.status === 429;

const IMPORTANCE_HINT =
  "0 to 100: how much a signal matters to JFrog, from its impact, novelty, who it's about, the kind of event, key topics, credibility and corroboration, faded by age. Open a signal to see its breakdown.";

type ResultsProps = {
  filters: ExplorerFilters;
  setFilters: (next: ExplorerFilters) => void;
  openSignal: (id: number) => void;
};

/** The signals list: most important first and paged, or a search's top matches in one page. */
function SignalResults({ filters, setFilters, openSignal }: ResultsProps) {
  const query = useSignals(filters);
  const queryClient = useQueryClient();
  const list = useRef<HTMLOListElement>(null);
  const items = useMemo(() => query.data?.pages.flatMap((p) => p.items) ?? [], [query.data]);
  const q = searchTerm(filters);
  const filtered = hasFilters(filters);
  const mode = query.data?.pages[0]?.search_mode;
  useRowKeys(list, () => {
    if (query.hasNextPage && !query.isFetchingNextPage) void query.fetchNextPage();
  });

  if (query.isPending) return <RowsSkeleton label="Loading signals" />;
  if (query.isError && items.length === 0) {
    return (
      <EmptyState
        tone="error"
        className="mt-7"
        title={rateLimited(query.error) ? "That's a lot of searches in a minute." : "The signals didn't load."}
        hint={
          rateLimited(query.error)
            ? "FrogEye allows 30 searches a minute each. Wait a moment, then try again."
            : "FrogEye's API didn't answer. Check your connection, then try again."
        }
        action={<RetryButton onRetry={() => query.refetch()} busy={query.isRefetching} />}
      />
    );
  }
  if (items.length === 0) {
    const clear = (
      <Button variant="outline" size="sm" onClick={() => setFilters({})}>
        <XIcon />
        Clear filters
      </Button>
    );
    if (q) {
      return (
        <EmptyState
          className="mt-7"
          title={`No signals match “${q}”.`}
          hint="Search reads each signal's title, fact, impact and summary. Try fewer or different words, or search everything to include articles and JFrog docs."
          action={
            <>
              <Button variant="outline" size="sm" onClick={() => setFilters({ ...filters, everything: true })}>
                <SearchIcon />
                Search everything
              </Button>
              {clear}
            </>
          }
        />
      );
    }
    return filtered ? (
      <EmptyState
        className="mt-7"
        title="No signals match these filters."
        hint="Lower the minimum importance, widen the date range, or pick another company or type."
        action={clear}
      />
    ) : (
      <EmptyState
        className="mt-7"
        title="No signals yet"
        hint="FrogEye's agents write signals each morning from the relevant documents the daily run collects, then rank them by importance and grade their sources. The Feed shows what has been collected so far."
        action={
          <Button asChild variant="outline" size="sm">
            <Link to="/feed">
              Open the Feed
              <ArrowRightIcon />
            </Link>
          </Button>
        }
      />
    );
  }

  const nextFailed = query.isFetchNextPageError;
  const staleCursor = nextFailed && query.error instanceof ApiError && query.error.status === 422;
  return (
    <div aria-busy={query.isPlaceholderData || undefined} className={cn("transition-opacity duration-200 ease-out-quart", query.isPlaceholderData && "opacity-55")}>
      <ListHead lead="Importance" leadHint={IMPORTANCE_HINT} grid={SIGNAL_GRID}>
        {q ? (
          <>
            {plural(items.length, "best match", "best matches")} for <span className="font-medium text-foreground">“{q}”</span>
          </>
        ) : (
          "Most important first"
        )}
      </ListHead>
      {mode === "keyword" ? <KeywordNote /> : null}
      <ol ref={list} aria-label="Signals" className="divide-y">
        {items.map((signal) => (
          <li key={signal.id}>
            <SignalRow
              signal={signal}
              onOpen={openSignal}
              activeCompetitor={filters.competitor}
              onCompetitor={(slug) => setFilters({ ...filters, competitor: slug })}
            />
          </li>
        ))}
      </ol>
      <div className="flex flex-col items-start gap-2 border-t pt-5 pb-2">
        {nextFailed ? (
          <EmptyState
            tone="error"
            title={staleCursor ? "The ranking changed since this page loaded." : "The next page didn't load."}
            hint={
              staleCursor
                ? "New signals arrived or scores moved, so the place you were reading from no longer exists. Start again from the top."
                : "Check your connection, then try again. Everything above is still current."
            }
            action={
              staleCursor ? (
                <Button variant="outline" size="sm" onClick={() => queryClient.resetQueries({ queryKey: ["signals", "list"] })}>
                  Start from the top
                </Button>
              ) : (
                <RetryButton onRetry={() => query.fetchNextPage()} busy={query.isFetchingNextPage} />
              )
            }
          />
        ) : query.hasNextPage ? (
          <>
            <Button variant="outline" onClick={() => query.fetchNextPage()} loading={query.isFetchingNextPage}>
              {query.isFetchingNextPage ? "Loading…" : "Load more"}
            </Button>
            <p className="text-meta text-muted-foreground">Showing the top {plural(items.length, "signal")}.</p>
          </>
        ) : (
          <p className="text-meta text-muted-foreground">
            {q
              ? items.length >= 50
                ? "The top 50 matches. Add a word or a filter to narrow them."
                : `That's every match: ${plural(items.length, "signal")}.`
              : `That's every signal${filtered ? " that matches" : ""}: ${plural(items.length, "signal")}.`}
          </p>
        )}
      </div>
    </div>
  );
}

/** The search ran on keywords alone (no key, an embedding error, or the budget spent). */
function KeywordNote() {
  return (
    <p className="mt-3 flex flex-wrap items-center gap-x-1.5 text-meta text-muted-foreground">
      <InfoIcon aria-hidden className="size-3.5 shrink-0" />
      <span className="font-medium text-foreground">Keyword results — AI search unavailable</span>
      <span>These match your exact words, not their meaning.</span>
    </p>
  );
}

/** "Search everything": articles, signals and JFrog docs from GET /search, ranked together. */
function EverythingResults({ filters, setFilters, openSignal, analyst }: ResultsProps & { analyst: boolean }) {
  const q = searchTerm(filters);
  const query = useSearchEverything(filters);
  const list = useRef<HTMLOListElement>(null);
  useRowKeys(list);

  if (!q) {
    return (
      <EmptyState
        className="mt-7"
        title="Search articles, signals and JFrog docs at once."
        hint="Type two or more characters. FrogEye reads the question, searches every collected article, every signal and the JFrog knowledge base, and ranks the results together, each marked with its kind."
      />
    );
  }
  if (query.isPending) return <RowsSkeleton label="Searching" />;
  if (query.isError && !query.data) {
    if (query.error instanceof ApiError && query.error.status === 503) {
      return (
        <EmptyState
          className="mt-7"
          title="AI search unavailable"
          hint={
            <>
              Search everything isn't answering right now.{" "}
              {analyst ? "Check that the API and its workers are up." : "A CI analyst can check the server."} Signal search still
              works, matching your exact words.
            </>
          }
          action={
            <Button variant="outline" size="sm" onClick={() => setFilters({ ...filters, everything: undefined })}>
              <SearchIcon />
              Search signals instead
            </Button>
          }
        />
      );
    }
    return (
      <EmptyState
        tone="error"
        className="mt-7"
        title={rateLimited(query.error) ? "That's a lot of searches in a minute." : "The search didn't finish."}
        hint={
          rateLimited(query.error)
            ? "FrogEye allows 30 searches a minute each. Wait a moment, then try again."
            : "FrogEye's API didn't answer. Check your connection, then try again."
        }
        action={<RetryButton onRetry={() => query.refetch()} busy={query.isRefetching} />}
      />
    );
  }

  const hits = query.data?.hits ?? [];
  const rewritten = query.data?.plan?.rewritten?.trim();
  if (hits.length === 0) {
    return (
      <EmptyState
        className="mt-7"
        title={`Nothing matches “${q}”.`}
        hint="Try fewer or different words, or widen the company, date and kind filters."
        action={
          hasFilters({ ...filters, q: undefined }) ? (
            <Button variant="outline" size="sm" onClick={() => setFilters({ everything: true, q: filters.q })}>
              <XIcon />
              Clear filters
            </Button>
          ) : undefined
        }
      />
    );
  }
  return (
    <div aria-busy={query.isPlaceholderData || undefined} className={cn("transition-opacity duration-200 ease-out-quart", query.isPlaceholderData && "opacity-55")}>
      <ListHead lead="Kind" grid={HIT_GRID}>
        {plural(hits.length, "result")} for <span className="font-medium text-foreground">“{q}”</span>, best first
        {rewritten && rewritten.toLowerCase() !== q.toLowerCase() ? <span>, read as “{rewritten}”</span> : null}
      </ListHead>
      {query.data?.search_mode === "keyword" ? <KeywordNote /> : null}
      <ol ref={list} aria-label="Search results" className="divide-y border-b">
        {hits.map((hit, i) => (
          <li key={`${hit.kind}-${hit.ref_id}-${i}`}>
            <SearchHitRow hit={hit} query={q} onOpenSignal={openSignal} />
          </li>
        ))}
      </ol>
    </div>
  );
}

export function SignalsPage() {
  const { user } = useAuth();
  const analyst = user?.role === "analyst";
  const [params, setParams] = useSearchParams();
  const filters = useMemo(() => parseFilters(params, { analyst }), [params, analyst]);
  const setFilters = useCallback(
    (next: ExplorerFilters) => setParams((current) => toParams(next, current), { replace: true }),
    [setParams],
  );
  const { openSignal } = useSignalParam();
  const competitors = useCompetitors();
  const props = { filters, setFilters, openSignal };

  return (
    <>
      <PageHeader title="Signals" description="Every analysed story, searchable. The Briefing is drawn from these." />
      <SignalFilters filters={filters} analyst={analyst} competitors={competitors.data} onChange={setFilters} />
      {filters.everything ? <EverythingResults {...props} analyst={analyst} /> : <SignalResults {...props} />}
    </>
  );
}
