import { useQuery, useQueryClient } from "@tanstack/react-query";
import { createContext, useCallback, useContext, useEffect, useMemo, type ReactNode } from "react";
import { Navigate, useLocation } from "react-router";
import { ShellSkeleton } from "@/components/layout/ShellSkeleton";
import { api, ApiError, setUnauthorizedHandler, type UserOut } from "@/lib/api/client";
import { setAppTimeZone } from "@/lib/time";

type AuthValue = {
  user: UserOut | null;
  login(email: string, password: string): Promise<void>;
  logout(): Promise<void>;
  loading: boolean;
};

const ME_KEY = ["auth", "me"] as const;

function isMeKey(key: readonly unknown[]) {
  return key.length === ME_KEY.length && key.every((part, i) => part === ME_KEY[i]);
}

const AuthContext = createContext<AuthValue | null>(null);

async function fetchMe(): Promise<UserOut | null> {
  const { data, response } = await api.GET("/api/v1/auth/me");
  if (response.status === 401) return null;
  if (!data) throw new ApiError(response.status);
  // dates and times show in the app's zone (the backend's TIMEZONE), set before any renders.
  setAppTimeZone(data.timezone);
  return data;
}

export function AuthProvider({ children }: { children: ReactNode }) {
  const queryClient = useQueryClient();
  const me = useQuery({ queryKey: ME_KEY, queryFn: fetchMe, staleTime: Infinity, retry: false });

  // The session can expire while a page is open: any 401 marks the reader signed out on the
  // live /me query (same pattern as logout), so <RequireAuth> sends them to /login and keeps
  // their place for the return trip.
  useEffect(
    () =>
      setUnauthorizedHandler(() => {
        if (queryClient.getQueryData(ME_KEY)) queryClient.setQueryData(ME_KEY, null);
      }),
    [queryClient],
  );

  const login = useCallback(
    async (email: string, password: string) => {
      const { data, response } = await api.POST("/api/v1/auth/login", { body: { email, password } });
      if (!data) throw new ApiError(response.status);
      setAppTimeZone(data.timezone);
      // Nothing cached for a previous reader (or an expired session) carries over.
      queryClient.removeQueries({ predicate: (query) => !isMeKey(query.queryKey) });
      queryClient.setQueryData(ME_KEY, data);
    },
    [queryClient],
  );

  const logout = useCallback(async () => {
    const { response } = await api.POST("/api/v1/auth/logout");
    if (!response.ok) throw new ApiError(response.status);
    // Mark the session signed out on the live /me query first so its observer (this provider)
    // re-renders. queryClient.clear() would detach that observer and strand the old user.
    queryClient.setQueryData(ME_KEY, null);
    // Then drop everything else cached for the previous reader.
    queryClient.removeQueries({ predicate: (query) => !isMeKey(query.queryKey) });
  }, [queryClient]);

  const value = useMemo<AuthValue>(
    () => ({ user: me.data ?? null, loading: me.isPending, login, logout }),
    [me.data, me.isPending, login, logout],
  );

  return <AuthContext value={value}>{children}</AuthContext>;
}

export function useAuth(): AuthValue {
  const value = useContext(AuthContext);
  if (!value) throw new Error("useAuth must be used inside <AuthProvider>");
  return value;
}

/** Route guard: renders children for a signed-in reader, otherwise sends them to /login. */
export function RequireAuth({ children }: { children: ReactNode }) {
  const { user, loading } = useAuth();
  const location = useLocation();
  if (loading) return <ShellSkeleton />;
  if (!user) return <Navigate to="/login" replace state={{ from: location }} />;
  return children;
}
