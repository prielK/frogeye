import { QueryClientProvider } from "@tanstack/react-query";
import { act, renderHook, waitFor } from "@testing-library/react";
import { http, HttpResponse } from "msw";
import { createElement, type ReactNode } from "react";
import { MemoryRouter } from "react-router";
import type { Schemas } from "@/lib/api/client";
import { createQueryClient } from "@/lib/query";
import { agentRun, run } from "@/test/fixtures";
import { MockEventSource, server } from "@/test/msw";
import { EVENTS_URL, RECONNECT_BASE_MS, RUN_STAGES, SEED_MAX_AGE_MS, subscribeEvents, useEvents } from "./events";

function wrapperFor(queryClient = createQueryClient({ retry: false })) {
  const wrapper = ({ children }: { children: ReactNode }) =>
    createElement(QueryClientProvider, { client: queryClient }, createElement(MemoryRouter, null, children));
  return { queryClient, wrapper };
}

afterEach(() => {
  vi.useRealTimers();
});

it("shares one EventSource between subscribers and closes it with the last one", () => {
  const first = subscribeEvents(() => {});
  const second = subscribeEvents(() => {});
  expect(MockEventSource.instances).toHaveLength(1);
  expect(MockEventSource.instances[0]!.url).toBe(EVENTS_URL);

  first();
  expect(MockEventSource.open).toHaveLength(1);
  second();
  expect(MockEventSource.open).toHaveLength(0);
});

it("reconnects with exponential backoff and starts over after a healthy connection", () => {
  vi.useFakeTimers();
  const stop = subscribeEvents(() => {});

  MockEventSource.instances[0]!.fail();
  vi.advanceTimersByTime(RECONNECT_BASE_MS - 1);
  expect(MockEventSource.instances).toHaveLength(1);
  vi.advanceTimersByTime(1);
  expect(MockEventSource.instances).toHaveLength(2);

  MockEventSource.instances[1]!.fail(); // second failure in a row: twice the wait
  vi.advanceTimersByTime(2 * RECONNECT_BASE_MS - 1);
  expect(MockEventSource.instances).toHaveLength(2);
  vi.advanceTimersByTime(1);
  expect(MockEventSource.instances).toHaveLength(3);

  MockEventSource.instances[2]!.accept(); // healthy again: the next drop waits the base delay
  MockEventSource.instances[2]!.fail();
  vi.advanceTimersByTime(RECONNECT_BASE_MS);
  expect(MockEventSource.instances).toHaveLength(4);

  stop();
  MockEventSource.instances[3]!.fail();
  vi.advanceTimersByTime(60_000);
  expect(MockEventSource.instances).toHaveLength(4);
});

it("delivers each frame's {type, payload} to subscribers", () => {
  const seen: unknown[] = [];
  const stop = subscribeEvents((message) => seen.push([message.type, message.payload]));
  MockEventSource.emit("briefing.published", { date: "2026-10-18", status: "published" });
  expect(seen).toEqual([["briefing.published", { date: "2026-10-18", status: "published" }]]);
  stop();
});

it("refreshes briefings when one is published", () => {
  const { queryClient, wrapper } = wrapperFor();
  queryClient.setQueryData(["briefings", "latest", "sales"], { id: 1 });
  queryClient.setQueryData(["briefings", "list", "sales"], []);
  queryClient.setQueryData(["documents", {}], { pages: [] });
  renderHook(() => useEvents(), { wrapper });

  act(() => MockEventSource.emit("briefing.published", { date: "2026-10-18", status: "published" }));

  expect(queryClient.getQueryState(["briefings", "latest", "sales"])?.isInvalidated).toBe(true);
  expect(queryClient.getQueryState(["briefings", "list", "sales"])?.isInvalidated).toBe(true);
  expect(queryClient.getQueryState(["documents", {}])?.isInvalidated).toBe(false);
});

