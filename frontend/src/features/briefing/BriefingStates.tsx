import { ArrowRightIcon, CircleAlertIcon, GaugeIcon, KeyRoundIcon, PlayIcon, RotateCwIcon, type LucideIcon } from "lucide-react";
import { useId, type ReactNode } from "react";
import { Link } from "react-router";
import { EmptyState } from "@/components/common/EmptyState";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
// Accepted cross-feature import: the first-run state starts the same run as Admin › Runs.
import { useStartRun } from "@/features/admin/useRuns";
import { usd } from "@/lib/format";
import { plainDay } from "@/lib/time";
import { editionLabel, type Edition } from "@/lib/roles";
import { cn } from "@/lib/utils";
import {
  briefingHref,
  FOCUS_RING,
  lastFullBriefing,
  skippedCause,
  useBudgets,
  type BriefingOut,
  type BriefingSummary,
} from "./useBriefing";

/*
 * The Briefing's states other than a written briefing: quiet, skipped and failed days (shown
 * under the masthead), no briefing yet (with the AI-off variant), loading and errors. Each is
 * calm and says what happened and what to do about it.
 */

/** An explanation block on an ink rule: what happened, and what to do about it. */
function StateNote({ icon: Icon, title, children, action }: { icon: LucideIcon; title: string; children: ReactNode; action?: ReactNode }) {
  const id = useId();
  return (
    <section aria-labelledby={id} className="flex max-w-[62ch] flex-col gap-2.5 border-t border-foreground pt-5">
      <h3 id={id} className="flex items-center gap-2 text-body font-semibold">
        <Icon aria-hidden className="size-4 shrink-0 text-muted-foreground" />
        {title}
      </h3>
      <div className="flex flex-col gap-2.5 text-body text-muted-foreground">{children}</div>
      {action ? <div className="mt-1.5 flex flex-wrap items-center gap-2">{action}</div> : null}
    </section>
  );
}

function Code({ children }: { children: ReactNode }) {
  return <code className="rounded-xs bg-muted px-1 py-px text-meta text-foreground">{children}</code>;
}

/** The newest fully written briefing before this one: what a quiet, skipped or failed day points back to. */
export function LastFullBriefing({ date, list }: { date: string; list: BriefingSummary[] | undefined }) {
  const id = useId();
  const last = lastFullBriefing(list, date);
  if (!last) return null;
  return (
    <section aria-labelledby={id} className="flex max-w-[46rem] flex-col gap-2 border-t border-foreground pt-3">
      <h3 id={id} className="text-body font-semibold">
        The last full briefing
      </h3>
      <Link
        to={briefingHref(last.date)}
        className={cn(
          "group w-fit rounded-xs font-headline text-title text-balance decoration-1 underline-offset-[0.16em] hover:underline",
          FOCUS_RING,
        )}
      >
        {last.headline}
        <ArrowRightIcon
          aria-hidden
          className="ml-1.5 inline size-4 -translate-y-px text-muted-foreground transition-transform duration-150 ease-out-quart group-hover:translate-x-0.5"
        />
      </Link>
      <p className="text-meta text-muted-foreground">{plainDay(last.date, "dateline")}</p>
    </section>
  );
}

/**
 * A day an LLM budget cap stopped the editor: which cap (from /costs, see skippedCause), and
 * the lever that actually lifts it. The total cap can only be raised; `--max-usd` raises the
 * daily cap for one run and leaves the total in force (backend cli/run_daily.py).
 */
