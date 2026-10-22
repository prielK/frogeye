import { queryOptions } from "@tanstack/react-query";
import { api, ApiError } from "./client";

/*
 * Reads several features share, written once: the latest agent runs (the Briefing's
 * AI-off check, the top bar's live-run pill, the Agent runs page) and the AI spend (the
 * Briefing's skipped-day explanation, the AI spend panel). One key per read, one fetcher.
 */

export const agentRunsKey = (limit: number) => ["agent-runs", { limit }] as const;

/** The `limit` latest agent runs, newest first (`GET /agent-runs`, every role). */
export function agentRunsQuery(limit: number) {
  return queryOptions({
    queryKey: agentRunsKey(limit),
    queryFn: async ({ signal }) => {
      const { data, response } = await api.GET("/api/v1/agent-runs", { params: { query: { limit } }, signal });
      if (!data) throw new ApiError(response.status);
      return data;
    },
  });
}

/** AI spend against both caps (`GET /costs`, every role); the day is the server's local day. */
export function costsQuery() {
  return queryOptions({
    queryKey: ["costs"],
    queryFn: async ({ signal }) => {
      const { data, response } = await api.GET("/api/v1/costs", { signal });
      if (!data) throw new ApiError(response.status);
      return data;
    },
  });
}

/**
 * When the demo data this database started from was captured (`GET /demo-snapshot`, every
 * role); `captured_at` is null on a database that did not start from the bundled snapshot.
 * It changes only with a new database, so it is read once per session.
 */
export function demoSnapshotQuery() {
  return queryOptions({
    queryKey: ["demo-snapshot"],
    queryFn: async ({ signal }) => {
      const { data, response } = await api.GET("/api/v1/demo-snapshot", { signal });
      if (!data) throw new ApiError(response.status);
      return data;
    },
    staleTime: Infinity,
  });
}
