import { useQuery, useQueryClient, type QueryClient } from "@tanstack/react-query";
import { useCallback, useEffect, useRef, useState } from "react";
import { api, ApiError, type Schemas } from "@/lib/api/client";
import { formatCount } from "@/lib/format";
import { agentRunsKey, agentRunsQuery } from "@/lib/api/queries";
import { RUN_STAGES, SEED_MAX_AGE_MS, useEventStream } from "@/lib/events";
import { RUNS_POLL_MS } from "./useRuns";

export type AgentRunOut = Schemas["AgentRunOut"];
export type AgentStepOut = Schemas["AgentStepOut"];

/*
 * Agent runs and their step traces. Keys live under ["agent-runs", ...] so lib/events folds
 * run progress into every cached list and detail; steps sit under their own root,
 * ["agent-steps", id], because that fold reads any array under "agent-runs" as runs.
 */

export const RUNS_LIMIT = 30;
const STEPS_PAGE = 500;

export { agentRunsKey };
export const agentRunKey = (id: number) => ["agent-runs", "detail", id] as const;
export const agentStepsKey = (id: number) => ["agent-steps", id] as const;

/**
 * Poll a run while it is running (and recent: an older "running" row is a dead worker's). The
 * stream cannot be trusted to say a run ended: its last `run.progress` is sent before the
 * runner commits the finished row, and a run that dies sends nothing.
 */
export function agentRunRefetchInterval(run: Pick<AgentRunOut, "status" | "started_at"> | undefined, now = Date.now()): number | false {
  return run?.status === "running" && now - Date.parse(run.started_at) <= SEED_MAX_AGE_MS ? RUNS_POLL_MS : false;
}

export function agentRunsRefetchInterval(runs: AgentRunOut[] | undefined, now = Date.now()): number | false {
  return runs?.some((r) => agentRunRefetchInterval(r, now) !== false) ? RUNS_POLL_MS : false;
}

export function useAgentRuns(limit = RUNS_LIMIT) {
  return useQuery({
    ...agentRunsQuery(limit),
    refetchInterval: (query) => agentRunsRefetchInterval(query.state.data),
  });
}

export function useAgentRun(id: number) {
  return useQuery({
    queryKey: agentRunKey(id),
    enabled: Number.isInteger(id) && id > 0,
    queryFn: async ({ signal }) => {
      const { data, response } = await api.GET("/api/v1/agent-runs/{run_id}", { params: { path: { run_id: id } }, signal });
      if (!data) throw new ApiError(response.status);
      return data;
    },
    refetchInterval: (query) => agentRunRefetchInterval(query.state.data),
  });
}

/** Every step after `after` (the API's `after_step` keys on step_no), 500 per request. */
async function fetchSteps(id: number, after: number, signal?: AbortSignal): Promise<AgentStepOut[]> {
  const all: AgentStepOut[] = [];
  for (let cursor = after; ; ) {
    const { data, response } = await api.GET("/api/v1/agent-runs/{run_id}/steps", {
      params: { path: { run_id: id }, query: { after_step: cursor, limit: STEPS_PAGE } },
      signal,
    });
    if (!data) throw new ApiError(response.status);
    all.push(...data);
    if (data.length < STEPS_PAGE) return all;
    cursor = data[data.length - 1]!.step_no;
  }
}

function merge(current: AgentStepOut[], incoming: AgentStepOut[]): AgentStepOut[] {
  const known = new Set(current.map((s) => s.id));
  const added = incoming.filter((s) => !known.has(s.id));
  if (added.length === 0) return current;
  return [...current, ...added].sort((a, b) => a.step_no - b.step_no || a.id - b.id);
}

/** Fetch the steps after the last one cached and append them; returns the ids it added. */
async function appendNewSteps(queryClient: QueryClient, id: number): Promise<number[]> {
  const current = queryClient.getQueryData<AgentStepOut[]>(agentStepsKey(id));
  if (!current) {
    await queryClient.invalidateQueries({ queryKey: agentStepsKey(id), exact: true });
    return [];
  }
  const last = current.reduce((max, s) => Math.max(max, s.step_no), 0);
  const incoming = await fetchSteps(id, last);
  const known = new Set((queryClient.getQueryData<AgentStepOut[]>(agentStepsKey(id)) ?? current).map((s) => s.id));
  queryClient.setQueryData<AgentStepOut[]>(agentStepsKey(id), (steps) => merge(steps ?? current, incoming));
  return incoming.filter((s) => !known.has(s.id)).map((s) => s.id);
}

