import { ChevronRightIcon, PlayIcon, RotateCwIcon } from "lucide-react";
import { useId, useState } from "react";
import { Link } from "react-router";
import { EmptyState } from "@/components/common/EmptyState";
import { Num } from "@/components/common/Num";
import { PageHeader } from "@/components/layout/PageHeader";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";
import type { RunOut } from "@/lib/api/client";
import { useAuth } from "@/lib/auth";
import { formatCount, formatPercent } from "@/lib/format";
import { formatAbsolute, formatClock, formatDuration, formatMoment, formatRelative, useNow } from "@/lib/time";
import { cn } from "@/lib/utils";
import { FunnelBar, FunnelLegend } from "./FunnelBar";
import { RunStatusBadge } from "./RunStatusBadge";
import {
  AWAIT_START_MS,
  isRecorded,
  isRunActive,
  isRunInterrupted,
  lastSucceededFinish,
  prefilterPassRate,
  triggerLabel,
  useNextManualRunAt,
  useRuns,
  useStartRun,
} from "./useRuns";

const DETAIL_STATS: { key: string; label: string }[] = [
  { key: "sources_attempted", label: "Sources attempted" },
  { key: "sources_failed", label: "Sources failed" },
  { key: "fetched", label: "Items fetched" },
  { key: "new_raw", label: "New items" },
  { key: "documents_created", label: "Documents created" },
  { key: "duplicates", label: "Duplicates dropped" },
  { key: "prefilter_passed", label: "Passed the prefilter" },
  { key: "prefilter_rejected", label: "Filtered out" },
  { key: "metrics_written", label: "Metrics recorded" },
];

/** The job boards' changes, from every run's ingestion. */
const BOARD_STATS: { key: string; label: string }[] = [
  { key: "postings_new", label: "New postings" },
  { key: "postings_closed", label: "Postings closed" },
];

/** What a full run's intelligence stages filed from the pages and documents it read; an ingest-only run has no such stage. */
const FULL_RUN_STATS: { key: string; label: string }[] = [
  { key: "page_changes", label: "Page changes filed" },
  { key: "facts_extracted", label: "Facts extracted" },
];

function stat(run: RunOut, key: string): number | null {
  const v = run.stats[key];
  return typeof v === "number" ? v : null;
}

/** "3h ago" reads inside a sentence; "Yesterday" and dates need a preposition. */
function since(iso: string, now: number) {
  const rel = formatRelative(iso, now);
  if (rel.endsWith("ago") || rel === "just now") return rel;
  if (rel === "Yesterday") return "yesterday";
  return `on ${rel}`;
}

function lead(runs: RunOut[], now: number): string {
  const last = runs[0];
  if (!last) return "Each ingestion run and what it collected.";
  if (isRunActive(last)) return `A run is in progress: it started ${since(last.started_at, now)}.`;
  if (isRunInterrupted(last))
    return `The last run started ${since(last.started_at, now)} and stopped before it finished; the next run marks it as failed.`;
  const when = since(last.finished_at ?? last.started_at, now);
  if (last.status === "failed") return `The last run failed ${when}.${last.error ? " Its error is below." : ""}`;
  const passed = stat(last, "prefilter_passed");
  const rejected = stat(last, "prefilter_rejected");
  if (passed === null || rejected === null) return `The last run finished ${when}.`;
  // Duplicates are dropped before the prefilter, so its base is what it screened.
  return `The last run finished ${when}: ${formatCount(passed)} of ${formatCount(passed + rejected)} screened documents passed the prefilter.`;
}

function RunStatus({ run }: { run: RunOut }) {
  return <RunStatusBadge state={isRunActive(run) ? "running" : isRunInterrupted(run) ? "interrupted" : run.status} />;
}

function Duration({ run, now }: { run: RunOut; now: number }) {
  const started = Date.parse(run.started_at);
  if (run.finished_at) return <>{formatDuration(Date.parse(run.finished_at) - started)}</>;
  if (isRunActive(run)) {
    return (
      <span>
        {formatDuration(now - started)}
        <span className="text-muted-foreground"> so far</span>
      </span>
    );
  }
  return <span className="text-muted-foreground">—</span>;
}

const STATS_GRID = "grid grid-cols-2 gap-x-8 gap-y-2.5 sm:grid-cols-3 lg:grid-cols-5";

/** A row of counts; one the run did not write reads "Not recorded". */
function Stats({ run, stats, className }: { run: RunOut; stats: { key: string; label: string }[]; className?: string }) {
  return (
    <dl className={cn(STATS_GRID, className)}>
      {stats.map(({ key, label }) => {
        const value = stat(run, key);
        return (
          <div key={key} className="flex flex-col gap-0.5">
            <dt className="text-meta text-muted-foreground">{label}</dt>
            <dd className={cn("text-body font-medium", value === null && "font-normal text-muted-foreground")}>
              {value === null ? "Not recorded" : <Num value={value} />}
            </dd>
          </div>
        );
      })}
    </dl>
  );
}

