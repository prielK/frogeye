import { QueryClient } from "@tanstack/react-query";
import { ApiError } from "@/lib/api/client";

/** A 4xx answer (signed out, forbidden, bad cursor) won't change on retry; only transient failures retry. */
function isPermanent(error: unknown) {
  return error instanceof ApiError && error.status >= 400 && error.status < 500;
}

export function createQueryClient({ retry = 1 }: { retry?: number | false } = {}) {
  return new QueryClient({
    defaultOptions: {
      queries: {
        retry: (failures, error) => retry !== false && !isPermanent(error) && failures < retry,
        staleTime: 30_000,
        refetchOnWindowFocus: false,
      },
      mutations: { retry: false },
    },
  });
}
