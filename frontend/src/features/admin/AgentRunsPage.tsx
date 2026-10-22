import { ChevronRightIcon, RotateCwIcon } from "lucide-react";
import { Fragment, useId } from "react";
import { Link } from "react-router";
import { EmptyState } from "@/components/common/EmptyState";
import { Num } from "@/components/common/Num";
import { PageHeader } from "@/components/layout/PageHeader";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { useAuth } from "@/lib/auth";
import { formatCompact, formatCount, formatUsd } from "@/lib/format";
import { formatAbsolute, formatDuration, formatMoment, formatRelative, plainDay, useNow } from "@/lib/time";
import { cn } from "@/lib/utils";
import { CostPanel } from "./CostPanel";
import { RunStatusBadge } from "./RunStatusBadge";
import { SectionHead } from "@/components/layout/SectionHead";
import { briefingOutcome, fullAnalyses, funnelCount, graphName, parseNotes, RUNS_LIMIT, useAgentRuns, type AgentRunOut } from "./useAgentRuns";
import { budgetState, useCosts } from "./useCosts";
import { triggerLabel } from "./useRuns";

/*
 * Agent runs: what the daily agents did and what it cost, for every reader (PRODUCT.md: trust
 * is earned on screen). Structure: the lead sentence answers "did this morning's run go well,
 * and are we near a cap?"; then the runs, one table row each (a row opens its step trace);
 * then the spend. No stat tiles, no chart wall: one meter per cap and one week chart.
 */

/** Run costs in a column: three places below a dollar, so cents and fractions line up. */
const runCost = (n: number) => formatUsd(n, { decimals: 3 });

const LINK =
  "rounded-xs font-medium text-primary-ink underline-offset-4 hover:underline focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 focus-visible:ring-offset-background focus-visible:outline-none";

function since(iso: string, now: number) {
  const rel = formatRelative(iso, now);
  if (rel.endsWith("ago") || rel === "just now") return rel;
  return rel === "Yesterday" ? "yesterday" : `on ${rel}`;
}

function runLead(run: AgentRunOut | undefined, now: number): string {
  if (!run) return "Each daily agent run, step by step, with its tokens and cost.";
  if (run.status === "running") return `A run is in progress: it started ${since(run.started_at, now)}. Open it to follow its steps live.`;
  const when = since(run.finished_at ?? run.started_at, now);
  if (run.status === "failed") return `The last run failed ${when}; its trace shows where.`;
  if (run.status === "interrupted") return `The last run stopped ${when} before it finished; it can be resumed for 6 hours.`;
  const notes = parseNotes(run.notes);
  if (notes.aiOff) return `The last run finished ${when} without AI analysis: no OPENAI_API_KEY is set, so it only collected sources.`;
  const fresh = funnelCount(run, "signals_new");
  const analysed = funnelCount(run, "signals_analyzed");
  const deep = funnelCount(run, "signals_deep_analyzed");
  const took = run.finished_at ? ` in ${formatDuration(Date.parse(run.finished_at) - Date.parse(run.started_at))}` : "";
  const what = [
    fresh !== null ? `${formatCount(fresh)} new ${fresh === 1 ? "signal" : "signals"}` : null,
    analysed !== null ? `${formatCount(analysed)} analysed` : null,
    deep ? fullAnalyses(deep) : null, // stage B's work, when it wrote some
  ].filter(Boolean);
  return `The last run finished ${when}${took}, spending ${formatUsd(run.cost_usd, { precise: true })}${what.length ? `: ${what.join(", ")}` : ""}.`;
}

function Flags({ run }: { run: AgentRunOut }) {
  const notes = parseNotes(run.notes);
  const flags = [
    notes.aiOff ? "AI off" : null,
    notes.budgetStopped ? "Stopped at the cap" : null,
    notes.errors.length ? `${notes.errors.length} ${notes.errors.length === 1 ? "stage error" : "stage errors"}` : null,
    notes.errorsHidden ? "Stage errors" : null,
  ].filter(Boolean);
  if (!flags.length) return null;
  return <span className="text-meta text-muted-foreground">{flags.join(" · ")}</span>;
}

/**
 * What the run found, as text: "412 read › 61 relevant › 14 new signals · 9 analysed" (stages
 * span orders of magnitude, so no bar). Analysed is work, not a narrower stage: the
 * cap may leave new signals for the next run, which then analyses them with its own.
 */
