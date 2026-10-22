import { ChevronRightIcon, CircleDashedIcon, FlaskConicalIcon, RotateCwIcon } from "lucide-react";
import { useId, useState, type ReactNode } from "react";
import { BarList } from "@/components/charts/BarList";
import { EmptyState } from "@/components/common/EmptyState";
import { Num } from "@/components/common/Num";
import { PageHeader } from "@/components/layout/PageHeader";
import { SectionHead } from "@/components/layout/SectionHead";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { Toggle } from "@/features/competitors/sections/shared";
import { ApiError } from "@/lib/api/client";
import { formatCount, formatUsd, plural } from "@/lib/format";
import { formatAbsolute } from "@/lib/time";
import {
  evalsLead,
  sharedPrefix,
  formatMetric,
  formatRate,
  fraction,
  groupMetrics,
  groupName,
  isRate,
  splitGroups,
  useEvals,
  type EvalFailure,
  type EvalGroup,
  type Evals,
  type EvalSuite,
} from "./useEvals";

/*
 * Admin › Evals: how accurate FrogEye is, for every reader (trust is earned on screen). The lead
 * says what was measured and what went wrong; the run's facts sit between hairlines as on a run's
 * detail page; then one ink-ruled section per suite, in the harness's order: what it measures and
 * who labelled it, its headline numbers as a ledger (each rate with its count), its breakdowns (a
 * bar chart where groups report the same measures, else a table), and every case it got wrong one
 * click away. Cases that could not be scored are said, never counted.
 */

function Fact({ term, children }: { term: string; children: ReactNode }) {
  return (
    <div className="flex min-w-0 flex-col gap-0.5">
      <dt className="text-meta text-muted-foreground">{term}</dt>
      <dd className="text-body font-medium">{children}</dd>
    </div>
  );
}

const MODE: Record<Evals["mode"], string> = { record: "Recorded", replay: "Replayed" };

/** What the mode meant for this run: a record run calls a model live only for what the recordings lack. */
const modeHint = (data: Evals) =>
  data.cost.live_calls === 0 ? "every answer from the recordings, no live call" : `${plural(data.cost.live_calls, "live call")} for what the recordings lacked`;

function RunFacts({ data }: { data: Evals }) {
  const m = data.models;
  const c = data.cost;
  return (
    <div className="border-y py-4">
      <dl className="grid grid-cols-2 gap-x-8 gap-y-3 sm:grid-cols-3 xl:grid-cols-6">
        <Fact term="Run">
          <time dateTime={data.run_at}>{formatAbsolute(data.run_at)}</time>
        </Fact>
        <Fact term="Mode">
          {MODE[data.mode]}
          <span className="block text-meta font-normal text-muted-foreground">{modeHint(data)}</span>
        </Fact>
        <Fact term="Recordings extended">{data.recorded_at ? <time dateTime={data.recorded_at}>{formatAbsolute(data.recorded_at)}</time> : <span className="font-normal text-muted-foreground">Never</span>}</Fact>
        <Fact term="Models">
          <span className="whitespace-nowrap">{m.fast}</span> · <span className="whitespace-nowrap">{m.smart}</span>
          <span className="block text-meta font-normal text-muted-foreground">{`fast · smart; embeddings ${m.embedding}; judge ${m.judge}`}</span>
        </Fact>
        <Fact term="Cost">
          <Num value={c.live_usd} format={(n) => formatUsd(n, { precise: true })} />
          <span className="font-normal text-muted-foreground">{` live, ${plural(c.live_calls, "call")}`}</span>
          <span className="block text-meta font-normal text-muted-foreground">{`${plural(c.replayed_calls, "call")} replayed, ${formatUsd(c.recorded_usd, { precise: true })} when recorded`}</span>
        </Fact>
        <Fact term="Not recorded">
          {data.stale_cases === 0 ? <>
              None
              <span className="block text-meta font-normal text-muted-foreground">every case scored</span>
            </> : <span className="text-warning">{plural(data.stale_cases, "case")}</span>}
        </Fact>
      </dl>
    </div>
  );
}

