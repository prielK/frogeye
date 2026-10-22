import { ArrowLeftIcon, ArrowRightIcon, ChevronRightIcon, CircleAlertIcon, GaugeIcon, KeyRoundIcon, RotateCwIcon, type LucideIcon } from "lucide-react";
import { Fragment, useMemo, type ReactNode } from "react";
import { Link, useParams } from "react-router";
import { EmptyState } from "@/components/common/EmptyState";
import { Num } from "@/components/common/Num";
import { PageHeader } from "@/components/layout/PageHeader";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";
import { ApiError } from "@/lib/api/client";
import { useAuth } from "@/lib/auth";
import { formatCompact, formatCount, formatUsd } from "@/lib/format";
import { formatAbsolute, formatClock, formatDuration, formatLongDay, plainDay, useNow } from "@/lib/time";
import { cn } from "@/lib/utils";
import { RunStatusBadge } from "./RunStatusBadge";
import { StepTimeline } from "./StepTimeline";
import {
  briefingOutcome,
  fullAnalyses,
  funnelCount,
  graphLabel,
  parseNotes,
  useAgentRun,
  useAgentSteps,
  type AgentRunOut,
  type AgentStepOut,
} from "./useAgentRuns";
import { triggerLabel } from "./useRuns";

const LINK =
  "rounded-xs font-medium text-primary-ink underline-offset-4 hover:underline focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 focus-visible:ring-offset-background focus-visible:outline-none";

type Totals = { cost: number; tokensIn: number; tokensOut: number; cached: number };

function totals(run: AgentRunOut, steps: AgentStepOut[] | undefined): Totals {
  // The run row gets its totals when it finishes; until then, sum what the steps cost so far.
  if (run.status !== "running" || !steps) return { cost: run.cost_usd, tokensIn: run.tokens_in, tokensOut: run.tokens_out, cached: sum(steps, "cached_tokens") };
  return { cost: sum(steps, "cost_usd"), tokensIn: sum(steps, "tokens_in"), tokensOut: sum(steps, "tokens_out"), cached: sum(steps, "cached_tokens") };
}

function sum(steps: AgentStepOut[] | undefined, key: "cost_usd" | "tokens_in" | "tokens_out" | "cached_tokens"): number {
  return (steps ?? []).reduce((total, s) => total + s[key], 0);
}

function lead(run: AgentRunOut, t: Totals, steps: number, now: number, isAnalyst: boolean): string {
  const started = Date.parse(run.started_at);
  const took = formatDuration((run.finished_at ? Date.parse(run.finished_at) : now) - started);
  const notes = parseNotes(run.notes);
  if (run.status === "running") return `In progress for ${took}: ${formatCount(steps)} ${steps === 1 ? "step" : "steps"} so far, ${formatUsd(t.cost, { precise: true })} spent.`;
  if (run.status === "interrupted")
    return `The worker stopped after ${took}, before the run finished. ${isAnalyst ? "Resume it within 6 hours with python -m app.cli.run_daily --resume." : "A CI analyst can resume it within 6 hours."}`;
  if (run.status === "failed") return `Failed after ${took}. What went wrong is below; the next run starts fresh.`;
  const fetched = funnelCount(run, "fetched");
  if (notes.aiOff) return `Ran for ${took} without AI analysis: it collected${fetched !== null ? ` ${formatCount(fetched)} items` : " its sources"} and made no AI calls.`;
  const relevant = funnelCount(run, "relevant");
  const fresh = funnelCount(run, "signals_new");
  const analysed = funnelCount(run, "signals_analyzed");
  const deep = funnelCount(run, "signals_deep_analyzed") || null; // said only when it wrote some
  const filed = fresh !== null ? `filed ${formatCount(fresh)} new ${fresh === 1 ? "signal" : "signals"}` : null;
  const wrote = deep !== null ? `wrote ${fullAnalyses(deep)}` : null;
  const found = [
    fetched !== null ? `read ${formatCount(fetched)} items` : null,
    relevant !== null ? `judged ${formatCount(relevant)} relevant` : null,
    // what the money went on, including carried-over signals analysed this run
    filed && analysed !== null
      ? wrote
        ? `${filed}, analysed ${formatCount(analysed)} and ${wrote}`
        : `${filed} and analysed ${formatCount(analysed)}`
      : filed,
  ].filter(Boolean);
  return `Ran for ${took} and spent ${formatUsd(t.cost, { precise: true })}${found.length ? `: it ${found.join(", ")}` : ""}.`;
}

