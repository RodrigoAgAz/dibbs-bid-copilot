"use client";

import { useQuery } from "@tanstack/react-query";
import { api, unwrap } from "@/lib/api/client";

export function useMe() {
  return useQuery({
    queryKey: ["me"],
    queryFn: async () => unwrap(await api.GET("/api/auth/me")),
    staleTime: 5 * 60_000,
  });
}

/** True for read-only seats (role "viewer"), including the public demo account. */
export function useReadOnly(): boolean {
  const { data } = useMe();
  return data?.user.role === "viewer";
}

/** What the login page offers (public demo button). */
export function useAuthOptions() {
  return useQuery({
    queryKey: ["auth-options"],
    queryFn: async () => unwrap(await api.GET("/api/auth/options")),
    staleTime: 10 * 60_000,
  });
}

/** Public data freshness (pipelines, newest DIBBS posting, active model). */
export function useDataHealth() {
  return useQuery({
    queryKey: ["data-health"],
    queryFn: async () => unwrap(await api.GET("/api/health/data")),
    staleTime: 5 * 60_000,
    refetchInterval: 10 * 60_000,
  });
}
