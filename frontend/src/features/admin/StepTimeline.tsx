import { BotIcon, ChevronRightIcon, CircleDotIcon, CircleXIcon, InfoIcon, WrenchIcon, type LucideIcon } from "lucide-react";
import { useId, useMemo, useState } from "react";
import { Num } from "@/components/common/Num";
import { Button } from "@/components/ui/button";
import { formatCompact, formatCount, formatUsd } from "@/lib/format";
import { formatClockSeconds } from "@/lib/time";
import { cn } from "@/lib/utils";
import { SectionHead } from "@/components/layout/SectionHead";
import { groupSteps, isErrorStep, kindSummary, stepOutcome, type AgentGroup, type AgentStepOut, type NodeGroup } from "./useAgentRuns";

/*
 * The run's trace, grouped node -> agent -> steps in execution order. Each node row is also a
 * line of the run's cost ledger: its steps, tokens and cost, with a quiet meter for its share
 * of the run's cost (one value against the run total, so `meter` on `meter-track`). A node
 * opens on click; while the run is live its current node is open, and nodes with failed
 * steps open by themselves. Arguments and results are analyst-only: the API
 * sends null to everyone else and this view does not draw them either way; everyone sees
 * how each step ended (its status), its tokens and its cost.
 */

const STEP_CAP = 50;
const ARGS_PREVIEW = 96;
const SUMMARY_PREVIEW = 200;

const KIND: Record<string, { label: string; icon: LucideIcon }> = {
  llm: { label: "LLM", icon: BotIcon },
  tool: { label: "Tool", icon: WrenchIcon },
  node: { label: "Note", icon: CircleDotIcon },
};

const fine = (n: number) => formatUsd(n, { precise: true });
/** The ledger's cost column: four places below a dollar, so the stages line up. */
const ledger = (n: number) => formatUsd(n, { decimals: 4 });

function formatArgs(args: Record<string, unknown>): string {
  return Object.entries(args)
    .map(([key, value]) => `${key}: ${JSON.stringify(value)}`)
    .join(", ");
}

function latency(ms: number | null): string | null {
  if (ms === null) return null;
  return ms < 1000 ? `${ms}ms` : `${(ms / 1000).toFixed(1)}s`;
}

function StepNumbers({ step }: { step: AgentStepOut }) {
  const took = latency(step.latency_ms);
  const parts =
    step.kind === "llm"
      ? [
          <>
            <Num value={step.tokens_in} format={formatCompact} /> in
            {step.cached_tokens ? (
              <>
                {" ("}
                <Num value={step.cached_tokens} format={formatCompact} /> cached)
              </>
            ) : null}
          </>,
          <>
            <Num value={step.tokens_out} format={formatCompact} /> out
          </>,
          <Num value={step.cost_usd} format={fine} className="text-foreground" />,
        ]
      : [];
  if (took) parts.push(<>{took}</>);
  if (!parts.length) return null;
  return (
    <span className="text-meta text-muted-foreground sm:text-right sm:whitespace-nowrap">
      {parts.map((part, i) => (
        <span key={i}>
          {i > 0 ? " · " : null}
          {part}
        </span>
      ))}
    </span>
  );
}

