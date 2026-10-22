import { useQuery, useQueryClient, type QueryClient } from "@tanstack/react-query";
import { ShieldAlertIcon, TrendingUpIcon, ZapIcon } from "lucide-react";
import { createElement, useEffect, useEffectEvent, useRef, useState, useSyncExternalStore } from "react";
import { useLocation, useNavigate } from "react-router";
import { toast } from "sonner";
// An accepted inversion: the stream's breaking toast opens the shared signal drawer
// and warms its query, both owned by components/signal; nothing there imports lib/events.
import { openSignalAt } from "@/components/signal/param";
import { signalQuery } from "@/components/signal/query";
import type { Schemas } from "@/lib/api/client";
import { agentRunsQuery } from "@/lib/api/queries";
import { formatCount } from "@/lib/format";

/*
 * Live events: the API's server-sent event stream (`GET /api/v1/events`), one connection
 * per app however many components listen. Frames are `event: <type>` with `data` = the bus
 * message `{type, payload, ts}`; the server pings every 15 s and keeps the stream up
 * through Redis outages, so the client only reconnects after the HTTP stream itself
 * drops or is refused, waiting 1 s, 2 s, 4 s ... up to 30 s between attempts.
 */

export const EVENTS_URL = "/api/v1/events";
export const RECONNECT_BASE_MS = 1_000;
export const RECONNECT_MAX_MS = 30_000;

/** The event types the API publishes (see backend agents/nodes/publish.py, route.py, graphs/daily_intel.py). */
const TYPES = ["briefing.published", "run.progress", "signal.breaking"] as const;

export type EventMessage = { type: string; payload: unknown; ts: string | null };
type Listener = (message: EventMessage) => void;

/** `run.progress`: sent after each stage of the daily graph finishes. */
export type RunProgress = {
  pipeline_run_id: number | null;
  agent_run_id: number | null;
  stage: string;
  counts: Record<string, unknown>;
};

/** `signal.breaking`: a signal at or above the breaking threshold (importance 85). */
export type BreakingSignal = { signal_id: number; title: string; importance: number; competitors: string[] };

// ---- the shared connection ----------------------------------------------------------------

const listeners = new Set<Listener>();
let source: EventSource | null = null;
let retryTimer: ReturnType<typeof setTimeout> | null = null;
let failures = 0;
let droppedSinceOpen = false;

/** Wait before reconnect attempt `n` (0-based): 1 s doubling to a 30 s cap. */
export function reconnectDelay(attempt: number): number {
  return Math.min(RECONNECT_MAX_MS, RECONNECT_BASE_MS * 2 ** attempt);
}

function parse(type: string, data: unknown): EventMessage | null {
  if (typeof data !== "string") return null;
  try {
    const message = JSON.parse(data) as Partial<EventMessage> | null;
    if (!message || typeof message !== "object") return null;
    return { type, payload: message.payload ?? null, ts: typeof message.ts === "string" ? message.ts : null };
  } catch {
    return null;
  }
}

function deliver(message: EventMessage) {
  for (const listener of [...listeners]) {
    try {
      listener(message);
    } catch (error) {
      // One faulty listener must not starve the others or kill the stream.
      console.error("FrogEye events: a listener failed", error);
    }
  }
}

function connect() {
  if (source || typeof EventSource === "undefined") return;
  // Same origin, so the session cookie rides along without withCredentials.
  const es = new EventSource(EVENTS_URL);
  source = es;
  es.addEventListener("open", () => {
    failures = 0;
    if (droppedSinceOpen) {
      droppedSinceOpen = false;
      // Events sent while we were away are gone; say so to listeners so they can catch up.
      deliver({ type: "stream.reconnected", payload: null, ts: null });
    }
  });
  es.addEventListener("error", () => {
    // Close and retry on our own schedule: the browser's built-in retry gives up on HTTP
    // errors (a 502 from the proxy) and hammers on a fixed 3 s otherwise.
    if (source !== es) return;
    es.close();
    source = null;
    droppedSinceOpen = true;
    scheduleReconnect();
  });
  for (const type of TYPES) {
    es.addEventListener(type, (event) => {
      const message = parse(type, (event as MessageEvent).data);
      if (message) deliver(message);
    });
  }
}

function scheduleReconnect() {
  if (retryTimer !== null || listeners.size === 0) return;
  const delay = reconnectDelay(failures);
  failures += 1;
  retryTimer = setTimeout(() => {
    retryTimer = null;
    if (listeners.size > 0) connect();
  }, delay);
}

