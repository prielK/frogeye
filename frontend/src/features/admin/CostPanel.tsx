import { OctagonAlertIcon, RotateCwIcon, TriangleAlertIcon } from "lucide-react";
import { useId } from "react";
import { EmptyState } from "@/components/common/EmptyState";
import { Num } from "@/components/common/Num";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";
import { useAuth } from "@/lib/auth";
import { formatPercent, formatUsd } from "@/lib/format";
import { plainDay } from "@/lib/time";
import { cn } from "@/lib/utils";
import { SectionHead } from "@/components/layout/SectionHead";
import { humanize } from "./useAgentRuns";
import { budgetState, capShare, useCosts, type BudgetState, type CostsOut } from "./useCosts";

/*
 * AI spend. Form (dataviz): each cap is one value against a limit, so a meter, never a stat
 * tile; the week is a magnitude over time, so seven columns of one hue against the daily cap
 * drawn as a reference line. Every value is printed (a text twin of the chart), tooltips only
 * add the arithmetic. Fills: `meter` while there is room, then `warning` near the cap and
 * `threat` at it, each validated >= 3:1 on card and page in both themes, and each state also
 * says itself in an icon and words.
 */

const FILL: Record<BudgetState, string> = { ok: "bg-meter", near: "bg-warning", reached: "bg-threat" };
const money = (v: number) => formatUsd(v);
const fine = (v: number) => formatUsd(v, { precise: true });

function StateWord({ state }: { state: BudgetState }) {
  if (state === "ok") return null;
  const near = state === "near";
  const Icon = near ? TriangleAlertIcon : OctagonAlertIcon;
  return (
    <span className={cn("font-medium", near ? "text-warning" : "text-threat")}>
      <Icon aria-hidden className="mr-1 inline size-3.5 align-[-2px]" />
      {near ? "Near the cap" : "Cap reached"}
    </span>
  );
}

function BudgetMeter({ label, spent, cap, explain }: { label: string; spent: number; cap: number; explain: (state: BudgetState) => string }) {
  const id = useId();
  const state = budgetState(spent, cap);
  const share = cap > 0 ? Math.min(1, spent / cap) : 1;
  const scope = label === "Today" ? "daily" : "total";
  return (
    <div className="flex flex-col gap-2">
      <div className="flex items-baseline justify-between gap-4">
        <span id={id} className="text-body font-medium">
          {label}
        </span>
        <span className="text-body whitespace-nowrap">
          <Num value={spent} format={money} className="font-semibold text-foreground" />
          <span className="text-muted-foreground">
            {" of "}
            <Num value={cap} format={money} />
          </span>
        </span>
      </div>
      <div
        role="meter"
        aria-labelledby={id}
        aria-valuemin={0}
        aria-valuemax={cap}
        aria-valuenow={Math.min(spent, cap)}
        aria-valuetext={`${formatUsd(spent)} of ${formatUsd(cap)}, ${capShare(spent, cap)} of the ${scope} cap`}
        className="relative h-2 rounded-r-[4px] bg-meter-track"
      >
        {spent > 0 ? (
          <span
            className={cn("absolute inset-y-0 left-0 rounded-r-[4px] transition-[width] duration-200 ease-out-quart", FILL[state])}
            style={{ width: `max(2px, ${share * 100}%)` }}
          />
        ) : null}
      </div>
      <p className="text-meta text-pretty text-muted-foreground">
        <StateWord state={state} />
        {state !== "ok" ? " · " : null}
        {capShare(spent, cap)} of the {scope} cap. {explain(state)}
      </p>
    </div>
  );
}

