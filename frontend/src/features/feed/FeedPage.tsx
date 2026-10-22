import { useQueryClient } from "@tanstack/react-query";
import { ArrowRightIcon, RotateCwIcon, XIcon } from "lucide-react";
import { useCallback, useId, useMemo } from "react";
import { Link, useSearchParams } from "react-router";
import { EmptyState } from "@/components/common/EmptyState";
import { PageHeader } from "@/components/layout/PageHeader";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { ApiError, type DocumentOut } from "@/lib/api/client";
import { useCompetitorIndex, useCompetitors } from "@/lib/competitors";
import { plural } from "@/lib/format";
import { dayHeading, dayKey, useNow } from "@/lib/time";
import { cn } from "@/lib/utils";
import { DocumentRow } from "./DocumentRow";
import { FeedFilters } from "./FeedFilters";
import { hasFilters, parseFilters, useDocuments, type DocumentFilters } from "./useDocuments";

type Day = { key: string; iso: string | null; items: DocumentOut[] };

/** Consecutive documents grouped by local publication day (the API returns newest first). */
function groupByDay(items: DocumentOut[]): Day[] {
  const days: Day[] = [];
  for (const item of items) {
    const key = dayKey(item.published_at);
    const last = days.at(-1);
    if (last?.key === key) last.items.push(item);
    else days.push({ key, iso: item.published_at, items: [item] });
  }
  return days;
}

function toParams(filters: DocumentFilters): URLSearchParams {
  const params = new URLSearchParams();
  for (const [key, value] of Object.entries(filters)) if (value) params.set(key, value);
  return params;
}

function DayHeading({ id, iso, now }: { id: string; iso: string | null; now: number }) {
  const heading = iso ? dayHeading(iso, now) : { name: "Undated", date: "Publication date unknown" };
  return (
    <h2
      id={id}
      className="sticky top-14 z-(--z-dropdown) flex items-baseline gap-2 border-b bg-background pt-5 pb-2 text-body font-semibold"
    >
      {heading.name ?? heading.date}
      {heading.name ? <span className="text-meta font-normal text-muted-foreground">{heading.date}</span> : null}
    </h2>
  );
}

function DaySection({ day, now, ...rowProps }: { day: Day; now: number } & Omit<Parameters<typeof DocumentRow>[0], "doc">) {
  const id = useId();
  return (
    <section aria-labelledby={id}>
      <DayHeading id={id} iso={day.iso} now={now} />
      <ol className="divide-y">
        {day.items.map((doc) => (
          <li key={doc.id}>
            <DocumentRow doc={doc} {...rowProps} />
          </li>
        ))}
      </ol>
    </section>
  );
}