/** The collection stages: each narrows the one before. */
const FUNNEL = [
  { key: "fetched", label: (n: number) => `${n === 1 ? "item" : "items"} read` },
  { key: "documents_created", label: (n: number) => `new ${n === 1 ? "document" : "documents"}` },
  { key: "prefilter_passed", label: () => "passed the keyword screen" },
  { key: "relevant", label: () => "relevant" },
  { key: "signals_new", label: (n: number) => `new ${n === 1 ? "signal" : "signals"}` },
];
/** What the run did with signals, new or carried over: not a narrower stage, so no chevron. */
const WORK = [
  { key: "signals_analyzed", label: () => "analysed" },
  { key: "signals_deep_analyzed", label: (n: number) => `full ${n === 1 ? "analysis" : "analyses"}`, hideZero: true },
  { key: "signals_breaking", label: () => "breaking" },
];

/** What the run found, stage by stage, as one line of text (the stages span orders of magnitude, so no bar). */
function FunnelLine({ run }: { run: AgentRunOut }) {
  const counted = (list: { key: string; label: (n: number) => string; hideZero?: boolean }[]) =>
    list.flatMap((s) => {
      const n = funnelCount(run, s.key);
      return n === null || (s.hideZero && n === 0) ? [] : [{ key: s.key, n, label: s.label(n) }];
    });
  const stages = counted(FUNNEL);
  const work = counted(WORK);
  if (!stages.length) return null;
  const item = (s: { n: number; label: string }, dot: boolean) => (
    <span className="whitespace-nowrap">
      <Num value={s.n} className="font-semibold text-foreground" /> {s.label}
      {dot ? (
        <>
          <span className="sr-only">; </span>
          <span aria-hidden className="ml-1.5 text-muted-foreground/70">
            ·
          </span>
        </>
      ) : null}
    </span>
  );
  return (
    <p className="flex flex-wrap items-center gap-x-1.5 gap-y-1 text-meta text-muted-foreground">
      {stages.map((s, i) => (
        <Fragment key={s.key}>
          {i > 0 ? (
            <>
              <span className="sr-only">, </span>
              <ChevronRightIcon aria-hidden className="size-3 shrink-0 text-muted-foreground/70" />
            </>
          ) : null}
          {item(s, i === stages.length - 1 && work.length > 0)}
        </Fragment>
      ))}
      {/* A dot ends the item before it, so a wrapped line never starts with one. */}
      {work.map((s, i) => (
        <Fragment key={s.key}>{item(s, i < work.length - 1)}</Fragment>
      ))}
    </p>
  );
}

function Fact({ term, children }: { term: string; children: ReactNode }) {
  return (
    <div className="flex min-w-0 flex-col gap-0.5">
      <dt className="text-meta text-muted-foreground">{term}</dt>
      <dd className="text-body font-medium">{children}</dd>
    </div>
  );
}

function Note({ icon: Icon, tone, title, children }: { icon: LucideIcon; tone: "warning" | "threat" | "muted"; title: string; children: ReactNode }) {
  return (
    <div className="flex max-w-[72ch] gap-2.5">
      <Icon aria-hidden className={cn("mt-0.5 size-4 shrink-0", tone === "warning" ? "text-warning" : tone === "threat" ? "text-threat" : "text-muted-foreground")} />
      <div className="flex min-w-0 flex-col gap-1">
        <p className={`text-body font-medium ${tone === "threat" ? "text-threat" : "text-foreground"}`}>{title}</p>
        <div className="text-meta text-muted-foreground">{children}</div>
      </div>
    </div>
  );
}

