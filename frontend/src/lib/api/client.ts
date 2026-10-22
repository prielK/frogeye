import createClient from "openapi-fetch";
import type { components, paths } from "./schema";

/**
 * Typed client for the FrogEye API. Same origin; the session rides in an httpOnly cookie.
 * `fetch` is looked up per request (not captured at import) so instrumentation installed
 * later, such as MSW in tests, still sees every call.
 */
export const api = createClient<paths>({
  baseUrl: "",
  credentials: "include",
  fetch: (request: Request) => globalThis.fetch(request),
});

const LOGIN_PATH = "/api/v1/auth/login";
let onUnauthorized: (() => void) | null = null;

/**
 * Register what happens when the session expires mid-use (any 401 except a rejected
 * sign-in, which the login form reports itself). Returns an unregister function.
 */
export function setUnauthorizedHandler(handler: () => void): () => void {
  onUnauthorized = handler;
  return () => {
    if (onUnauthorized === handler) onUnauthorized = null;
  };
}

api.use({
  onResponse({ request, response }) {
    if (response.status === 401 && new URL(request.url).pathname !== LOGIN_PATH) onUnauthorized?.();
    return response;
  },
});

export type Schemas = components["schemas"];
export type UserOut = Schemas["UserOut"];
export type PersonaOut = Schemas["PersonaOut"];
export type Role = Schemas["Role"];
export type Competitor = Schemas["CompetitorOut"];
export type DocumentOut = Schemas["DocumentOut"];
export type DocumentPage = Schemas["DocumentPage"];
export type SourceOut = Schemas["SourceOut"];
export type SourceTestOut = Schemas["SourceTestOut"];
export type RunOut = Schemas["RunOut"];

/** A non-2xx response the caller did not handle. */
export class ApiError extends Error {
  readonly status: number;

  constructor(status: number, message = `Request failed with status ${status}`) {
    super(message);
    this.name = "ApiError";
    this.status = status;
  }
}

/** Read FastAPI's `{detail: "..."}` message from an error body, when it is a plain string. */
export function errorDetail(error: unknown): string | null {
  if (error && typeof error === "object" && "detail" in error && typeof error.detail === "string") {
    return error.detail;
  }
  return null;
}