function WeekChart({ days, cap }: { days: CostsOut["last_7_days"]; cap: number }) {
  // Headroom above the taller of the cap and the busiest day, so the cap line never sits on the frame.
  const max = Math.max(cap, ...days.map((d) => d.cost_usd)) * 1.15 || 1;
  const week = days.reduce((sum, d) => sum + d.cost_usd, 0);
  const capAt = cap > 0 ? `${(cap / max) * 100}%` : null;
  return (
    <figure className="flex min-w-0 flex-col gap-3" aria-label="AI spend per day, the last 7 days">
      <figcaption className="text-meta text-muted-foreground">
        <span className="text-body font-medium text-foreground">Last 7 days</span>
        {" · "}
        <Num value={week} format={money} className="font-semibold text-foreground" /> in all
      </figcaption>
      <div className="grid grid-cols-[minmax(0,1fr)_3.5rem] gap-x-2 sm:grid-cols-[minmax(0,1fr)_4.5rem] sm:gap-x-3">
        <ul className="relative grid h-28 grid-cols-7 border-b border-input">
          {capAt ? <li aria-hidden className="pointer-events-none absolute inset-x-0 border-t border-dashed border-muted-foreground/70" style={{ bottom: capAt }} /> : null}
          {days.map((d, i) => {
            const today = i === days.length - 1;
            const name = today ? "Today" : plainDay(d.date, "long");
            const detail = `${name}: ${fine(d.cost_usd)}${cap > 0 ? `, ${capShare(d.cost_usd, cap)} of the ${money(cap)} daily cap` : ""}`;
            return (
              <li key={d.date} className="relative flex min-w-0 justify-center">
                <Tooltip>
                  <TooltipTrigger asChild>
                    <button
                      type="button"
                      aria-label={detail}
                      className={cn(
                        "group flex h-full w-full items-end justify-center rounded-t-sm",
                        "transition-colors duration-150 ease-out-quart hover:bg-accent/70 focus-visible:bg-accent/70 focus-visible:ring-2 focus-visible:ring-ring focus-visible:outline-none",
                      )}
                    >
                      {d.cost_usd > 0 ? (
                        <span
                          className="w-full max-w-6 rounded-t-[4px] bg-meter transition-opacity duration-150 group-hover:opacity-80"
                          style={{ height: `max(2px, ${(d.cost_usd / max) * 100}%)` }}
                        />
                      ) : null}
                    </button>
                  </TooltipTrigger>
                  <TooltipContent>{detail}.</TooltipContent>
                </Tooltip>
              </li>
            );
          })}
        </ul>
        <div aria-hidden className="relative h-28">
          {capAt ? (
            <span className="absolute left-0 translate-y-1/2 text-meta leading-tight text-muted-foreground" style={{ bottom: capAt }}>
              Daily cap
              <br />
              <Num value={cap} format={money} className="text-foreground" />
            </span>
          ) : null}
        </div>
        <ul aria-hidden className="grid grid-cols-7 pt-2 text-center">
          {days.map((d, i) => {
            const today = i === days.length - 1;
            return (
              <li key={d.date} className="flex min-w-0 flex-col gap-0.5">
                <span className={`truncate text-meta ${today ? "font-semibold text-foreground" : "text-muted-foreground"}`}>
                  {today ? (
                    "Today"
                  ) : (
                    <>
                      <span className="sm:hidden">{plainDay(d.date, "weekday")}</span>
                      <span className="hidden sm:inline">{plainDay(d.date, "weekdayDay")}</span>
                    </>
                  )}
                </span>
                <span className={`truncate text-meta ${today ? "font-semibold" : ""}`}>
                  <Num value={d.cost_usd} format={money} className="text-foreground" />
                </span>
              </li>
            );
          })}
        </ul>
      </div>
    </figure>
  );
}

function share(part: number, total: number): string {
  return total > 0 && part > 0 ? formatPercent(part / total) : "—";
}