function Found({ run }: { run: AgentRunOut }) {
  const stages = [
    { key: "fetched", after: "read" },
    { key: "relevant", after: "relevant" },
    { key: "signals_new", after: "new signals" },
  ].flatMap((s) => {
    const n = funnelCount(run, s.key);
    return n === null ? [] : [{ ...s, n, after: s.key === "signals_new" && n === 1 ? "new signal" : s.after }];
  });
  if (!stages.length) {
    return <span className="text-meta text-muted-foreground">{run.status === "running" ? "Counts arrive as stages finish" : "Not recorded"}</span>;
  }
  const aiOff = parseNotes(run.notes).aiOff;
  if (aiOff) stages.splice(1);
  const analysed = aiOff ? null : funnelCount(run, "signals_analyzed");
  const deep = aiOff ? null : funnelCount(run, "signals_deep_analyzed") || null;
  return (
    <span className="flex flex-wrap items-center gap-x-1.5 text-meta text-muted-foreground">
      {stages.map((s, i) => (
        <Fragment key={s.key}>
          {i > 0 ? <ChevronRightIcon aria-hidden className="size-3 shrink-0 text-muted-foreground/70" /> : null}
          <span className="whitespace-nowrap">
            {i > 0 ? <span className="sr-only">, </span> : null}
            <Num value={s.n} className="font-semibold text-foreground" /> {s.after}
            {/* The dot ends this item, so a wrapped line never starts with one. */}
            {i === stages.length - 1 && analysed !== null ? (
              <>
                <span className="sr-only">; </span>
                <span aria-hidden className="ml-1.5 text-muted-foreground/70">
                  ·
                </span>
              </>
            ) : null}
          </span>
        </Fragment>
      ))}
      {analysed !== null ? (
        <span className="whitespace-nowrap">
          <Num value={analysed} className="font-semibold text-foreground" /> analysed
          {deep !== null ? (
            <>
              <span className="sr-only">; </span>
              <span aria-hidden className="ml-1.5 text-muted-foreground/70">
                ·
              </span>
            </>
          ) : null}
        </span>
      ) : null}
      {deep !== null ? (
        <span className="whitespace-nowrap">
          <Num value={deep} className="font-semibold text-foreground" /> full {deep === 1 ? "analysis" : "analyses"}
        </span>
      ) : null}
    </span>
  );
}

function Briefing({ run }: { run: AgentRunOut }) {
  const outcome = briefingOutcome(run);
  if (run.briefing_date && outcome) {
    return (
      <span className="flex flex-col gap-0.5">
        {/* `relative` lifts it above the row's stretched link; a static class string, so no size or colour is merged away. */}
        <Link to={`/briefing/${run.briefing_date}`} className={`${LINK} relative w-fit text-meta`}>
          {outcome}
        </Link>
        <span className="text-meta text-muted-foreground">{plainDay(run.briefing_date, "short")}</span>
      </span>
    );
  }
  return <span className="text-meta text-muted-foreground">{run.status === "running" ? "Not yet" : "None"}</span>;
}

function RunRow({ run, now }: { run: AgentRunOut; now: number }) {
  const live = run.status === "running";
  const started = Date.parse(run.started_at);
  const dash = (
    <span className="text-muted-foreground">
      <span aria-hidden>—</span>
      <span className="sr-only">Totals arrive when the run finishes</span>
    </span>
  );
  return (
    <TableRow className="group relative">
      <TableCell>
        <div className="flex flex-col gap-0.5">
          {/* The link's ::after covers the row, so the whole row opens the run; the briefing link sits above it. */}
          <Link
            to={`/admin/agents/${run.id}`}
            className={cn(LINK, "w-fit text-foreground after:absolute after:inset-0 group-hover:underline")}
            aria-label={`Run ${run.id}, started ${formatAbsolute(run.started_at)}`}
          >
            <time dateTime={run.started_at}>{formatMoment(run.started_at, now)}</time>
          </Link>
          <span className="text-meta text-muted-foreground">{triggerLabel(run.trigger)}</span>
        </div>
      </TableCell>
      <TableCell className="text-meta text-muted-foreground">{graphName(run.graph)}</TableCell>
      <TableCell>
        <div className="flex flex-col gap-0.5">
          <RunStatusBadge state={run.status} />
          <Flags run={run} />
        </div>
      </TableCell>
      <TableCell>
        {run.finished_at ? (
          formatDuration(Date.parse(run.finished_at) - started)
        ) : live ? (
          <span>
            {formatDuration(now - started)}
            <span className="text-muted-foreground"> so far</span>
          </span>
        ) : (
          dash
        )}
      </TableCell>
      <TableCell>
        {live ? (
          dash
        ) : run.tokens_in + run.tokens_out === 0 ? (
          <span className="text-muted-foreground">No AI calls</span>
        ) : (
          <span>
            <Num value={run.tokens_in} format={formatCompact} />
            <span className="text-muted-foreground">
              {" / "}
              <Num value={run.tokens_out} format={formatCompact} />
            </span>
          </span>
        )}
      </TableCell>
      <TableCell className="font-medium">{live ? dash : <Num value={run.cost_usd} format={runCost} />}</TableCell>
      <TableCell className="whitespace-normal">
        <Found run={run} />
      </TableCell>
      <TableCell>
        <Briefing run={run} />
      </TableCell>
      <TableCell className="pr-1 pl-0">
        <ChevronRightIcon aria-hidden className="size-4 text-muted-foreground transition-transform duration-150 ease-out-quart group-hover:translate-x-0.5" />
      </TableCell>
    </TableRow>
  );
}