function RunDetails({ run }: { run: RunOut }) {
  const passed = stat(run, "prefilter_passed");
  const rejected = stat(run, "prefilter_rejected");
  const passRate = prefilterPassRate(run.stats);
  const failed = stat(run, "sources_failed");
  return (
    <div className="flex flex-col gap-4 py-2">
      {isRunInterrupted(run) ? (
        <p className="text-meta text-muted-foreground">
          The worker stopped before this run finished, so it recorded no counts. The next run marks it as failed.
        </p>
      ) : null}
      {run.error ? (
        <div className="flex flex-col gap-1.5">
          <p className="text-meta font-medium text-threat">What went wrong</p>
          <pre className="max-h-48 overflow-auto rounded-md border bg-card px-3 py-2.5 font-mono text-meta leading-relaxed break-words whitespace-pre-wrap text-foreground">
            {run.error}
          </pre>
          <p className="text-meta text-muted-foreground">
            Counts below are whatever the run recorded before it stopped. Fix the cause, then start a new run.
          </p>
        </div>
      ) : null}
      <Stats run={run} stats={DETAIL_STATS} />
      {/* The job boards' and the intelligence stages' counts on a row of their own, so they read together. */}
      <Stats run={run} stats={run.mode === "full" ? [...BOARD_STATS, ...FULL_RUN_STATS] : BOARD_STATS} className="border-t pt-3" />
      <p className="text-meta text-muted-foreground">
        Run {run.id} · started {formatAbsolute(run.started_at)}
        {run.finished_at ? ` · finished ${formatAbsolute(run.finished_at)}` : ""}
        {passRate !== null && passed !== null && rejected !== null
          ? ` · the prefilter passed ${formatPercent(passRate)} of the ${formatCount(passed + rejected)} documents it screened`
          : ""}
        {failed ? ` · ${formatCount(failed)} ${failed === 1 ? "source" : "sources"} failed; see Sources for their errors` : ""}
        {/* A full run's graph is traced as an agent run: its steps, tokens and cost (agent_run_id, D30). */}
        {run.agent_run_id !== null ? (
          <>
            {" · "}
            <Link
              to={`/admin/agents/${run.agent_run_id}`}
              className="rounded-xs font-medium text-primary-ink decoration-1 underline-offset-[0.2em] hover:underline focus-visible:ring-2 focus-visible:ring-ring focus-visible:outline-none"
            >
              Its agent run
            </Link>
          </>
        ) : null}
      </p>
    </div>
  );
}

function RunRow({ run, now, open, onToggle }: { run: RunOut; now: number; open: boolean; onToggle: () => void }) {
  const detailsId = useId();
  return (
    <>
      <TableRow data-state={open ? "selected" : undefined} className={cn(open && "border-b-0")}>
        <TableCell className="pr-0">
          <Button
            variant="ghost"
            size="icon-xs"
            aria-expanded={open}
            aria-controls={detailsId}
            aria-label={`Details for run ${run.id}`}
            onClick={onToggle}
            className="text-muted-foreground hover:text-foreground"
          >
            <ChevronRightIcon className={cn("transition-transform duration-200 ease-out-quart", open && "rotate-90")} />
          </Button>
        </TableCell>
        <TableCell>
          <RunStatus run={run} />
        </TableCell>
        <TableCell className="text-muted-foreground">{triggerLabel(run.trigger)}</TableCell>
        <TableCell>
          <Tooltip>
            <TooltipTrigger asChild>
              <time dateTime={run.started_at}>{formatMoment(run.started_at, now)}</time>
            </TooltipTrigger>
            <TooltipContent>{formatAbsolute(run.started_at)}</TooltipContent>
          </Tooltip>
        </TableCell>
        <TableCell>
          <Duration run={run} now={now} />
        </TableCell>
        <TableCell className="py-2">
          {isRunActive(run) && Object.keys(run.stats).length === 0 ? (
            <span className="text-meta text-muted-foreground">Counts arrive when the run finishes.</span>
          ) : (
            <FunnelBar stats={run.stats} />
          )}
        </TableCell>
      </TableRow>
      {open ? (
        <TableRow id={detailsId} data-state="selected" className="hover:bg-accent">
          <TableCell colSpan={6} className="px-3 pt-0 pb-3 pl-12 whitespace-normal">
            <RunDetails run={run} />
          </TableCell>
        </TableRow>
      ) : null}
    </>
  );
}