function RunNotes({ run, isAnalyst }: { run: AgentRunOut; isAnalyst: boolean }) {
  const notes = parseNotes(run.notes);
  if (!notes.aiOff && !notes.budgetStopped && !notes.errors.length && !notes.errorsHidden && !run.error) return null;
  return (
    <div className="flex flex-col gap-4 pb-8">
      {run.error ? (
        <Note icon={CircleAlertIcon} tone="threat" title="What went wrong">
          <pre className="mt-1 max-h-48 overflow-auto rounded-md border bg-card px-3 py-2.5 font-mono text-meta leading-relaxed break-words whitespace-pre-wrap text-foreground">
            {run.error}
          </pre>
          {isAnalyst ? null : <p className="mt-1.5">CI analysts see the full error.</p>}
        </Note>
      ) : null}
      {notes.aiOff ? (
        <Note icon={KeyRoundIcon} tone="muted" title="AI analysis was off">
          No OPENAI_API_KEY was set, so the run collected sources and stopped there: no triage, signals or briefing.
        </Note>
      ) : null}
      {notes.budgetStopped ? (
        <Note icon={GaugeIcon} tone="warning" title="Stopped at the budget cap">
          The AI budget ran out mid-run. Documents it didn't reach stay queued for the next run; nothing was lost.
        </Note>
      ) : null}
      {notes.errors.length ? (
        <Note icon={CircleAlertIcon} tone="threat" title={notes.errors.length === 1 ? "One stage reported an error" : `${notes.errors.length} stages reported errors`}>
          <ul className="mt-1 flex flex-col gap-1">
            {notes.errors.map((e, i) => (
              <li key={i} className="font-mono text-meta break-words text-foreground">
                {e}
              </li>
            ))}
          </ul>
          <p className="mt-1.5">The run carried on past them; the failed steps are marked below.</p>
        </Note>
      ) : null}
      {notes.errorsHidden ? (
        <Note icon={CircleAlertIcon} tone="threat" title="Some stages reported errors">
          The run carried on past them, and the failed steps are marked below. CI analysts can see each error: they can quote model output.
        </Note>
      ) : null}
    </div>
  );
}

function BackLink() {
  return (
    <Link
      to="/admin/agents"
      className="mb-4 inline-flex w-fit items-center gap-1.5 rounded-xs text-meta text-muted-foreground underline-offset-4 hover:text-foreground hover:underline focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 focus-visible:ring-offset-background focus-visible:outline-none"
    >
      <ArrowLeftIcon aria-hidden className="size-3.5" />
      Agent runs
    </Link>
  );
}

function DetailSkeleton() {
  return (
    <div role="status" aria-label="Loading the run" className="flex flex-col gap-6">
      <Skeleton className="h-9 w-80" />
      <Skeleton className="h-5 w-[32rem] max-w-full" />
      <div className="flex gap-10 border-y py-4">
        {Array.from({ length: 5 }, (_, i) => (
          <Skeleton key={i} className="h-9 w-24" />
        ))}
      </div>
      {Array.from({ length: 5 }, (_, i) => (
        <Skeleton key={i} className="h-11 w-full" />
      ))}
    </div>
  );
}