function StepRow({ step, showArgs, fresh }: { step: AgentStepOut; showArgs: boolean; fresh: boolean }) {
  const [expanded, setExpanded] = useState(false);
  const kind = KIND[step.kind] ?? { label: step.kind, icon: CircleDotIcon };
  const failed = isErrorStep(step);
  const args = showArgs && step.args && Object.keys(step.args).length ? formatArgs(step.args) : null;
  // A tool's arguments are what it was asked; an LLM call's are bookkeeping (task, tier, prompt
  // version) repeated on every call, so they wait one click away (PRODUCT.md: detail one step behind).
  const argsInline = step.kind !== "llm";
  // Results are for analysts; everyone else reads how the step ended, in words.
  const summary = (showArgs ? step.result_summary : null) ?? stepOutcome(step) ?? "";
  const long = summary.length > SUMMARY_PREVIEW || (argsInline && (args?.length ?? 0) > ARGS_PREVIEW);
  const toggle = long || (args !== null && !argsInline);
  const Icon = kind.icon;
  const subject = step.kind === "llm" ? (step.model ?? "Model call") : step.kind === "tool" ? (step.tool_name ?? "Tool call") : null;

  return (
    <li
      className={cn(
        "grid grid-cols-[3.5rem_minmax(0,1fr)] gap-x-3 border-t py-2.5 first:border-t-0 sm:grid-cols-[3.75rem_4rem_minmax(0,1fr)_auto]",
        fresh && "animate-in duration-300 ease-out-expo fade-in-0 slide-in-from-bottom-1",
      )}
    >
      <span className="flex flex-col text-meta text-muted-foreground">
        <span className="font-medium text-foreground">
          <span className="sr-only">Step </span>#<Num value={step.step_no} />
        </span>
        <time dateTime={step.created_at}>{formatClockSeconds(Date.parse(step.created_at))}</time>
      </span>
      <span className={`hidden h-5 items-center gap-1.5 text-meta font-medium sm:inline-flex ${failed ? "text-threat" : "text-muted-foreground"}`}>
        <Icon aria-hidden className="size-3.5 shrink-0" />
        {kind.label}
      </span>
      <div className="flex min-w-0 flex-col gap-1">
        <div className="flex flex-wrap items-baseline gap-x-2 gap-y-0.5">
          <span className={`inline-flex items-center gap-1 text-meta font-medium sm:hidden ${failed ? "text-threat" : "text-muted-foreground"}`}>
            <Icon aria-hidden className="size-3.5 shrink-0" />
            {kind.label}
          </span>
          {subject ? (
            step.kind === "tool" ? (
              <code className="font-mono text-meta font-medium text-foreground">{subject}</code>
            ) : (
              <span className="text-body font-medium text-foreground">{subject}</span>
            )
          ) : null}
          {fresh ? (
            <span className="rounded-sm bg-primary-soft px-1 text-meta font-medium text-primary-ink">New</span>
          ) : null}
        </div>
        {args && (argsInline || expanded) ? (
          <p className={`font-mono text-meta break-all text-muted-foreground ${expanded ? "whitespace-pre-wrap" : "line-clamp-1"}`}>
            <span className="font-sans font-medium text-foreground">Args </span>
            {expanded ? JSON.stringify(step.args, null, 1) : args}
          </p>
        ) : null}
        {summary ? (
          <p
            className={`text-meta break-words whitespace-pre-wrap ${failed ? "text-threat" : subject ? "text-muted-foreground" : "text-foreground"} ${expanded ? "" : "line-clamp-2"}`}
          >
            {failed ? <CircleXIcon aria-hidden className="mr-1 inline size-3.5 align-[-2px]" /> : null}
            {summary}
          </p>
        ) : null}
        {toggle ? (
          <button
            type="button"
            aria-expanded={expanded}
            onClick={() => setExpanded((v) => !v)}
            className="w-fit rounded-xs text-meta font-medium text-primary-ink underline-offset-4 hover:underline focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 focus-visible:ring-offset-background focus-visible:outline-none"
          >
            {expanded ? "Show less" : long ? "Show all" : "Show arguments"}
          </button>
        ) : null}
        <span className="sm:hidden">
          <StepNumbers step={step} />
        </span>
      </div>
      <span className="hidden sm:block">
        <StepNumbers step={step} />
      </span>
    </li>
  );
}