it("folds run progress into the runs and agent-runs caches and tracks the live run", () => {
  const { queryClient, wrapper } = wrapperFor();
  queryClient.setQueryData<Schemas["RunOut"][]>(["runs"], [run({ id: 12, status: "running", active: true, stats: {} })]);
  queryClient.setQueryData<Schemas["AgentRunOut"][]>(
    ["agent-runs", { limit: 5 }],
    [agentRun({ id: 77, status: "running", finished_at: null, funnel: null })],
  );
  const { result } = renderHook(() => useEvents(), { wrapper });
  expect(result.current.liveRun).toBeNull();

  act(() =>
    MockEventSource.emit("run.progress", {
      pipeline_run_id: 12,
      agent_run_id: 77,
      stage: "triage",
      counts: { fetched: 412, relevant: 61, briefing_status: null },
    }),
  );

  const runs = queryClient.getQueryData<Schemas["RunOut"][]>(["runs"])!;
  expect(runs[0]!.stats).toMatchObject({ fetched: 412, relevant: 61 });
  const agentRuns = queryClient.getQueryData<Schemas["AgentRunOut"][]>(["agent-runs", { limit: 5 }])!;
  expect(agentRuns[0]!.funnel).toMatchObject({ fetched: 412, relevant: 61 });
  expect(result.current.liveRun).toMatchObject({ agentRunId: 77, stage: "triage" });

  // The last stage ends the live run; the finished rows come from the API.
  act(() =>
    MockEventSource.emit("run.progress", { pipeline_run_id: 12, agent_run_id: 77, stage: "publish", counts: {} }),
  );
  expect(result.current.liveRun).toBeNull();
  expect(queryClient.getQueryState(["runs"])?.isInvalidated).toBe(true);
  expect(queryClient.getQueryState(["agent-runs", { limit: 5 }])?.isInvalidated).toBe(true);
});

it("names the full-analysis stage while it runs, and counts it among the steps", () => {
  const { wrapper } = wrapperFor();
  const { result } = renderHook(() => useEvents(), { wrapper });
  const progress = (stage: string) =>
    act(() => MockEventSource.emit("run.progress", { pipeline_run_id: 12, agent_run_id: 77, stage, counts: {} }));

  expect(RUN_STAGES).toHaveLength(12);
  progress("score");
  expect(result.current.liveRun).toMatchObject({ activity: "Writing full analyses", step: 9 });
  progress("deep_analyze");
  expect(result.current.liveRun).toMatchObject({ activity: "Routing alerts", step: 10 });
  progress("route");
  expect(result.current.liveRun).toMatchObject({ activity: "Writing the briefing", step: 11 });
});

it("refreshes the signals list and the AI spend when a run finishes", () => {
  const { queryClient, wrapper } = wrapperFor();
  queryClient.setQueryData(["signals", "list", {}], { pages: [] });
  queryClient.setQueryData(["signals", "detail", 101], { id: 101 });
  queryClient.setQueryData(["costs"], { today_usd: 0.1 });
  renderHook(() => useEvents(), { wrapper });

  act(() => MockEventSource.emit("run.progress", { pipeline_run_id: 12, agent_run_id: 77, stage: "score", counts: {} }));
  expect(queryClient.getQueryState(["signals", "list", {}])?.isInvalidated).toBe(false);
  expect(queryClient.getQueryState(["costs"])?.isInvalidated).toBe(false);

  act(() => MockEventSource.emit("run.progress", { pipeline_run_id: 12, agent_run_id: 77, stage: "publish", counts: {} }));
  expect(queryClient.getQueryState(["signals", "list", {}])?.isInvalidated).toBe(true);
  expect(queryClient.getQueryState(["signals", "detail", 101])?.isInvalidated).toBe(true);
  expect(queryClient.getQueryState(["costs"])?.isInvalidated).toBe(true);
});

it("refreshes the open intelligence pages when a run finishes", () => {
  const { queryClient, wrapper } = wrapperFor();
  const keys = [["competitor-overview"], ["competitor-profile", "sonatype"], ["products"], ["product", "artifactory"], ["intel-reference"]];
  for (const key of keys) queryClient.setQueryData(key, {});
  renderHook(() => useEvents(), { wrapper });
  act(() => MockEventSource.emit("run.progress", { pipeline_run_id: 12, agent_run_id: 77, stage: "score", counts: {} }));
  for (const key of keys) expect(queryClient.getQueryState(key)?.isInvalidated, key.join("/")).toBe(false);
  act(() => MockEventSource.emit("run.progress", { pipeline_run_id: 12, agent_run_id: 77, stage: "publish", counts: {} }));
  for (const key of keys) expect(queryClient.getQueryState(key)?.isInvalidated, key.join("/")).toBe(true);
});