/** The headline numbers as a ledger: the measure, its value, and for a rate the cases it counts. */
function Metrics({ suite }: { suite: EvalSuite }) {
  return (
    <dl aria-label={`${suite.title}: headline numbers`} className="flex flex-col divide-y border-t">
      {suite.metrics.map((m) => {
        const frac = fraction(m);
        return (
          <div key={m.key} className="grid grid-cols-[minmax(0,1fr)_auto] items-baseline gap-x-4 py-2">
            <dt className="text-body text-pretty text-foreground">{m.label}</dt>
            <dd className="flex items-baseline gap-2 text-right">
              {m.value === null ? <span className="text-body text-muted-foreground">{formatMetric(m)}</span> : <Num value={m.value} format={() => formatMetric(m)} className="text-body font-semibold" />}
              {frac ? <span className="w-[4.5rem] text-meta whitespace-nowrap text-muted-foreground">{frac}</span> : <span className="w-[4.5rem]" aria-hidden />}
            </dd>
          </div>
        );
      })}
    </dl>
  );
}

/** Groups that report the same measures, side by side as bars on the measure's own 0–1 scale, one measure at a time. */
function GroupChart({ suite, groups }: { suite: EvalSuite; groups: EvalGroup[] }) {
  const metrics = groupMetrics(groups);
  const [key, setKey] = useState(metrics[0]!.key);
  const metric = metrics.find((m) => m.key === key) ?? metrics[0]!;
  const rows = groups.map((g) => ({ g, m: g.metrics.find((x) => x.key === metric.key)! }));
  const rate = rows.every(({ m }) => isRate(m));
  const best = [...rows].sort((a, b) => (b.m.value ?? 0) - (a.m.value ?? 0))[0]!;
  const worst = [...rows].sort((a, b) => (a.m.value ?? 0) - (b.m.value ?? 0))[0]!;
  return (
    <BarList
      title={`${metric.label} by ${suite.name === "retrieval" ? "mode" : "group"}`}
      summary={`${groupName(best.g.name)} ${formatMetric(best.m)}, ${groupName(worst.g.name)} ${formatMetric(worst.m)}.`}
      empty={{ title: "No breakdown recorded" }}
      actions={metrics.length > 1 ? <Toggle label="Measure" options={metrics.map((m) => ({ value: m.key, label: m.label }))} value={metric.key} onChange={setKey} /> : undefined}
      items={rows.map(({ g, m }) => ({ key: g.name, label: groupName(g.name), value: m.value ?? 0, aside: fraction(m) ?? undefined }))}
      max={12}
      extent={1}
      valueFormat={rate ? formatRate : (n) => n.toFixed(3)}
      unit={rate ? "of the group's cases" : undefined}
      note={rate ? "Each bar is a share of its group's cases, counted beside its value; a full bar is all of them." : `Each bar is the mean ${metric.label} over the group's cases; a full bar is 1.`}
    />
  );
}

/**
 * Every breakdown as a table: a row per group, a column per measure (a group that does not report
 * one reads "—"). When each group reports one measure of its own (a guardrail's genuine inputs
 * pass, its attacks are caught) the measures share one "Result" column, each named in its cell.
 */