export function AgentRunDetail() {
  const id = Number(useParams().id);
  const { user } = useAuth();
  const isAnalyst = user?.role === "analyst";
  const run = useAgentRun(id);
  const steps = useAgentSteps(id, run.data?.status);
  const now = useNow(1000);
  const t = useMemo(() => (run.data ? totals(run.data, steps.data) : null), [run.data, steps.data]);

  if (!Number.isInteger(id) || id <= 0 || (run.isError && run.error instanceof ApiError && run.error.status === 404)) {
    return (
      <>
        <BackLink />
        <PageHeader title="Run not found" description="No agent run has this number. It may be a typo in the address." />
        <Button asChild variant="outline" size="sm">
          <Link to="/admin/agents">See every run</Link>
        </Button>
      </>
    );
  }
  if (run.isPending) {
    return (
      <>
        <BackLink />
        <DetailSkeleton />
      </>
    );
  }
  if (run.isError) {
    return (
      <>
        <BackLink />
        <EmptyState
          tone="error"
          title="This run didn't load."
          hint="FrogEye's API didn't answer. Check your connection, then try again."
          action={
            <Button variant="outline" size="sm" onClick={() => run.refetch()} loading={run.isRefetching}>
              <RotateCwIcon />
              Try again
            </Button>
          }
        />
      </>
    );
  }

  const r = run.data;
  const totalsNow = t!;
  const live = r.status === "running";
  const started = Date.parse(r.started_at);
  const outcome = briefingOutcome(r);
  const notes = parseNotes(r.notes);
  const count = steps.data?.length ?? 0;
  const empty = notes.aiOff
    ? "No AI steps: AI analysis was off for this run, so it only collected sources."
    : live
      ? "No steps yet. The first ones appear once collection finishes and the agents start reading."
      : "This run recorded no steps.";

  return (
    <>
      <BackLink />
      <PageHeader
        title={`${graphLabel(r.graph)}, ${formatLongDay(r.started_at)}`}
        description={lead(r, totalsNow, count, now, isAnalyst)}
        actions={
          r.briefing_date ? (
            <Button asChild variant="outline">
              <Link to={`/briefing/${r.briefing_date}`}>
                Open the briefing
                <ArrowRightIcon />
              </Link>
            </Button>
          ) : null
        }
      />
      <div className="flex flex-col gap-3 border-y py-4">
        <dl className="grid grid-cols-2 gap-x-8 gap-y-3 sm:grid-cols-3 lg:grid-cols-6">
          <Fact term="Status">
            <RunStatusBadge state={r.status} />
          </Fact>
          <Fact term="Started">
            <Tooltip>
              <TooltipTrigger asChild>
                <time dateTime={r.started_at}>{formatClock(started)}</time>
              </TooltipTrigger>
              <TooltipContent>{formatAbsolute(r.started_at)}</TooltipContent>
            </Tooltip>
            <span className="font-normal text-muted-foreground"> · {triggerLabel(r.trigger)}</span>
          </Fact>
          <Fact term="Duration">
            {formatDuration((r.finished_at ? Date.parse(r.finished_at) : now) - started)}
            {live ? <span className="font-normal text-muted-foreground"> so far</span> : null}
          </Fact>
          <Fact term="Tokens in / out">
            <Tooltip>
              <TooltipTrigger asChild>
                <span className="cursor-default">
                  <Num value={totalsNow.tokensIn} format={formatCompact} /> / <Num value={totalsNow.tokensOut} format={formatCompact} />
                </span>
              </TooltipTrigger>
              <TooltipContent>
                {formatCount(totalsNow.tokensIn)} in{totalsNow.cached ? ` (${formatCount(totalsNow.cached)} from the prompt cache)` : ""}, {formatCount(totalsNow.tokensOut)} out
              </TooltipContent>
            </Tooltip>
          </Fact>
          <Fact term="Cost">
            <Num value={totalsNow.cost} format={(n) => formatUsd(n, { precise: true })} />
            {live ? <span className="font-normal text-muted-foreground"> so far</span> : null}
          </Fact>
          <Fact term="Briefing">
            {r.briefing_date && outcome ? (
              <Link to={`/briefing/${r.briefing_date}`} className={LINK}>
                {outcome} · {plainDay(r.briefing_date, "short")}
              </Link>
            ) : (
              <span className="font-normal text-muted-foreground">{live ? "Not filed yet" : "None filed"}</span>
            )}
          </Fact>
        </dl>
        <FunnelLine run={r} />
      </div>
      <div className="pt-8">
        <RunNotes run={r} isAnalyst={isAnalyst} />
        {steps.isPending ? (
          <div role="status" aria-label="Loading steps" className="flex flex-col gap-2">
            {Array.from({ length: 5 }, (_, i) => (
              <Skeleton key={i} className="h-11 w-full" />
            ))}
          </div>
        ) : steps.isError ? (
          <EmptyState
            tone="error"
            title="The steps didn't load."
            hint="FrogEye's API didn't answer. Check your connection, then try again."
            action={
              <Button variant="outline" size="sm" onClick={() => steps.refetch()} loading={steps.isRefetching}>
                <RotateCwIcon />
                Try again
              </Button>
            }
          />
        ) : (
          <StepTimeline key={r.id} steps={steps.data} live={live} showArgs={isAnalyst} fresh={steps.fresh} empty={empty} />
        )}
        {r.pipeline_run_id ? (
          <p className="pt-6 text-meta text-muted-foreground">
            Run {r.id} · its source collection is ingestion run {r.pipeline_run_id} on{" "}
            <Link to="/admin/runs" className={LINK}>
              Runs
            </Link>
            .
          </p>
        ) : null}
      </div>
    </>
  );
}