it("also refreshes them when a polled run stops running without a final event", async () => {
  const live = agentRun({ id: 81, status: "running", started_at: new Date(Date.now() - 60_000).toISOString(), finished_at: null });
  let done = false;
  server.use(http.get("/api/v1/agent-runs", () => HttpResponse.json([done ? { ...live, status: "failed" } : live])));
  const { queryClient, wrapper } = wrapperFor();
  queryClient.setQueryData(["costs"], { today_usd: 0.1 });
  queryClient.setQueryData(["signals", "list", {}], { pages: [] });
  const { result } = renderHook(() => useEvents(), { wrapper });
  await waitFor(() => expect(result.current.liveRun).toMatchObject({ agentRunId: 81 }));

  done = true;
  await act(() => queryClient.refetchQueries({ queryKey: ["agent-runs", { limit: 1 }] }));
  await waitFor(() => expect(result.current.liveRun).toBeNull());
  expect(queryClient.getQueryState(["costs"])?.isInvalidated).toBe(true);
  expect(queryClient.getQueryState(["signals", "list", {}])?.isInvalidated).toBe(true);
});

it("refetches runs it has not seen yet", () => {
  const { queryClient, wrapper } = wrapperFor();
  queryClient.setQueryData<Schemas["RunOut"][]>(["runs"], [run({ id: 11 })]);
  renderHook(() => useEvents(), { wrapper });

  act(() =>
    MockEventSource.emit("run.progress", { pipeline_run_id: 12, agent_run_id: 78, stage: "ingest", counts: { fetched: 3 } }),
  );

  expect(queryClient.getQueryState(["runs"])?.isInvalidated).toBe(true);
  expect(queryClient.getQueryData<Schemas["RunOut"][]>(["runs"])![0]!.id).toBe(11);
});

describe("the live run on load", () => {
  function latestRun(run: Schemas["AgentRunOut"], limits: string[] = []) {
    server.use(
      http.get("/api/v1/agent-runs", ({ request }) => {
        limits.push(new URL(request.url).searchParams.get("limit") ?? "");
        return HttpResponse.json([run]);
      }),
    );
  }

  it("seeds from the latest agent run while it is running, then follows the stream", async () => {
    const startedAt = new Date(Date.now() - 4 * 60_000).toISOString();
    const limits: string[] = [];
    latestRun(agentRun({ id: 81, status: "running", started_at: startedAt, finished_at: null, funnel: { fetched: 412 } }), limits);
    const { wrapper } = wrapperFor();
    const { result } = renderHook(() => useEvents(), { wrapper });

    await waitFor(() => expect(result.current.liveRun).toMatchObject({ agentRunId: 81, step: 0, startedAt, counts: { fetched: 412 } }));
    expect(limits).toEqual(["1"]);

    act(() => MockEventSource.emit("run.progress", { pipeline_run_id: 12, agent_run_id: 81, stage: "triage", counts: {} }));
    expect(result.current.liveRun).toMatchObject({ agentRunId: 81, stage: "triage", step: 4, startedAt, counts: { fetched: 412 } });
  });

  it("does not seed from a finished run, or from a running row a dead worker left behind", async () => {
    const stale = new Date(Date.now() - SEED_MAX_AGE_MS - 60_000).toISOString();
    for (const run of [agentRun({ id: 82, status: "succeeded" }), agentRun({ id: 83, status: "running", started_at: stale, finished_at: null })]) {
      const { queryClient, wrapper } = wrapperFor();
      latestRun(run);
      const { result, unmount } = renderHook(() => useEvents(), { wrapper });
      await waitFor(() => expect(queryClient.getQueryData(["agent-runs", { limit: 1 }])).toEqual([run]));
      await act(() => new Promise((resolve) => setTimeout(resolve, 20))); // let any seeding effect run
      expect(result.current.liveRun).toBeNull();
      unmount();
    }
  });
});
