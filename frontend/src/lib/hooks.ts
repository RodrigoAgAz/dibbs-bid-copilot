"use client";

import { useCallback, useSyncExternalStore } from "react";
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

const MATHS_KEY = "show-maths";
const mathsListeners = new Set<() => void>();
let mathsSession: boolean | null = null; // this page view's choice, for when storage is blocked

function readMaths(): boolean {
  if (mathsSession !== null) return mathsSession;
  try {
    return window.localStorage.getItem(MATHS_KEY) === "1";
  } catch {
    return false;
  }
}

/** "Show the maths": the simple view hides the model's working (triage, competitors, sensitivity, the book's limits).
 * One switch in the header, remembered per browser. */
export function useShowMaths(): [boolean, (on: boolean) => void] {
  const on = useSyncExternalStore(
    (cb) => {
      mathsListeners.add(cb);
      return () => mathsListeners.delete(cb);
    },
    readMaths,
    () => false,
  );
  const set = useCallback((v: boolean) => {
    mathsSession = v;
    try {
      window.localStorage.setItem(MATHS_KEY, v ? "1" : "0");
    } catch {
      // storage blocked: the choice holds for this page view
    }
    mathsListeners.forEach((l) => l());
  }, []);
  return [on, set];
}
