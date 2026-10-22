import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import { api, ApiError, errorDetail, type RunOut } from "@/lib/api/client";

export const RUNS_POLL_MS = 3000;
const RUNS_KEY = ["runs"] as const;

/**
 * Whether a run is genuinely in progress. The API reports `active` only while a worker holds the
 * ingestion lock, so a "running" row left behind by a stopped worker reads as interrupted, not live.
 */
export function isRunActive(run: RunOut): boolean {
  return run.status === "running" && run.active;
}

/** A "running" row whose worker stopped before finishing; the next run records it as failed. */
export function isRunInterrupted(run: RunOut): boolean {
  return run.status === "running" && !run.active;
}

/** Poll while a run is genuinely going; otherwise the list only changes when someone starts one. */
export function runsRefetchInterval(runs: RunOut[] | undefined): number | false {
  return runs?.some(isRunActive) ? RUNS_POLL_MS : false;
}

function count(stats: Record<string, unknown>, key: string): number | null {
  const v = stats[key];
  return typeof v === "number" && Number.isFinite(v) && v >= 0 ? v : null;
}

/**
 * Share of screened documents that passed the prefilter, 0..1. Duplicates are dropped before
 * the prefilter runs, so the base is passed + rejected, not documents created. Null when the
 * run didn't record both counts or screened nothing.
 */
export function prefilterPassRate(stats: Record<string, unknown>): number | null {
  const passed = count(stats, "prefilter_passed");
  const rejected = count(stats, "prefilter_rejected");
  if (passed === null || rejected === null || passed + rejected === 0) return null;
  return passed / (passed + rejected);
}

/** How a run started, in words (both run pages). */
export function triggerLabel(trigger: string) {
  if (trigger === "schedule") return "Scheduled";
  if (trigger === "manual") return "Manual";
  if (trigger === "cli") return "Command line";
  return trigger.charAt(0).toUpperCase() + trigger.slice(1);
}

async function fetchRuns(signal: AbortSignal): Promise<RunOut[]> {
  const { data, response } = await api.GET("/api/v1/runs", { params: { query: { limit: 50 } }, signal });
  if (!data) throw new ApiError(response.status);
  return data;
}

/** How long to keep polling for a run that was queued but hasn't been recorded yet. */
export const AWAIT_START_MS = 30_000;

/** Whether the run queued at `queuedAt` (epoch ms) has appeared in `runs` yet. */
export function isRecorded(runs: RunOut[] | undefined, queuedAt: number): boolean {
  // Allow for clock skew between this browser and the API host.
  return runs?.some((r) => Date.parse(r.started_at) >= queuedAt - 10_000) ?? false;
}

/**
 * Recent runs, newest first. Refetches every 3 s while one is genuinely running, and also for a short
 * while after `queuedAt` (a run was just queued but the worker hasn't recorded it yet).
 */
export function useRuns({ queuedAt = null }: { queuedAt?: number | null } = {}) {
  return useQuery({
    queryKey: RUNS_KEY,
    queryFn: ({ signal }) => fetchRuns(signal),
    refetchInterval: (query) => {
      const runs = query.state.data;
      const awaiting = queuedAt !== null && Date.now() - queuedAt < AWAIT_START_MS && !isRecorded(runs, queuedAt);
      return awaiting ? RUNS_POLL_MS : runsRefetchInterval(runs);
    },
  });
}

/**
 * When the next manual run opens up (epoch ms), from the API's own cooldown rule; null when a run
 * can start now. `lastFinished` keys the query so it refetches whenever a run finishes.
 */
export function useNextManualRunAt(lastFinished: string | null, enabled: boolean): number | null {
  const query = useQuery({
    queryKey: [...RUNS_KEY, "cooldown", lastFinished],
    enabled,
    queryFn: async ({ signal }) => {
      const { data, response } = await api.GET("/api/v1/runs/cooldown", { signal });
      if (!data) throw new ApiError(response.status);
      return data;
    },
  });
  const iso = query.data?.next_manual_run_at;
  return iso ? Date.parse(iso) : null;
}

/** The finish time of the newest succeeded run, if any. */
export function lastSucceededFinish(runs: RunOut[] | undefined): string | null {
  return runs?.find((r) => r.status === "succeeded" && r.finished_at)?.finished_at ?? null;
}

/** Where a run is started from: the toasts describe that page. */
const START_COPY = {
  runs: {
    started: "It shows up below within a few seconds and updates as it goes.",
    busy: "It's listed below and updates live. Start another once it finishes.",
  },
  briefing: {
    started: "The briefing appears here when the run files it, in a few minutes. Follow it from the top bar or Agent runs.",
    busy: "The briefing appears here when it's filed. Follow it from the top bar or Agent runs.",
  },
} as const;

/** Queue a full daily run (ingest, then the AI pipeline when a key is set). A 409 means one
 * is already going: say so calmly, it isn't a failure. `from` names the page it starts on. */
export function useStartRun({ from = "runs" }: { from?: keyof typeof START_COPY } = {}) {
  const queryClient = useQueryClient();
  const copy = START_COPY[from];
  return useMutation({
    mutationFn: async () => {
      const { data, error, response } = await api.POST("/api/v1/runs", { body: { mode: "full" } });
      if (!data) throw new ApiError(response.status, errorDetail(error) ?? undefined);
      return data;
    },
    onSuccess: () => {
      toast.success("Run started", { description: copy.started });
      void queryClient.invalidateQueries({ queryKey: RUNS_KEY });
    },
    onError: (error) => {
      if (error instanceof ApiError && error.status === 429) {
        // Cooldown after a recent run: the API's message says when the next one opens up.
        toast.info("Too soon for another run", {
          description: error.message || "A run finished recently. Try again a little later.",
        });
        return;
      }
      if (error instanceof ApiError && error.status === 409) {
        toast.info("A run is already in progress", { description: copy.busy });
        void queryClient.invalidateQueries({ queryKey: RUNS_KEY });
        return;
      }
      const forbidden = error instanceof ApiError && error.status === 403;
      toast.error("Couldn't start a run", {
        description: forbidden ? "Only CI analysts can start runs." : "FrogEye's API didn't answer. Check your connection and try again.",
      });
    },
  });
}