function AgentSteps({ group, showArgs, fresh }: { group: AgentGroup; showArgs: boolean; fresh: ReadonlySet<number> }) {
  const [all, setAll] = useState(false);
  const steps = all ? group.steps : group.steps.slice(0, STEP_CAP);
  const hidden = group.steps.length - steps.length;
  return (
    <div className="flex flex-col">
      <h4 className="flex flex-wrap items-baseline justify-between gap-x-4 border-b pb-1.5 text-meta">
        <span className="font-semibold text-foreground">{group.label}</span>
        <span className="text-muted-foreground">
          {kindSummary(group)}
          {group.cost > 0 ? (
            <>
              {" · "}
              <Num value={group.cost} format={ledger} />
            </>
          ) : null}
        </span>
      </h4>
      <ol>
        {steps.map((step) => (
          <StepRow key={step.id} step={step} showArgs={showArgs} fresh={fresh.has(step.id)} />
        ))}
      </ol>
      {hidden > 0 ? (
        <Button variant="ghost" size="sm" className="w-fit text-muted-foreground" onClick={() => setAll(true)}>
          Show {formatCount(hidden)} more {hidden === 1 ? "step" : "steps"}
        </Button>
      ) : null}
    </div>
  );
}

function NodeRow({
  group,
  runCost,
  open,
  live,
  onToggle,
  showArgs,
  fresh,
}: {
  group: NodeGroup;
  runCost: number;
  open: boolean;
  live: boolean;
  onToggle: () => void;
  showArgs: boolean;
  fresh: ReadonlySet<number>;
}) {
  const bodyId = useId();
  const share = runCost > 0 ? group.cost / runCost : 0;
  const tokens = group.tokensIn + group.tokensOut;
  return (
    <li className="border-b" data-node={group.node}>
      <h3>
        <button
          type="button"
          aria-expanded={open}
          aria-controls={bodyId}
          onClick={onToggle}
          className={cn(
            "grid w-full grid-cols-[1rem_minmax(0,1fr)_auto] items-center gap-x-4 rounded-sm px-2 py-3 text-left sm:grid-cols-[1rem_minmax(0,1fr)_5rem_6.5rem_8.5rem]",
            "transition-colors duration-150 ease-out-quart hover:bg-accent/60 focus-visible:ring-2 focus-visible:ring-ring focus-visible:outline-none",
          )}
        >
          <ChevronRightIcon
            aria-hidden
            className={cn("size-4 text-muted-foreground transition-transform duration-200 ease-out-quart", open && "rotate-90")}
          />
          <span className="flex min-w-0 flex-col gap-0.5">
            <span className="flex flex-wrap items-center gap-x-2 text-body font-semibold text-foreground">
              {group.label}
              {live ? (
                <span className="inline-flex items-center gap-1.5 text-meta font-medium text-primary-ink">
                  <span aria-hidden className="relative flex size-2">
                    <span className="absolute inset-0 animate-ping rounded-full bg-primary opacity-60 [animation-duration:1.6s]" />
                    <span className="relative size-2 rounded-full bg-primary" />
                  </span>
                  Latest
                </span>
              ) : null}
              {group.hasError ? (
                <span className="inline-flex items-center gap-1 text-meta font-medium text-threat">
                  <CircleXIcon aria-hidden className="size-3.5" />
                  Errors
                </span>
              ) : null}
            </span>
            <span className="truncate text-meta font-normal text-muted-foreground">
              {group.agents.map((a) => a.label).join(", ")} · {kindSummary(group)}
            </span>
          </span>
          <span className="hidden text-right text-meta text-muted-foreground sm:block">
            <Num value={group.steps} /> {group.steps === 1 ? "step" : "steps"}
          </span>
          <span className="hidden text-right text-meta text-muted-foreground sm:block">
            {tokens ? (
              <>
                <Num value={tokens} format={formatCompact} /> tokens
              </>
            ) : (
              "No tokens"
            )}
          </span>
          <span className="flex items-center justify-end gap-2.5">
            {runCost > 0 ? (
              <span aria-hidden className="relative h-1 w-10 shrink-0 rounded-r-full bg-meter-track">
                {share > 0 ? <span className="absolute inset-y-0 left-0 rounded-r-full bg-meter" style={{ width: `max(2px, ${share * 100}%)` }} /> : null}
              </span>
            ) : null}
            <span className="text-meta">
              <Num value={group.cost} format={ledger} className="font-semibold text-foreground" />
            </span>
            <span className="sr-only">{runCost > 0 ? `, ${Math.round(share * 100)}% of the run's cost` : ""}</span>
          </span>
        </button>
      </h3>
      {open ? (
        <div id={bodyId} className="flex flex-col gap-5 pt-1 pb-5 sm:pr-2 sm:pl-10">
          {group.agents.map((agent) => (
            <AgentSteps key={agent.key} group={agent} showArgs={showArgs} fresh={fresh} />
          ))}
        </div>
      ) : null}
    </li>
  );
}

