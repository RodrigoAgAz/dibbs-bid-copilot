/**
 * Typed API client generated from the FastAPI OpenAPI schema (openapi-typescript + openapi-fetch).
 * Regenerate types with `npm run gen:api` after backend changes (see `make api-types`).
 *
 * - Same-origin requests (Next.js rewrites /api/* to the backend), cookies are httpOnly.
 * - Mutating requests carry the double-submit CSRF header.
 * - A 401 triggers one refresh-token rotation and a retry; if that fails we go to /login.
 */
import createClient from "openapi-fetch";
import type { components, paths } from "./schema";

export type Schemas = components["schemas"];

export function readCookie(name: string): string | undefined {
  if (typeof document === "undefined") return undefined;
  const m = document.cookie.split("; ").find((c) => c.startsWith(`${name}=`));
  return m ? decodeURIComponent(m.split("=").slice(1).join("=")) : undefined;
}

const MUTATING = new Set(["POST", "PUT", "PATCH", "DELETE"]);
const NO_REFRESH = ["/api/auth/login", "/api/auth/signup", "/api/auth/refresh", "/api/auth/logout"];

function withCsrf(req: Request): Request {
  if (!MUTATING.has(req.method)) return req;
  const token = readCookie("csrf_token");
  if (!token) return req;
  const headers = new Headers(req.headers);
  headers.set("x-csrf-token", token);
  return new Request(req, { headers });
}

let refreshing: Promise<boolean> | null = null;

async function tryRefresh(): Promise<boolean> {
  if (!refreshing) {
    refreshing = fetch("/api/auth/refresh", {
      method: "POST",
      credentials: "same-origin",
      headers: { "x-csrf-token": readCookie("csrf_token") ?? "" },
    })
      .then((r) => r.ok)
      .catch(() => false)
      .finally(() => {
        setTimeout(() => (refreshing = null), 0);
      });
  }
  return refreshing;
}

export async function authFetch(input: Request): Promise<Response> {
  const retry = input.clone();
  const res = await fetch(withCsrf(input));
  const path = new URL(input.url, "http://x").pathname;
  if (res.status !== 401 || NO_REFRESH.some((p) => path.startsWith(p))) return res;
  if (await tryRefresh()) return fetch(withCsrf(retry));
  if (typeof window !== "undefined" && !window.location.pathname.startsWith("/login")) {
    window.location.href = `/login?next=${encodeURIComponent(window.location.pathname)}`;
  }
  return res;
}

export const api = createClient<paths>({
  baseUrl: typeof window === "undefined" ? "http://localhost:3000" : window.location.origin,
  fetch: authFetch,
  credentials: "same-origin",
});

export class ApiError extends Error {
  status: number;
  constructor(status: number, message: string) {
    super(message);
    this.status = status;
  }
}

export function errorMessage(error: unknown): string {
  if (!error) return "Something went wrong";
  if (typeof error === "string") return error;
  const detail = (error as { detail?: unknown }).detail;
  if (typeof detail === "string") return detail;
  if (Array.isArray(detail) && detail.length) {
    return detail
      .map((d: { loc?: (string | number)[]; msg?: string }) => `${(d.loc ?? []).slice(1).join(".")}: ${d.msg}`)
      .join("; ");
  }
  if (error instanceof Error) return error.message;
  return "Something went wrong";
}

/** Unwrap an openapi-fetch result, throwing ApiError on failure (for TanStack Query). */
export function unwrap<T>(r: { data?: T; error?: unknown; response: Response }): T {
  if (r.error !== undefined || !r.response.ok) {
    throw new ApiError(r.response.status, errorMessage(r.error));
  }
  return r.data as T;
}