function FeedSkeleton() {
  return (
    <div role="status" aria-label="Loading documents" className="mt-5">
      <Skeleton className="mb-1 h-4 w-40" />
      <div className="divide-y border-t">
        {Array.from({ length: 6 }, (_, i) => (
          <div key={i} className="grid gap-x-5 py-4 sm:grid-cols-[4.75rem_minmax(0,1fr)]">
            <Skeleton className="mt-1 hidden h-3 w-12 sm:block" />
            <div className="flex flex-col gap-2">
              <Skeleton className={cn("h-5", i % 2 ? "w-3/5" : "w-4/5")} />
              <Skeleton className="h-3.5 w-full max-w-[75ch]" />
              <Skeleton className="h-3.5 w-2/3 max-w-[50ch]" />
              <div className="mt-1 flex gap-2">
                <Skeleton className="h-5 w-24" />
                <Skeleton className="h-5 w-20 rounded-full" />
              </div>
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}

export function FeedPage() {
  const [params, setParams] = useSearchParams();
  const filters = useMemo(() => parseFilters(params), [params]);
  const setFilters = useCallback((next: DocumentFilters) => setParams(toParams(next), { replace: true }), [setParams]);
  const queryClient = useQueryClient();
  const now = useNow();

  const competitorList = useCompetitors();
  const competitors = useCompetitorIndex();
  const docs = useDocuments(filters);

  const items = useMemo(() => docs.data?.pages.flatMap((page) => page.items) ?? [], [docs.data]);
  const days = useMemo(() => groupByDay(items), [items]);
  const filtered = hasFilters(filters);
  const restart = () => queryClient.resetQueries({ queryKey: ["documents", filters], exact: true });

  let body;
  if (docs.isPending) {
    body = <FeedSkeleton />;
  } else if (docs.isError && items.length === 0) {
    body = (
      <EmptyState
        tone="error"
        className="mt-6"
        title="The feed didn't load."
        hint="FrogEye's API didn't answer. Check your connection, then try again."
        action={
          <Button variant="outline" size="sm" onClick={() => docs.refetch()} loading={docs.isRefetching}>
            <RotateCwIcon />
            Try again
          </Button>
        }
      />
    );
  } else if (items.length === 0) {
    body = filtered ? (
      <EmptyState
        className="mt-6"
        title="No documents match these filters."
        hint="Search looks at titles only. Widen the scope or status, or pick another company."
        action={
          <Button variant="outline" size="sm" onClick={() => setFilters({})}>
            <XIcon />
            Clear filters
          </Button>
        }
      />
    ) : (
      <EmptyState
        className="mt-6"
        title="No documents yet — run ingestion from Admin › Runs"
        hint="FrogEye collects from its public sources every morning at 06:00, Israel time. Each run drops duplicates, screens what's left for JFrog and competitor mentions, and lists the results here, newest first."
        action={
          <Button asChild variant="outline" size="sm">
            <Link to="/admin/runs">
              Go to Runs
              <ArrowRightIcon />
            </Link>
          </Button>
        }
      />
    );
  } else {
    const nextFailed = docs.isFetchNextPageError;
    const staleCursor = nextFailed && docs.error instanceof ApiError && docs.error.status === 422;
    body = (
      <div
        aria-busy={docs.isPlaceholderData || undefined}
        className={cn("transition-opacity duration-200 ease-out-quart", docs.isPlaceholderData && "opacity-55")}
      >
        {days.map((day) => (
          <DaySection
            key={day.key}
            day={day}
            now={now}
            competitors={competitors}
            activeCompetitor={filters.competitor}
            onCompetitor={(slug) => setFilters({ ...filters, competitor: slug })}
          />
        ))}

        <div className="flex flex-col items-start gap-2 border-t pt-5 pb-2">
          {nextFailed ? (
            <EmptyState
              tone="error"
              title={staleCursor ? "The feed changed since this page loaded." : "The next page didn't load."}
              hint={
                staleCursor
                  ? "New documents arrived, so the place you were reading from no longer exists. Start again from the newest."
                  : "Check your connection, then try again. Everything above is still current."
              }
              action={
                staleCursor ? (
                  <Button variant="outline" size="sm" onClick={restart}>
                    Start from the newest
                  </Button>
                ) : (
                  <Button variant="outline" size="sm" onClick={() => docs.fetchNextPage()}>
                    <RotateCwIcon />
                    Try again
                  </Button>
                )
              }
            />
          ) : docs.hasNextPage ? (
            <>
              <Button variant="outline" onClick={() => docs.fetchNextPage()} loading={docs.isFetchingNextPage}>
                {docs.isFetchingNextPage ? "Loading…" : "Load more"}
              </Button>
              <p className="text-meta text-muted-foreground">Showing the newest {plural(items.length, "document")}.</p>
            </>
          ) : (
            <p className="text-meta text-muted-foreground">
              That's everything{filtered ? " that matches" : ""}: {plural(items.length, "document")}.
            </p>
          )}
        </div>
      </div>
    );
  }

  return (
    <>
      <PageHeader title="Feed" description="Every public signal FrogEye has collected, newest first." />
      <FeedFilters filters={filters} competitors={competitorList.data} onChange={setFilters} />
      {body}
    </>
  );
}