function disconnect() {
  if (retryTimer !== null) clearTimeout(retryTimer);
  retryTimer = null;
  source?.close();
  source = null;
  failures = 0;
  droppedSinceOpen = false;
  progressSeen = false;
  setLiveRun(null);
}

/** Listen to the shared stream; opens it on the first listener, closes it after the last. */
export function subscribeEvents(listener: Listener): () => void {
  listeners.add(listener);
  connect();
  return () => {
    listeners.delete(listener);
    if (listeners.size === 0) disconnect();
  };
}

/** Run `listener` for every live event while the calling component is mounted. */
export function useEventStream(listener: Listener) {
  const onMessage = useEffectEvent(listener);
  useEffect(() => subscribeEvents((message) => onMessage(message)), []);
}

// ---- the live run (fed by run.progress) ---------------------------------------------------

/** The daily graph's stages, in order. A progress event names the stage that just finished. */
export const RUN_STAGES = [
  { key: "ingest", label: "Collecting sources" },
  { key: "index_new", label: "Indexing documents" },
  { key: "triage", label: "Triaging documents" },
  { key: "disambiguate", label: "Checking company names" },
  { key: "apply_triage", label: "Filing triage results" },
  { key: "cluster", label: "Grouping into stories" },
  { key: "analyze", label: "Analysing signals" },
  { key: "score", label: "Scoring signals" },
  { key: "deep_analyze", label: "Writing full analyses" },
  { key: "route", label: "Routing alerts" },
  { key: "newsroom", label: "Writing the briefing" },
  { key: "publish", label: "Publishing" },
] as const;
const LAST_STAGE = RUN_STAGES[RUN_STAGES.length - 1]!.key;

export type LiveRun = {
  pipelineRunId: number | null;
  agentRunId: number | null;
  /** The stage that last finished. */
  stage: string;
  /** 1-based position of the stage now running, out of RUN_STAGES.length; 0 while unknown (seeded on load). */
  step: number;
  /** What the run is doing now, e.g. "Writing the briefing". */
  activity: string;
  counts: Record<string, number>;
  updatedAt: number;
  startedAt: string | null;
};

let liveRun: LiveRun | null = null;
/** Whether run.progress arrived since the stream opened: from then on SSE, not the seed, is the truth. */
let progressSeen = false;
const liveListeners = new Set<() => void>();

function setLiveRun(next: LiveRun | null) {
  if (next === liveRun) return;
  liveRun = next;
  for (const l of liveListeners) l();
}

function subscribeLive(listener: () => void) {
  liveListeners.add(listener);
  return () => liveListeners.delete(listener);
}

function numericCounts(counts: Record<string, unknown>): Record<string, number> {
  return Object.fromEntries(
    Object.entries(counts).filter((entry): entry is [string, number] => typeof entry[1] === "number" && Number.isFinite(entry[1])),
  );
}

function asProgress(payload: unknown): RunProgress | null {
  if (!payload || typeof payload !== "object") return null;
  const p = payload as Partial<RunProgress>;
  if (typeof p.stage !== "string") return null;
  const id = (v: unknown) => (typeof v === "number" ? v : null);
  return {
    pipeline_run_id: id(p.pipeline_run_id),
    agent_run_id: id(p.agent_run_id),
    stage: p.stage,
    counts: p.counts && typeof p.counts === "object" ? p.counts : {},
  };
}

function asBreaking(payload: unknown): BreakingSignal | null {
  if (!payload || typeof payload !== "object") return null;
  const p = payload as Partial<BreakingSignal>;
  if (typeof p.signal_id !== "number" || typeof p.title !== "string") return null;
  return {
    signal_id: p.signal_id,
    title: p.title,
    importance: typeof p.importance === "number" ? p.importance : 0,
    competitors: Array.isArray(p.competitors) ? p.competitors.filter((c): c is string => typeof c === "string") : [],
  };
}