function QueuedRow() {
  return (
    <TableRow className="hover:bg-transparent">
      <TableCell />
      <TableCell>
        <span className="inline-flex h-5 items-center gap-1.5 rounded-sm bg-secondary px-1.5 text-meta font-medium text-secondary-foreground">
          Queued
        </span>
      </TableCell>
      <TableCell className="text-muted-foreground">Manual</TableCell>
      <TableCell colSpan={2} className="text-meta text-muted-foreground">
        Waiting for a worker to pick it up…
      </TableCell>
      <TableCell className="py-2">
        <Skeleton className="h-2 w-full rounded-[4px]" />
      </TableCell>
    </TableRow>
  );
}

function RunsSkeleton() {
  return (
    <div role="status" aria-label="Loading runs" className="flex flex-col">
      {Array.from({ length: 6 }, (_, i) => (
        <div key={i} className="flex items-center gap-8 border-b py-3.5">
          <Skeleton className="h-5 w-20" />
          <Skeleton className="h-4 w-16" />
          <Skeleton className="h-4 w-24" />
          <Skeleton className="h-4 w-12" />
          <Skeleton className="ml-auto h-2 w-64 rounded-[4px]" />
        </div>
      ))}
    </div>
  );
}

export function RunsPage() {
  const { user } = useAuth();
  const canRun = user?.role === "analyst";
  const now = useNow(1000);
  const [queuedAt, setQueuedAt] = useState<number | null>(null);
  const [toggled, setToggled] = useState<Map<number, boolean>>(() => new Map());

  const runs = useRuns({ queuedAt });
  const list = runs.data ?? [];
  // A queued run shows as a placeholder row until the worker records it (or a short wait passes).
  const waiting = queuedAt !== null && !isRecorded(runs.data, queuedAt) && now - queuedAt < AWAIT_START_MS;

  const start = useStartRun();
  const active = list.find(isRunActive);
  const nextAt = useNextManualRunAt(lastSucceededFinish(runs.data), canRun);
  const nextManualAt = nextAt !== null && nextAt > now ? nextAt : null;

  const isOpen = (run: RunOut, index: number) => toggled.get(run.id) ?? (index === 0 && run.status === "failed");
  const toggle = (run: RunOut, index: number) =>
    setToggled((prev) => new Map(prev).set(run.id, !isOpen(run, index)));

  const action = canRun ? (
    active ? (
      <Button loading>Running…</Button>
    ) : (
      <Button
        loading={start.isPending}
        onClick={() =>
          start.mutate(undefined, {
            onSuccess: () => setQueuedAt(Date.now()),
          })
        }
      >
        {start.isPending ? null : <PlayIcon />}
        Run now
      </Button>
    )
  ) : null;

  let body;
  if (runs.isPending) {
    body = <RunsSkeleton />;
  } else if (runs.isError) {
    body = (
      <EmptyState
        tone="error"
        title="Runs didn't load."
        hint="FrogEye's API didn't answer. Check your connection, then try again."
        action={
          <Button variant="outline" size="sm" onClick={() => runs.refetch()} loading={runs.isRefetching}>
            <RotateCwIcon />
            Try again
          </Button>
        }
      />
    );
  } else if (list.length === 0 && !waiting) {
    body = (
      <EmptyState
        title="No runs yet."
        hint={
          <>
            Ingestion runs every morning at 06:00, Israel time; each run is listed here with what it fetched and
            what survived the prefilter. {canRun ? "Start the first one now with Run now." : "A CI analyst can also start one by hand."}
          </>
        }
      />
    );
  } else {
    body = (
      <Table className="min-w-[48rem] table-fixed">
        <colgroup>
          <col className="w-10" />
          <col className="w-28" />
          <col className="w-28" />
          <col className="w-40" />
          <col className="w-28" />
          <col />
        </colgroup>
        <TableHeader>
          <TableRow className="hover:bg-transparent">
            <TableHead className="pr-0">
              <span className="sr-only">Details</span>
            </TableHead>
            <TableHead>Status</TableHead>
            <TableHead>Trigger</TableHead>
            <TableHead>Started</TableHead>
            <TableHead>Duration</TableHead>
            <TableHead>
              <span className="sr-only">Funnel: </span>
              <FunnelLegend />
            </TableHead>
          </TableRow>
        </TableHeader>
        <TableBody>
          {waiting ? <QueuedRow /> : null}
          {list.map((run, index) => (
            <RunRow key={run.id} run={run} now={now} open={isOpen(run, index)} onToggle={() => toggle(run, index)} />
          ))}
        </TableBody>
      </Table>
    );
  }

  return (
    <>
      <PageHeader
        title="Runs"
        description={
          runs.data ? (
            <>
              {lead(list, now)}
              {canRun && nextManualAt !== null ? ` Next manual run available at ${formatClock(nextManualAt)}.` : ""}
            </>
          ) : (
            "Each ingestion run and what it collected."
          )
        }
        actions={action}
      />
      {body}
    </>
  );
}