export function Skipped({ isAnalyst, isLatest }: { isAnalyst: boolean; isLatest: boolean }) {
  const budgets = useBudgets(true);
  const cause = skippedCause(budgets, isLatest);

  if (cause === "total" && budgets) {
    return (
      <StateNote icon={GaugeIcon} title="The total AI budget is spent">
        <p>
          FrogEye has spent its total AI budget: {usd.format(budgets.totalUsd)} of {usd.format(budgets.totalBudgetUsd)}. AI analysis
          is paused, so no signals are analysed and no briefing is written until the cap is raised. Collection keeps running, and the
          signals carry over, so nothing is lost.
        </p>
        <p>
          The total cap is the only lever: {isAnalyst ? "raise" : "a CI analyst can raise"} <Code>LLM_TOTAL_BUDGET_USD</Code> in the
          server's <Code>.env</Code> and restart the workers. A higher daily cap won't help while the total is spent.
        </p>
      </StateNote>
    );
  }

  if (cause === "daily" && budgets) {
    return (
      <StateNote icon={GaugeIcon} title="The day's AI budget ran out">
        <p>
          FrogEye spent its daily AI budget of {usd.format(budgets.dailyBudgetUsd)} before the editor could write this briefing.
          Collection kept running, and the day's signals carry over to the next run, so nothing is lost.
        </p>
        <p>
          The cap keeps a busy news day from running up costs; a total cap of {usd.format(budgets.totalBudgetUsd)} also applies, with{" "}
          {usd.format(budgets.totalUsd)} spent so far. {isAnalyst ? "To change it, set" : "A CI analyst can change it by setting"}{" "}
          <Code>LLM_DAILY_BUDGET_USD</Code> in the server's <Code>.env</Code> and restarting the workers, or catch up once with a higher
          cap: <Code>{`python -m app.cli.run_daily --max-usd ${(budgets.dailyBudgetUsd * 2).toFixed(2)}`}</Code>.
        </p>
      </StateNote>
    );
  }

  // No figures (still loading, or /costs failed), or an older day today's figures can't speak for.
  return (
    <StateNote icon={GaugeIcon} title="An AI budget cap was reached">
      <p>
        FrogEye stopped its AI work before the editor could write this briefing because it reached one of its two AI budget caps: the
        daily cap or the total cap. Collection kept running, and the signals carry over to the next run, so nothing is lost.
      </p>
      <p>
        {isAnalyst ? "To change them, set" : "A CI analyst can change them:"} <Code>LLM_DAILY_BUDGET_USD</Code> (per day) and{" "}
        <Code>LLM_TOTAL_BUDGET_USD</Code> (all time) live in the server's <Code>.env</Code>; restart the workers after changing either.
      </p>
    </StateNote>
  );
}

/** A day the editor failed: nothing is lost, and where the details are. */
export function Failed({ briefing, isAnalyst }: { briefing: BriefingOut; isAnalyst: boolean }) {
  return (
    <StateNote
      icon={CircleAlertIcon}
      title="The editor didn't finish"
      action={
        briefing.agent_run_id !== null ? (
          <Button asChild variant="outline" size="sm">
            <Link to={`/admin/agents/${briefing.agent_run_id}`}>
              See the agent run
              <ArrowRightIcon />
            </Link>
          </Button>
        ) : null
      }
    >
      <p>
        The newsroom agents stopped before the briefing was written. The day's signals carry over to the next run, so they will be
        briefed.{" "}
        {isAnalyst ? "The agent run has the error and every step before it." : "The CI team can see what went wrong in the agent run."}
      </p>
    </StateNote>
  );
}

/** Above an older briefing when the latest run had no OpenAI key. */
export function LlmOffNote({ isAnalyst }: { isAnalyst: boolean }) {
  return (
    <p className="mt-5 flex max-w-[62ch] items-start gap-2.5 rounded-md border bg-card px-3.5 py-2.5 text-meta text-muted-foreground">
      <KeyRoundIcon aria-hidden className="mt-0.5 size-4 shrink-0 text-warning" />
      <span>
        <span className="font-medium text-foreground">AI analysis is off.</span>{" "}
        {isAnalyst
          ? "Add OPENAI_API_KEY to enable AI analysis; until then the daily run only collects, and this is the latest briefing written."
          : "A CI analyst can add OPENAI_API_KEY to enable AI analysis; until then this is the latest briefing written."}
      </span>
    </p>
  );
}