function Breakdown({ costs }: { costs: CostsOut }) {
  const taskId = useId();
  const modelId = useId();
  const num = "text-right";
  if (costs.by_task.length === 0 && costs.by_model.length === 0) {
    return <p className="text-body text-muted-foreground">No AI calls yet. The breakdown fills in after the first run with an OpenAI key.</p>;
  }
  return (
    <div className="grid gap-x-12 gap-y-8 lg:grid-cols-2">
      <section aria-labelledby={taskId} className="min-w-0">
        <h3 id={taskId} className="pb-1 text-body font-medium">
          By task <span className="font-normal text-muted-foreground">· all time</span>
        </h3>
        <Table>
          <TableHeader>
            <TableRow className="hover:bg-transparent">
              <TableHead className="pl-0">Task</TableHead>
              <TableHead className={num}>Calls</TableHead>
              <TableHead className={num}>Cost</TableHead>
              <TableHead className={cn(num, "pr-0")}>Share</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {costs.by_task.map((t) => (
              <TableRow key={t.task}>
                <TableCell className="pl-0 font-medium">{humanize(t.task)}</TableCell>
                <TableCell className={num}>
                  <Num value={t.calls} />
                </TableCell>
                <TableCell className={num}>
                  <Num value={t.cost_usd} format={money} />
                </TableCell>
                <TableCell className={cn(num, "pr-0 text-muted-foreground")}>{share(t.cost_usd, costs.total_usd)}</TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
      </section>
      <section aria-labelledby={modelId} className="min-w-0">
        <h3 id={modelId} className="pb-1 text-body font-medium">
          By model <span className="font-normal text-muted-foreground">· all time</span>
        </h3>
        <Table>
          <TableHeader>
            <TableRow className="hover:bg-transparent">
              <TableHead className="pl-0">Model</TableHead>
              <TableHead className={num}>Calls</TableHead>
              <TableHead className={num}>
                <Tooltip>
                  <TooltipTrigger className="cursor-help underline decoration-dotted underline-offset-4">From cache</TooltipTrigger>
                  <TooltipContent>Calls answered from FrogEye's own cache: they cost nothing.</TooltipContent>
                </Tooltip>
              </TableHead>
              <TableHead className={cn(num, "pr-0")}>Cost</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {costs.by_model.map((m) => (
              <TableRow key={m.model}>
                <TableCell className="pl-0 font-medium">{m.model}</TableCell>
                <TableCell className={num}>
                  <Num value={m.calls} />
                </TableCell>
                <TableCell className={cn(num, "text-muted-foreground")}>
                  <Num value={m.cached_calls} />
                </TableCell>
                <TableCell className={cn(num, "pr-0")}>
                  <Num value={m.cost_usd} format={money} />
                </TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
      </section>
    </div>
  );
}

/** The spend section of the Agent runs page: both caps, the week, and where the money went. */
export function CostPanel() {
  const headId = useId();
  const costs = useCosts();
  const { user } = useAuth();
  const isAnalyst = user?.role === "analyst";
  const raiser = isAnalyst ? "until you raise" : "until a CI analyst raises";

  let body;
  if (costs.isPending) {
    body = (
      <div role="status" aria-label="Loading AI spend" className="grid gap-x-12 gap-y-8 lg:grid-cols-[minmax(0,22rem)_minmax(0,1fr)]">
        <div className="flex flex-col gap-6">
          <Skeleton className="h-14 w-full" />
          <Skeleton className="h-14 w-full" />
        </div>
        <Skeleton className="h-40 w-full" />
      </div>
    );
  } else if (costs.isError) {
    body = (
      <EmptyState
        tone="error"
        title="AI spend didn't load."
        hint="FrogEye's API didn't answer. Check your connection, then try again."
        action={
          <Button variant="outline" size="sm" onClick={() => costs.refetch()} loading={costs.isRefetching}>
            <RotateCwIcon />
            Try again
          </Button>
        }
      />
    );
  } else {
    const c = costs.data;
    body = (
      <div className="flex flex-col gap-10">
        <div className="grid gap-x-12 gap-y-8 lg:grid-cols-[minmax(0,22rem)_minmax(0,1fr)]">
          <div className="flex flex-col gap-6">
            <BudgetMeter
              label="Today"
              spent={c.today_usd}
              cap={c.daily_budget_usd}
              explain={(state) =>
                state === "reached"
                  ? "AI analysis waits for midnight; new documents carry over to the next run."
                  : state === "near"
                    ? "AI analysis stops at the cap until midnight."
                    : "It resets at midnight."
              }
            />
            <BudgetMeter
              label="All time"
              spent={c.total_usd}
              cap={c.total_budget_usd}
              explain={(state) =>
                state === "reached"
                  ? `AI analysis is off ${raiser} LLM_TOTAL_BUDGET_USD.`
                  : state === "near"
                    ? `When it's spent, AI analysis stops ${raiser} the cap.`
                    : "It never resets; only a higher cap adds room."
              }
            />
          </div>
          <WeekChart days={c.last_7_days} cap={c.daily_budget_usd} />
        </div>
        <Breakdown costs={c} />
      </div>
    );
  }

  return (
    <section aria-labelledby={headId}>
      <SectionHead id={headId} title="AI spend" meta="Every AI call, searches included · days run midnight to midnight, Israel time" />
      {body}
    </section>
  );
}