/**
 * A run's step trace, kept live: the stream's `run.progress` carries no steps, so each event
 * for this run fetches the steps after the last one seen (after_step) and appends them; a
 * reconnected stream catches up the same way, and so does the moment `status` (the polled
 * run's) leaves "running", for steps written after the last event. Appends never overlap: a
 * request that lands while one is in flight runs once more after it. `fresh` holds the ids
 * appended live.
 */
export function useAgentSteps(id: number, status?: string) {
  const queryClient = useQueryClient();
  const [fresh, setFresh] = useState<ReadonlySet<number>>(() => new Set());
  const busy = useRef(false);
  const again = useRef(false);
  const query = useQuery({
    queryKey: agentStepsKey(id),
    enabled: Number.isInteger(id) && id > 0,
    queryFn: ({ signal }) => fetchSteps(id, 0, signal),
  });

  const catchUp = useCallback(async () => {
    if (busy.current) {
      again.current = true;
      return;
    }
    busy.current = true;
    try {
      do {
        again.current = false;
        const added = await appendNewSteps(queryClient, id);
        if (added.length) setFresh((prev) => new Set([...prev, ...added]));
      } while (again.current);
    } catch {
      // A failed catch-up leaves the trace as it was; the next progress event tries again.
    } finally {
      busy.current = false;
    }
  }, [queryClient, id]);

  const wasRunning = useRef(false);
  useEffect(() => {
    if (status === "running") wasRunning.current = true;
    else if (status !== undefined && wasRunning.current) {
      wasRunning.current = false;
      void catchUp();
    }
  }, [status, catchUp]);

  useEventStream((message) => {
    if (message.type === "run.progress") {
      const payload = message.payload as { agent_run_id?: unknown } | null;
      if (payload?.agent_run_id === id) void catchUp();
    } else if (message.type === "stream.reconnected") {
      void queryClient.invalidateQueries({ queryKey: agentRunKey(id), exact: true });
      void catchUp();
    }
  });

  return { ...query, fresh };
}

// ---- reading a run ----------------------------------------------------------------------------

const FLAGS = ["llm_disabled", "budget_stopped", "stage_errors"];

/**
 * The run's notes: `;`-joined flags (llm_disabled, budget_stopped) and node errors (backend
 * runner._notes). Non-analysts get the errors folded into one `stage_errors` flag (an
 * error can quote model output): `errorsHidden`.
 */
export function parseNotes(notes: string | null): { aiOff: boolean; budgetStopped: boolean; errorsHidden: boolean; errors: string[] } {
  const parts = (notes ?? "")
    .split(";")
    .map((p) => p.trim())
    .filter(Boolean);
  return {
    aiOff: parts.includes("llm_disabled"),
    budgetStopped: parts.includes("budget_stopped"),
    errorsHidden: parts.includes("stage_errors"),
    errors: parts.filter((p) => !FLAGS.includes(p)),
  };
}

export function funnelCount(run: Pick<AgentRunOut, "funnel">, key: string): number | null {
  const v = run.funnel?.[key];
  return typeof v === "number" && Number.isFinite(v) && v >= 0 ? v : null;
}

const BRIEFING_STATUS: Record<string, string> = {
  published: "Published",
  quiet: "Quiet day",
  skipped_budget: "Skipped at the budget",
  failed: "Failed",
};

/** What became of the run's briefing, in words; null when the run filed none. */
export function briefingOutcome(run: AgentRunOut): string | null {
  if (!run.briefing_date) return null;
  const status = run.funnel?.briefing_status;
  return typeof status === "string" ? (BRIEFING_STATUS[status] ?? status) : "Filed";
}

export function graphLabel(graph: string): string {
  return graph === "daily_intel" ? "Daily run" : humanize(graph);
}

const GRAPH_NAMES: Record<string, string> = { daily_intel: "Daily intelligence" };

