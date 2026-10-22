import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import { api, ApiError, errorDetail, type Schemas, type SourceOut, type SourceTestOut } from "@/lib/api/client";

export type SourceFilters = {
  kind?: Schemas["SourceKind"];
  competitor?: string;
  health?: Schemas["SourceHealth"];
};

/** Kinds in the order an analyst thinks about them, with the names people use for them. */
export const KIND_LABELS: Record<string, string> = {
  rss: "RSS & Atom feeds",
  google_news: "Google News",
  gdelt: "GDELT",
  hn: "Hacker News",
  github_releases: "GitHub releases",
  github_repo_stats: "GitHub stats",
  sec_edgar: "SEC filings",
  page_watch: "Watched pages",
  job_board: "Job boards",
  oss_stats: "Open-source stats",
  tavily: "Web search",
};
const KIND_ORDER = Object.keys(KIND_LABELS);

export function kindLabel(kind: string): string {
  return KIND_LABELS[kind] ?? kind.replace(/_/g, " ");
}

export function kindRank(kind: string): number {
  const i = KIND_ORDER.indexOf(kind);
  return i === -1 ? KIND_ORDER.length : i;
}

/** Admiralty source-reliability grades. */
export const RELIABILITY: Record<string, string> = {
  A: "Completely reliable",
  B: "Usually reliable",
  C: "Fairly reliable",
  D: "Not usually reliable",
  E: "Unreliable",
  F: "Reliability can't be judged yet",
};

const SOURCES_KEY = ["sources"] as const;

async function fetchSources(filters: SourceFilters, signal: AbortSignal): Promise<SourceOut[]> {
  const { data, response } = await api.GET("/api/v1/sources", { params: { query: filters }, signal });
  if (!data) throw new ApiError(response.status);
  return data;
}

export function useSources(filters: SourceFilters = {}) {
  return useQuery({
    queryKey: [...SOURCES_KEY, filters],
    queryFn: ({ signal }) => fetchSources(filters, signal),
  });
}

type Snapshot = [readonly unknown[], SourceOut[] | undefined][];

function patchCached(queryClient: ReturnType<typeof useQueryClient>, id: number, patch: Partial<SourceOut>) {
  queryClient.setQueriesData<SourceOut[]>({ queryKey: SOURCES_KEY }, (list) =>
    list?.map((s) => (s.id === id ? { ...s, ...patch } : s)),
  );
}

/** Pause or resume a source. Optimistic: the switch moves at once and snaps back if the server refuses. */
export function useToggleSource() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async ({ id, enabled }: { id: number; enabled: boolean; name: string }) => {
      const { data, response } = await api.PATCH("/api/v1/sources/{source_id}", {
        params: { path: { source_id: id } },
        body: { enabled },
      });
      if (!data) throw new ApiError(response.status);
      return data;
    },
    onMutate: async ({ id, enabled }) => {
      await queryClient.cancelQueries({ queryKey: SOURCES_KEY });
      const snapshot: Snapshot = queryClient.getQueriesData<SourceOut[]>({ queryKey: SOURCES_KEY });
      patchCached(queryClient, id, { enabled });
      return { snapshot };
    },
    onError: (error, { enabled, name }, context) => {
      for (const [key, data] of context?.snapshot ?? []) queryClient.setQueryData(key, data);
      const forbidden = error instanceof ApiError && error.status === 403;
      toast.error(`Couldn't ${enabled ? "resume" : "pause"} ${name}`, {
        description: forbidden ? "Only CI analysts can change sources." : "Nothing changed. Check your connection and try again.",
      });
    },
    onSuccess: (source) => patchCached(queryClient, source.id, source),
  });
}

/** Dry-run a source's collector: nothing is stored and its health is untouched. */
export function useTestSource() {
  return useMutation({
    mutationFn: async (id: number): Promise<SourceTestOut> => {
      const { data, error, response } = await api.POST("/api/v1/sources/{source_id}/test", {
        params: { path: { source_id: id } },
      });
      // a board or page already read today is a 409 whose words the dialog shows.
      if (!data) throw new ApiError(response.status, errorDetail(error) ?? undefined);
      return data;
    },
  });
}