function GroupTable({ groups, label, prefix }: { groups: EvalGroup[]; label: string; prefix?: string }) {
  const all = groupMetrics(groups);
  const merged = all.length > 1 && groups.every((g) => g.metrics.length === 1);
  const metrics = merged ? [{ key: "__result", label: "Result" }] : all;
  const name = (g: EvalGroup) => groupName(prefix ? g.name.slice(prefix.length) : g.name);
  return (
    <div className="overflow-x-auto">
      <table aria-label={label} className="w-full border-collapse text-body max-sm:text-meta">
        <thead className="border-b">
          <tr>
            <th className="h-9 pr-3 text-left align-bottom text-meta font-medium text-muted-foreground">Group</th>
            <th className="h-9 px-3 text-right align-bottom text-meta font-medium text-muted-foreground">Cases</th>
            {metrics.map((m) => (
              <th key={m.key} className="h-9 px-3 text-right align-bottom text-meta font-medium text-muted-foreground last:pr-0">
                {m.label}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {groups.map((g) => (
            <tr key={g.name} className="border-b last:border-b-0">
              <th scope="row" className="py-2 pr-3 text-left font-normal text-foreground">
                {name(g)}
              </th>
              <td className="px-3 py-2 text-right text-muted-foreground">
                <Num value={g.cases} />
              </td>
              {metrics.map((key) => {
                const m = merged ? g.metrics[0] : g.metrics.find((x) => x.key === key.key);
                const frac = m ? fraction(m) : null;
                return (
                  <td key={key.key} className="px-3 py-2 text-right whitespace-nowrap last:pr-0">
                    {m ? (
                      <>
                        {merged ? <span className="mr-1.5 text-muted-foreground">{m.label}</span> : null}
                        {m.value === null ? <span className="text-muted-foreground">{formatMetric(m)}</span> : <Num value={m.value} format={() => formatMetric(m)} className="font-medium" />}
                        {frac ? <span className="ml-1.5 text-meta text-muted-foreground">{frac}</span> : null}
                      </>
                    ) : (
                      <span className="text-muted-foreground">—</span>
                    )}
                  </td>
                );
              })}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function Failure({ f }: { f: EvalFailure }) {
  return (
    <li className="grid gap-x-6 gap-y-1 py-3 sm:grid-cols-[11rem_minmax(0,1fr)]">
      <code className="text-meta font-medium text-foreground wrap-anywhere">{f.case_id}</code>
      <div className="flex min-w-0 flex-col gap-1 wrap-anywhere">
        <dl className="grid grid-cols-[4.5rem_minmax(0,1fr)] gap-x-3 gap-y-0.5 text-meta">
          <dt className="text-muted-foreground">Expected</dt>
          <dd className="text-foreground">{f.expected}</dd>
          <dt className="text-muted-foreground">Got</dt>
          <dd className="font-medium text-foreground">{f.got}</dd>
        </dl>
        {f.detail ? <p className="max-w-[80ch] text-meta text-pretty text-muted-foreground">{f.detail}</p> : null}
      </div>
    </li>
  );
}

/** Every case the app got wrong, one click away. */
function Failures({ suite }: { suite: EvalSuite }) {
  if (suite.failures.length === 0) {
    return <p className="text-meta text-muted-foreground">No failures: every scored case matched its label.</p>;
  }
  return (
    <details className="group/fail">
      <summary className="inline-flex cursor-pointer list-none items-center gap-1.5 rounded-sm text-body font-medium text-foreground select-none hover:text-primary-ink focus-visible:ring-2 focus-visible:ring-ring focus-visible:outline-none [&::-webkit-details-marker]:hidden">
        <ChevronRightIcon aria-hidden className="size-4 text-muted-foreground transition-transform duration-150 ease-out-quart group-open/fail:rotate-90" />
        {`${plural(suite.failures.length, "failure")} · each case it got wrong`}
      </summary>
      <ol aria-label={`${suite.title}: failures`} className="mt-2 flex flex-col divide-y border-t">
        {suite.failures.map((f) => (
          <Failure key={f.case_id} f={f} />
        ))}
      </ol>
    </details>
  );
}

/** Cases with a model call missing from the recordings: left out of the numbers, and named. */
function StaleNote({ suite }: { suite: EvalSuite }) {
  if (suite.stale === 0) return null;
  return (
    <div className="flex items-start gap-2 text-meta text-pretty">
      <CircleDashedIcon aria-hidden className="mt-0.5 size-3.5 shrink-0 text-warning" />
      <p className="text-muted-foreground">
        <span className="font-medium text-foreground">{`${plural(suite.stale, "case")} not recorded`}</span>
        {`: left out of these numbers until their model calls are recorded (${suite.stale_cases.join(", ")}).`}
      </p>
    </div>
  );
}

/** The fact-checker's numbers follow an experiment recorded beside the results; said, not restated. */
function ExperimentNote({ suite }: { suite: EvalSuite }) {
  return (
    <div className="flex items-start gap-2 text-meta text-pretty">
      <FlaskConicalIcon aria-hidden className="mt-0.5 size-3.5 shrink-0 text-muted-foreground" />
      <p className="max-w-[72ch] text-muted-foreground">
        <span className="font-medium text-foreground">These figures follow an experiment.</span>
        {` Three checker variants were run on these ${formatCount(suite.cases)} claims, and the app adopted the reason-first verdict on the smart model. The comparison, with each variant's catch rate and cost, is in backend/evals/results/experiments.md.`}
      </p>
    </div>
  );
}

function Suite({ suite }: { suite: EvalSuite }) {
  const headId = useId();
  const { chart, table } = splitGroups(suite.groups);
  const prefix = sharedPrefix(table);
  const scored = suite.scored === suite.cases ? plural(suite.cases, "case") : `${formatCount(suite.scored)} of ${plural(suite.cases, "case")} scored`;
  return (
    <section aria-labelledby={headId} className="flex min-w-0 flex-col">
      <SectionHead id={headId} title={suite.title} meta={scored} />
      <div className="grid gap-x-12 gap-y-6 lg:grid-cols-[minmax(0,5fr)_minmax(0,6fr)]">
        <div className="flex min-w-0 flex-col gap-4">
          <p className="max-w-[72ch] text-body text-pretty text-muted-foreground">{suite.description}</p>
          <p className="text-meta text-muted-foreground">
            Labels: <span className="text-foreground">{suite.label_source}</span>
          </p>
          <Metrics suite={suite} />
        </div>
        <div className="flex min-w-0 flex-col gap-5">
          {chart.length > 0 ? <GroupChart suite={suite} groups={chart} /> : null}
          {table.length > 0 ? (
            <div className="flex flex-col gap-2">
              <p className="text-body font-semibold text-foreground">{prefix ? `${groupName(prefix.slice(0, -3))}, by kind` : chart.length > 0 ? "More breakdowns" : "By group"}</p>
              <GroupTable groups={table} label={`${suite.title}: by group`} prefix={prefix} />
            </div>
          ) : null}
        </div>
      </div>
      <div className="mt-6 flex flex-col gap-3">
        <StaleNote suite={suite} />
        {suite.name === "fact_checker" ? <ExperimentNote suite={suite} /> : null}
        <Failures suite={suite} />
      </div>
    </section>
  );
}

function EvalsSkeleton() {
  return (
    <div role="status" aria-label="Loading the evaluation results" className="flex flex-col gap-10">
      <Skeleton className="h-16 w-full" />
      {Array.from({ length: 2 }, (_, i) => (
        <div key={i} className="flex flex-col gap-3 border-t border-foreground pt-3">
          <Skeleton className="h-5 w-48" />
          <Skeleton className="h-4 w-full max-w-[40rem]" />
          <Skeleton className="h-32 w-full" />
        </div>
      ))}
    </div>
  );
}

const DESCRIPTION = "How accurate FrogEye's models and guards are, measured on labelled cases.";

export function EvalsPage() {
  const query = useEvals();
  const missing = query.error instanceof ApiError && query.error.status === 404;
  let body: ReactNode;
  if (query.isPending) {
    body = <EvalsSkeleton />;
  } else if (missing) {
    body = <EmptyState title="No evaluation results yet" hint="Results appear here once the evaluation harness has run (python -m evals.run) and written backend/evals/results/latest.json." />;
  } else if (query.isError && !query.data) {
    body = (
      <EmptyState
        tone="error"
        title="The evaluation results didn't load."
        hint="FrogEye's API didn't answer. Check your connection, then try again."
        action={
          <Button variant="outline" size="sm" onClick={() => void query.refetch()} loading={query.isRefetching}>
            <RotateCwIcon />
            Try again
          </Button>
        }
      />
    );
  } else {
    const data = query.data!;
    body = (
      <div className="flex flex-col gap-12">
        <RunFacts data={data} />
        {data.suites.length === 0 ? <EmptyState title="The results hold no suite" hint="The last run of the harness wrote no suite's results; run it again to measure them." /> : data.suites.map((s) => <Suite key={s.name} suite={s} />)}
      </div>
    );
  }
  return (
    <>
      <PageHeader title="Evals" description={query.data ? evalsLead(query.data) : DESCRIPTION} />
      {body}
    </>
  );
}