/** The graph a run executed, for the runs table: "Daily intelligence". */
export function graphName(graph: string): string {
  return GRAPH_NAMES[graph] ?? humanize(graph);
}

/** "19 full analyses": what stage B wrote in a run. */
export function fullAnalyses(n: number): string {
  return `${formatCount(n)} full ${n === 1 ? "analysis" : "analyses"}`;
}

export function humanize(key: string): string {
  const words = key.replace(/[_-]+/g, " ").trim();
  return words ? words.charAt(0).toUpperCase() + words.slice(1) : key;
}

// ---- grouping the trace: node -> agent -> steps --------------------------------------------------

const AGENT_LABELS: Record<string, string> = { embed: "Embeddings" };
/** Older runs left these nodes' steps without an agent; their agent is known. */
const NODE_AGENTS: Record<string, string> = { triage: "triage", disambiguate: "disambiguate", analyze: "analyst" };

export function nodeLabel(node: string): string {
  return RUN_STAGES.find((s) => s.key === node)?.label ?? humanize(node);
}

/** The agent behind a step; a step with none is the graph's own bookkeeping ("Pipeline"). */
export function agentLabel(agent: string | null, node?: string): string {
  const name = agent ?? (node ? NODE_AGENTS[node] : undefined);
  if (!name) return "Pipeline";
  return AGENT_LABELS[name] ?? humanize(name);
}

type Totals = { cost: number; tokensIn: number; tokensOut: number; llm: number; tool: number; note: number };

export type AgentGroup = Totals & { key: string; agent: string | null; label: string; steps: AgentStepOut[] };
export type NodeGroup = Totals & { node: string; label: string; agents: AgentGroup[]; steps: number; hasError: boolean };

function empty(): Totals {
  return { cost: 0, tokensIn: 0, tokensOut: 0, llm: 0, tool: 0, note: 0 };
}

function add(t: Totals, s: AgentStepOut) {
  t.cost += s.cost_usd;
  t.tokensIn += s.tokens_in;
  t.tokensOut += s.tokens_out;
  if (s.kind === "llm") t.llm += 1;
  else if (s.kind === "tool") t.tool += 1;
  else t.note += 1;
}

/** The step failed: the API's status (every role), or the summary analysts read. */
export function isErrorStep(step: Pick<AgentStepOut, "result_summary" | "status">): boolean {
  return step.status === "error" || /^error\b/i.test(step.result_summary ?? "");
}

/** How a step that did not end ok ended, in words, for readers who don't get its result. */
export function stepOutcome(step: Pick<AgentStepOut, "status" | "error_kind">): string | null {
  if (step.status === "error") return step.error_kind ? `Failed: ${step.error_kind}` : "Failed";
  if (step.status === "refused") return "Refused by the model";
  if (step.status === "budget") return "Stopped at the budget cap";
  return null;
}

/** Group steps by node, then agent, each in order of first appearance (that is, execution order). */
export function groupSteps(steps: AgentStepOut[]): NodeGroup[] {
  const nodes = new Map<string, NodeGroup>();
  for (const step of [...steps].sort((a, b) => a.step_no - b.step_no || a.id - b.id)) {
    let node = nodes.get(step.node);
    if (!node) {
      node = { ...empty(), node: step.node, label: nodeLabel(step.node), agents: [], steps: 0, hasError: false };
      nodes.set(step.node, node);
    }
    const key = step.agent ?? "";
    let agent = node.agents.find((a) => a.key === key);
    if (!agent) {
      agent = { ...empty(), key, agent: step.agent, label: agentLabel(step.agent, step.node), steps: [] };
      node.agents.push(agent);
    }
    agent.steps.push(step);
    add(agent, step);
    add(node, step);
    node.steps += 1;
    node.hasError ||= isErrorStep(step);
  }
  return [...nodes.values()];
}

/** "30 LLM calls, 12 tool calls, 2 notes". */
export function kindSummary(t: Totals): string {
  const part = (n: number, one: string, many: string) => (n ? `${n} ${n === 1 ? one : many}` : null);
  return [part(t.llm, "LLM call", "LLM calls"), part(t.tool, "tool call", "tool calls"), part(t.note, "note", "notes")]
    .filter(Boolean)
    .join(", ");
}