function nextLiveRun(progress: RunProgress, previous: LiveRun | null): LiveRun | null {
  if (progress.stage === LAST_STAGE) return null;
  const index = RUN_STAGES.findIndex((s) => s.key === progress.stage);
  const running = RUN_STAGES[Math.min(index + 1, RUN_STAGES.length - 1)]!;
  const sameRun = previous && previous.agentRunId === progress.agent_run_id;
  return {
    pipelineRunId: progress.pipeline_run_id,
    agentRunId: progress.agent_run_id,
    stage: progress.stage,
    step: index + 2,
    activity: index >= 0 ? running.label : "Working",
    counts: { ...(sameRun ? previous.counts : {}), ...numericCounts(progress.counts) },
    updatedAt: Date.now(),
    startedAt: sameRun ? previous.startedAt : null,
  };
}

/** A "running" row older than this is a dead worker's; the runner resumes nothing older. */
export const SEED_MAX_AGE_MS = 6 * 60 * 60_000;

/**
 * a reload mid-run still shows the pill. Until the stream reports progress, a running and
 * recent latest agent run seeds the live run, its stage unknown (step 0) until the next event.
 * The same read retires the live run once that run is no longer running.
 */
export function seedLiveRun(run: AgentRunOut | undefined, now = Date.now()) {
  // The live run ended without a final event (it died, or the event raced the finish): stand down.
  if (liveRun && run?.id === liveRun.agentRunId && run.status !== "running") return setLiveRun(null);
  if (!run || run.status !== "running" || liveRun || progressSeen || now - Date.parse(run.started_at) > SEED_MAX_AGE_MS) return;
  const counts = numericCounts(run.funnel ?? {});
  setLiveRun({ pipelineRunId: run.pipeline_run_id, agentRunId: run.id, stage: "", step: 0, activity: "In progress", counts, updatedAt: now, startedAt: run.started_at });
}

// ---- cache effects --------------------------------------------------------------------------

type RunOut = Schemas["RunOut"];
type AgentRunOut = Schemas["AgentRunOut"];

/** Merge a progress event into cached pipeline runs (["runs"]); refetch when the run isn't cached yet. */
function applyToRuns(queryClient: QueryClient, progress: RunProgress, finished: boolean) {
  const cached = queryClient.getQueryData<RunOut[]>(["runs"]);
  const known = cached?.some((r) => r.id === progress.pipeline_run_id) ?? false;
  if (finished || !known) {
    void queryClient.invalidateQueries({ queryKey: ["runs"], exact: true });
    return;
  }
  const counts = numericCounts(progress.counts);
  queryClient.setQueryData<RunOut[]>(["runs"], (runs) =>
    runs?.map((r) =>
      r.id === progress.pipeline_run_id ? { ...r, status: "running", active: true, stats: { ...r.stats, ...counts } } : r,
    ),
  );
}

/** The same for agent runs: every cached ["agent-runs", ...] list or detail holding this run. */
function applyToAgentRuns(queryClient: QueryClient, progress: RunProgress, finished: boolean) {
  const counts = numericCounts(progress.counts);
  const update = (r: AgentRunOut): AgentRunOut =>
    r.id === progress.agent_run_id ? { ...r, status: "running", funnel: { ...(r.funnel ?? {}), ...counts } } : r;
  let known = false;
  for (const [key, data] of queryClient.getQueriesData<AgentRunOut[] | AgentRunOut>({ queryKey: ["agent-runs"] })) {
    if (Array.isArray(data)) {
      if (!data.some((r) => r.id === progress.agent_run_id)) continue;
      known = true;
      if (!finished) queryClient.setQueryData<AgentRunOut[]>(key, data.map(update));
    } else if (data && typeof data === "object" && "id" in data && data.id === progress.agent_run_id) {
      known = true;
      if (!finished) queryClient.setQueryData<AgentRunOut>(key, update(data));
    }
  }
  if (finished || !known) void queryClient.invalidateQueries({ queryKey: ["agent-runs"] });
}

/** The intelligence pages' reads a run changes: the lists, the deep dives and the rules (the collected-since day). */
const INTEL_KEYS = [["competitor-overview"], ["competitor-profile"], ["products"], ["product"], ["intel-reference"]] as const;

/**
 * A run just finished: what it changed is stale. The Signals list and every open signal (it
 * re-scored them), the AI spend (the Today meter and the week chart) and the open
 * intelligence pages; briefings refresh on briefing.published.
 */
function refreshAfterRun(queryClient: QueryClient) {
  void queryClient.invalidateQueries({ queryKey: ["signals"] });
  void queryClient.invalidateQueries({ queryKey: ["costs"] });
  for (const queryKey of INTEL_KEYS) void queryClient.invalidateQueries({ queryKey });
}