export function StepTimeline({
  steps,
  live,
  showArgs,
  fresh,
  empty,
}: {
  steps: AgentStepOut[];
  /** The run is still going: its latest node stays open and the head says steps are arriving. */
  live: boolean;
  showArgs: boolean;
  fresh: ReadonlySet<number>;
  /** What to say when the run has no steps (yet). */
  empty: string;
}) {
  const headId = useId();
  const groups = useMemo(() => groupSteps(steps), [steps]);
  const [toggled, setToggled] = useState<ReadonlyMap<string, boolean>>(() => new Map());
  const runCost = groups.reduce((sum, g) => sum + g.cost, 0);
  const latest = live ? (groups[groups.length - 1]?.node ?? null) : null;
  // The node last seen live stays open when the run finishes, so the trace being read doesn't fold away.
  const [lastLive, setLastLive] = useState<string | null>(latest);
  if (latest && latest !== lastLive) setLastLive(latest);
  const isOpen = (g: NodeGroup) => toggled.get(g.node) ?? (g.node === (latest ?? lastLive) || g.hasError);
  const allOpen = groups.length > 0 && groups.every(isOpen);
  const toggle = (g: NodeGroup) => setToggled((prev) => new Map(prev).set(g.node, !isOpen(g)));
  const toggleAll = () => setToggled(new Map(groups.map((g) => [g.node, !allOpen])));
  const total = groups.reduce((sum, g) => sum + g.steps, 0);

  return (
    <section aria-labelledby={headId} className="flex flex-col">
      <SectionHead
        id={headId}
        title="Steps"
        meta={
          total ? (
            <span>
              <Num value={total} /> {total === 1 ? "step" : "steps"} in {groups.length} {groups.length === 1 ? "stage" : "stages"} ·{" "}
              <Num value={runCost} format={fine} />
            </span>
          ) : null
        }
        actions={
          groups.length > 1 ? (
            <Button variant="ghost" size="xs" onClick={toggleAll} className="text-muted-foreground">
              {allOpen ? "Collapse all" : "Expand all"}
            </Button>
          ) : null
        }
        className="pb-2"
      />
      {live || !showArgs ? (
        <div className="flex flex-col gap-1 pb-3 text-meta text-muted-foreground">
          {live ? (
            <p className="flex items-center gap-2">
              <span aria-hidden className="relative flex size-2">
                <span className="absolute inset-0 animate-ping rounded-full bg-primary opacity-60 [animation-duration:1.6s]" />
                <span className="relative size-2 rounded-full bg-primary" />
              </span>
              <span>
                <span className="font-medium text-foreground">Live.</span> New steps appear here as each stage finishes.
              </span>
            </p>
          ) : null}
          {!showArgs ? (
            <p className="flex items-center gap-2">
              <InfoIcon aria-hidden className="size-3.5 shrink-0" />
              Call arguments and results are shown to CI analysts only; failures, tokens and costs are shown to everyone.
            </p>
          ) : null}
        </div>
      ) : null}
      {groups.length === 0 ? (
        <p className="max-w-[65ch] border-t py-4 text-body text-muted-foreground">{empty}</p>
      ) : (
        <ol className="border-t">
          {groups.map((g) => (
            <NodeRow
              key={g.node}
              group={g}
              runCost={runCost}
              open={isOpen(g)}
              live={g.node === latest}
              onToggle={() => toggle(g)}
              showArgs={showArgs}
              fresh={fresh}
            />
          ))}
        </ol>
      )}
    </section>
  );
}