/** No briefing at all yet: analysts can start the pipeline; everyone else gets a calm explanation. */
export function NoBriefing({ isAnalyst, llmOff, edition }: { isAnalyst: boolean; llmOff: boolean; edition: Edition }) {
  const start = useStartRun({ from: "briefing" });
  const runButton = isAnalyst ? (
    <Button loading={start.isPending} onClick={() => start.mutate()}>
      {start.isPending ? null : <PlayIcon />}
      Run the pipeline
    </Button>
  ) : null;
  const feedLink = (
    <Button asChild variant={isAnalyst ? "ghost" : "outline"}>
      <Link to="/feed">
        Open the Feed
        <ArrowRightIcon />
      </Link>
    </Button>
  );

  let title: string;
  let body: ReactNode;
  if (llmOff) {
    title = "AI analysis is off";
    body = (
      <>
        <p className="font-medium text-foreground">Add OPENAI_API_KEY to enable AI analysis.</p>
        <p>
          Without a key the daily run still collects and screens public sources (they're in the Feed), but no signals are analysed and no
          briefing is written.{" "}
          {isAnalyst
            ? "Set the key in the server's .env, restart the api and worker containers, then run the pipeline."
            : "A CI analyst sets the key on the server; your briefing starts the morning after."}
        </p>
      </>
    );
  } else if (isAnalyst) {
    title = "No briefing yet";
    body = (
      <p>
        The Morning Briefing is written after each daily run: collection at 06:00 Israel time, then triage, analysis and the newsroom.
        Start a run now and the briefing appears here as soon as it's published.
      </p>
    );
  } else {
    title = "Your first Morning Briefing is on its way";
    body = (
      <p>
        FrogEye writes it every morning after the 06:00 run (Israel time), tailored to the {editionLabel(edition)} edition. Until then,
        the Feed has everything collected so far.
      </p>
    );
  }

  return (
    <section className="flex max-w-[62ch] flex-col gap-3 border-t border-foreground pt-8">
      <h2 className="font-headline text-display">{title}</h2>
      <div className="flex flex-col gap-2.5 text-body text-muted-foreground">{body}</div>
      <div className="mt-3 flex flex-wrap items-center gap-2">
        {runButton}
        {feedLink}
      </div>
    </section>
  );
}

function LatestLink() {
  return (
    <Button asChild variant="outline" size="sm">
      <Link to={briefingHref(null)}>
        Read the latest briefing
        <ArrowRightIcon />
      </Link>
    </Button>
  );
}

/** /briefing/<date> for a day with no briefing in any edition. */
export function NoBriefingForDate({ date }: { date: string }) {
  return (
    <EmptyState
      className="mt-2"
      title={`No briefing for ${plainDay(date, "dateline")}.`}
      hint="FrogEye files one briefing per morning; this day has none, in any edition."
      action={<LatestLink />}
    />
  );
}

/** /briefing/<something that isn't a date>. */
export function NotABriefingDate() {
  return (
    <EmptyState
      className="mt-2"
      title="That isn't a briefing date."
      hint="Briefing addresses end in a date such as /briefing/2026-10-17."
      action={<LatestLink />}
    />
  );
}

export function BriefingLoadError({ onRetry, retrying }: { onRetry: () => void; retrying: boolean }) {
  return (
    <EmptyState
      tone="error"
      className="mt-2"
      title="The briefing didn't load."
      hint="FrogEye's API didn't answer. Check your connection, then try again."
      action={
        <Button variant="outline" size="sm" onClick={onRetry} loading={retrying}>
          <RotateCwIcon />
          Try again
        </Button>
      }
    />
  );
}

/** The masthead's geometry while the briefing loads. */
export function BriefingSkeleton() {
  return (
    <div role="status" aria-label="Loading the briefing" className="flex flex-col">
      <div className="flex h-10 items-center gap-7 border-b">
        {[56, 40, 56, 64, 92].map((w, i) => (
          <Skeleton key={i} className="h-3.5" style={{ width: w }} />
        ))}
      </div>
      <div className="flex flex-col gap-3 pt-8 pb-9">
        <Skeleton className="h-10 w-full max-w-[36rem]" />
        <Skeleton className="h-10 w-2/3 max-w-[24rem]" />
        <Skeleton className="mt-2 h-4 w-full max-w-[40rem]" />
        <Skeleton className="h-4 w-4/5 max-w-[32rem]" />
      </div>
      <div className="flex flex-col gap-3 border-t pt-6">
        <Skeleton className="h-7 w-3/4 max-w-[30rem]" />
        <Skeleton className="h-4 w-full max-w-[38rem]" />
        <Skeleton className="h-4 w-2/3 max-w-[28rem]" />
      </div>
    </div>
  );
}