async function stanceOf(queryClient: QueryClient, signalId: number): Promise<string | null> {
  // The payload carries no stance; the lookup also warms the drawer, so "Open" is instant.
  try {
    return (await queryClient.fetchQuery(signalQuery(signalId))).stance;
  } catch {
    return null;
  }
}

/**
 * Calm by default (DESIGN.md: urgency only where it is real): a neutral toast with the
 * signal's title, its companies and importance, and an Open action. Only a signal whose
 * stance is threat takes the threat icon, and still says so in words.
 */
function toastBreaking(signal: BreakingSignal, stance: string | null, open: (id: number) => void) {
  const look =
    stance === "threat"
      ? { label: "Threat", icon: ShieldAlertIcon, className: "text-threat" }
      : stance === "opportunity"
        ? { label: "Opportunity", icon: TrendingUpIcon, className: "text-opportunity" }
        : { label: "Breaking signal", icon: ZapIcon, className: "text-primary-ink" };
  toast(signal.title, {
    id: `breaking-${signal.signal_id}`,
    description: [look.label, signal.competitors.join(", "), `importance ${formatCount(signal.importance)}`]
      .filter(Boolean)
      .join(" · "),
    icon: createElement(look.icon, { "aria-hidden": true, className: `size-4 ${look.className}` }),
    duration: 12_000,
    action: { label: "Open", onClick: () => open(signal.signal_id) },
  });
}

/** Mounted useEvents instances, oldest first; only the oldest applies the effects. */
const owners: object[] = [];

function readLiveRun() {
  return liveRun;
}

/**
 * The app's live events, mounted once in the signed-in shell (TopBar). Opens the shared
 * stream and applies what it says: a published briefing refreshes briefings, run progress
 * updates the cached runs and the live run, a breaking signal raises a toast that opens
 * the signal drawer. Returns the live run for the run pill. If several components mount
 * it, only the first applies the effects; the stream stays one connection.
 */
export function useEvents(): { liveRun: LiveRun | null } {
  const queryClient = useQueryClient();
  const navigate = useNavigate();
  const location = useLocation();
  const [token] = useState(() => ({}));
  // A toast's Open may be clicked after the reader moved on: resolve the target at click time.
  const where = useRef(location);
  useEffect(() => {
    where.current = location;
  }, [location]);

  const handle = useEffectEvent((message: EventMessage) => {
    if (owners[0] !== token) return;
    switch (message.type) {
      case "briefing.published":
        void queryClient.invalidateQueries({ queryKey: ["briefings"] });
        break;
      case "stream.reconnected":
        // Anything published while the stream was down would otherwise be missed.
        void queryClient.invalidateQueries({ queryKey: ["briefings"] });
        break;
      case "run.progress": {
        const progress = asProgress(message.payload);
        if (!progress) break;
        const finished = progress.stage === LAST_STAGE;
        progressSeen = true;
        applyToRuns(queryClient, progress, finished);
        applyToAgentRuns(queryClient, progress, finished);
        if (finished) refreshAfterRun(queryClient);
        setLiveRun(nextLiveRun(progress, liveRun));
        break;
      }
      case "signal.breaking": {
        const signal = asBreaking(message.payload);
        if (!signal) break;
        // The app-level drawer opens over whatever page the reader is on by then.
        const open = (id: number) => openSignalAt(navigate, where.current, id);
        void stanceOf(queryClient, signal.signal_id).then((stance) => toastBreaking(signal, stance, open));
        break;
      }
    }
  });

  useEffect(() => {
    owners.push(token);
    const stop = subscribeEvents((message) => handle(message));
    return () => {
      stop();
      owners.splice(owners.indexOf(token), 1);
    };
  }, [token]);

  // seed the pill from the latest agent run (every role may list runs), then follow SSE;
  // while a run is live, poll it (3 s, as Runs does) so the pill clears even with no final event.
  const live = useSyncExternalStore(subscribeLive, readLiveRun, () => null);
  const latest = useQuery({ ...agentRunsQuery(1), refetchInterval: live ? 3_000 : false });
  useEffect(() => {
    if (owners[0] !== token) return;
    const wasLive = liveRun !== null;
    seedLiveRun(latest.data?.[0]);
    // The poll saw the run end without a final event (it died, or the event raced the commit).
    if (wasLive && liveRun === null) refreshAfterRun(queryClient);
  }, [latest.data, token, queryClient]);

  return { liveRun: live };
}
