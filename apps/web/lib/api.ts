import type { ApiErrorBody } from "./types";

export const API_BASE =
  process.env.NEXT_PUBLIC_FIBOKI_API ?? "http://127.0.0.1:8000";

const CSRF_COOKIE = "fiboki_csrf";
const CSRF_HEADER = "X-Fiboki-CSRF";

/** The sign-in route, and the query parameter that carries the return path. */
export const LOGIN_PATH = "/login";
export const NEXT_PARAM = "next";

/** Paths whose 401 is an answer, not an expired session. */
const NO_REDIRECT_ON_401 = new Set(["/api/auth/login"]);

/**
 * Only an in-app path may be a return target: it must start with one slash.
 * "//evil.example" and "https://..." are protocol-relative or absolute URLs,
 * which would turn the sign-in page into an open redirect.
 */
export function safeReturnPath(raw: string | null | undefined): string {
  if (!raw || !raw.startsWith("/") || raw.startsWith("//") || raw.startsWith("/\\")) return "/";
  if (raw === LOGIN_PATH || raw.startsWith(`${LOGIN_PATH}?`)) return "/";
  return raw;
}

/**
 * A 401 means there is no valid session. Send the operator to sign in, and
 * bring them back to where they were. Never from the sign-in page itself.
 */
export function redirectToLogin() {
  if (typeof window === "undefined") return;
  const { pathname, search } = window.location;
  if (pathname === LOGIN_PATH) return;
  const next = encodeURIComponent(safeReturnPath(`${pathname}${search}`));
  // A full navigation, deliberately: nothing held in memory by the expired
  // session (query cache, stream, live store) should survive into the next.
  // eslint-disable-next-line @next/next/no-location-assign-relative-destination
  window.location.href = `${LOGIN_PATH}?${NEXT_PARAM}=${next}`;
}

export class ApiError extends Error {
  readonly code: string;
  readonly status: number;
  readonly correlationId: string;
  readonly context: Record<string, unknown>;

  constructor(status: number, body: Partial<ApiErrorBody>, fallback: string) {
    super(body.detail ?? fallback);
    this.name = "ApiError";
    this.status = status;
    this.code = body.code ?? "unknown_error";
    this.correlationId = body.correlation_id ?? "";
    this.context = body.context ?? {};
  }
}

function readCsrfCookie(): string {
  if (typeof document === "undefined") return "";
  const match = document.cookie
    .split(";")
    .map((c) => c.trim())
    .find((c) => c.startsWith(`${CSRF_COOKIE}=`));
  return match ? decodeURIComponent(match.slice(CSRF_COOKIE.length + 1)) : "";
}

async function parseError(response: Response): Promise<ApiError> {
  let body: Partial<ApiErrorBody> = {};
  try {
    body = (await response.json()) as Partial<ApiErrorBody>;
  } catch {
    // A non-JSON error body is itself information: the API did not handle this.
  }
  return new ApiError(
    response.status,
    body,
    `The request failed with HTTP ${response.status}.`,
  );
}

/**
 * Every call is credentialed and every mutating call carries the double-submit
 * CSRF token. There is never a credential in the URL.
 */
export async function apiFetch<T>(
  path: string,
  init: RequestInit = {},
): Promise<T> {
  const method = (init.method ?? "GET").toUpperCase();
  const headers = new Headers(init.headers);
  headers.set("Accept", "application/json");
  if (init.body !== undefined) headers.set("Content-Type", "application/json");
  if (!["GET", "HEAD", "OPTIONS"].includes(method)) {
    headers.set(CSRF_HEADER, readCsrfCookie());
  }

  let response: Response;
  try {
    response = await fetch(`${API_BASE}${path}`, {
      ...init,
      method,
      headers,
      credentials: "include",
      cache: "no-store",
    });
  } catch {
    // A network failure is NOT an empty result. It gets its own error so the
    // page can say "could not reach the platform" rather than drawing zeros.
    throw new ApiError(
      0,
      {
        code: "network_unreachable",
        detail:
          "Could not reach the Fiboki API. This is a connection failure, not " +
          "an empty result — nothing on this page is current.",
      },
      "network failure",
    );
  }

  if (response.status === 401 && !NO_REDIRECT_ON_401.has(path.split("?")[0] ?? path)) {
    redirectToLogin();
  }
  if (!response.ok) throw await parseError(response);
  if (response.status === 204) return undefined as T;
  return (await response.json()) as T;
}