function RunsSkeleton() {
  return (
    <div role="status" aria-label="Loading agent runs" className="flex flex-col">
      {Array.from({ length: 5 }, (_, i) => (
        <div key={i} className="flex items-center gap-8 border-b py-4">
          <Skeleton className="h-9 w-24" />
          <Skeleton className="h-5 w-20" />
          <Skeleton className="h-4 w-14" />
          <Skeleton className="h-4 w-20" />
          <Skeleton className="ml-auto h-4 w-56" />
        </div>
      ))}
    </div>
  );
}

export function AgentRunsPage() {
  const headId = useId();
  const now = useNow(1000);
  const { user } = useAuth();
  const runs = useAgentRuns();
  const costs = useCosts();
  const list = runs.data ?? [];

  const budget = costs.data
    ? budgetState(costs.data.total_usd, costs.data.total_budget_usd) === "reached"
      ? " The total AI budget is spent: AI analysis stays off until the cap is raised."
      : budgetState(costs.data.today_usd, costs.data.daily_budget_usd) === "reached"
        ? " Today's AI budget is spent: AI analysis resumes after midnight, Israel time."
        : ""
    : "";

  let body;
  if (runs.isPending) {
    body = <RunsSkeleton />;
  } else if (runs.isError) {
    body = (
      <EmptyState
        tone="error"
        title="Agent runs didn't load."
        hint="FrogEye's API didn't answer. Check your connection, then try again."
        action={
          <Button variant="outline" size="sm" onClick={() => runs.refetch()} loading={runs.isRefetching}>
            <RotateCwIcon />
            Try again
          </Button>
        }
      />
    );
  } else if (list.length === 0) {
    body = (
      <EmptyState
        title="No agent runs yet."
        hint={
          <>
            The daily run starts at 06:00, Israel time. Each one is listed here with every step its agents took, the tokens
            they used and what it cost.{" "}
            {user?.role === "analyst" ? (
              <>
                Start one from{" "}
                <Link to="/admin/runs" className={LINK}>
                  Runs
                </Link>{" "}
                with Run now.
              </>
            ) : (
              "A CI analyst can also start one by hand."
            )}
          </>
        }
      />
    );
  } else {
    body = (
      <>
        {/*
          The fixed columns take about 55rem; the minimum width leaves "What it found" its 13rem
          (two lines), so a narrower window scrolls the table instead of crushing that column
          until its counts overprint the Briefing column.
        */}
        <Table className="min-w-[68rem] table-fixed">
          <colgroup>
            <col className="w-36" />
            <col className="w-32" />
            <col className="w-32" />
            <col className="w-28" />
            <col className="w-32" />
            <col className="w-20" />
            <col />
            <col className="w-32" />
            <col className="w-7" />
          </colgroup>
          <TableHeader>
            <TableRow className="hover:bg-transparent">
              <TableHead>Started</TableHead>
              <TableHead>Graph</TableHead>
              <TableHead>Status</TableHead>
              <TableHead>Duration</TableHead>
              <TableHead>Tokens in / out</TableHead>
              <TableHead>Cost</TableHead>
              <TableHead>What it found</TableHead>
              <TableHead>Briefing</TableHead>
              <TableHead className="pl-0">
                <span className="sr-only">Open</span>
              </TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {list.map((run) => (
              <RunRow key={run.id} run={run} now={now} />
            ))}
          </TableBody>
        </Table>
        {list.length >= RUNS_LIMIT ? <p className="pt-3 text-meta text-muted-foreground">Showing the {RUNS_LIMIT} latest runs.</p> : null}
      </>
    );
  }

  return (
    <>
      <PageHeader title="Agent runs" description={runs.data ? `${runLead(list[0], now)}${budget}` : "Each daily agent run, step by step, with its tokens and cost."} />
      <div className="flex flex-col gap-12">
        <section aria-labelledby={headId}>
          <SectionHead id={headId} title="Runs" meta="Newest first · open a run to see every step" />
          {body}
        </section>
        <CostPanel />
      </div>
    </>
  );
}
