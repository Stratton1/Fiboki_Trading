"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import type { ApiErrorBody } from "./types";

export const API_BASE =
  process.env.NEXT_PUBLIC_FIBOKI_API ?? "http://127.0.0.1:8000";

const CSRF_COOKIE = "fiboki_csrf";
const CSRF_HEADER = "X-Fiboki-CSRF";

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
  } catch (cause) {
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

  if (!response.ok) throw await parseError(response);
  if (response.status === 204) return undefined as T;
  return (await response.json()) as T;
}

export type AsyncState<T> =
  | { status: "loading"; data: null; error: null }
  | { status: "error"; data: null; error: ApiError }
  | {
      status: "success";
      data: T;
      error: null;
      /** Client receipt time (ISO, UTC) of the data currently held. */
      asOf: string;
      /** A poll or manual reload is in flight; `data` is the last good payload. */
      refreshing: boolean;
      /**
       * The most recent refresh failed. `data` is still the last good payload
       * and is NOT current; the view must say so rather than blank itself.
       */
      refreshError: ApiError | null;
    };

/** The hook's return: the state, a manual reload, and the poll interval in force. */
export type ApiHandle<T> = AsyncState<T> & {
  reload: () => void;
  refreshMs: number | undefined;
};

const LOADING = { status: "loading", data: null, error: null } as const;

function toApiError(error: unknown): ApiError {
  return error instanceof ApiError
    ? error
    : new ApiError(0, { code: "unexpected_error" }, String(error));
}

/**
 * The four states are modelled in the type, so a component physically cannot
 * render a success branch while the request is in flight or has failed.
 *
 * "empty" is derived by the consumer from the successful payload, because only
 * the consumer knows what empty means for its shape.
 *
 * Refresh semantics. The state resets to loading ONLY when the path changes. A
 * poll tick or a manual reload keeps the last good payload on screen, marks it
 * `refreshing`, and on failure records `refreshError` beside it. Once a path
 * has succeeded, its view never falls back to the loading or error branch:
 * blanking the execution-mode banner every 30 seconds taught operators to
 * ignore it, and a view that flickers to a skeleton on each poll is a view
 * whose outages look like routine refreshes.
 */
export function useApi<T>(
  path: string | null,
  options: { refreshMs?: number } = {},
): ApiHandle<T> {
  const [state, setState] = useState<AsyncState<T>>(LOADING);
  const [nonce, setNonce] = useState(0);
  // Adjust state DURING render when the request identity changes, rather than
  // in an effect. React sanctions adjusting state during render for exactly
  // this; doing it in an effect renders one frame of stale success data for
  // the new path, which on this product means showing the previous
  // instrument's numbers under the new instrument's heading.
  const [tracked, setTracked] = useState<{ path: string | null; nonce: number }>({
    path,
    nonce,
  });
  if (tracked.path !== path) {
    // A different resource: nothing held belongs to it.
    setTracked({ path, nonce });
    setState(LOADING);
  } else if (tracked.nonce !== nonce) {
    // The same resource, fetched again: keep the last good payload.
    setTracked({ path, nonce });
    if (state.status === "success") {
      if (!state.refreshing) setState({ ...state, refreshing: true });
    } else if (state.status === "error") {
      // Never succeeded: a retry is a fresh load.
      setState(LOADING);
    }
  }

  const mounted = useRef(true);
  useEffect(() => {
    mounted.current = true;
    return () => {
      mounted.current = false;
    };
  }, []);

  useEffect(() => {
    if (path === null) return;
    let cancelled = false;
    apiFetch<T>(path)
      .then((data) => {
        if (!cancelled && mounted.current) {
          setState({
            status: "success",
            data,
            error: null,
            asOf: new Date().toISOString(),
            refreshing: false,
            refreshError: null,
          });
        }
      })
      .catch((error: unknown) => {
        if (cancelled || !mounted.current) return;
        const apiError = toApiError(error);
        setState((previous) =>
          previous.status === "success"
            ? { ...previous, refreshing: false, refreshError: apiError }
            : { status: "error", data: null, error: apiError },
        );
      });
    return () => {
      cancelled = true;
    };
  }, [path, nonce]);

  useEffect(() => {
    const interval = options.refreshMs;
    if (!interval || path === null) return;
    const timer = setInterval(() => setNonce((n) => n + 1), interval);
    return () => clearInterval(timer);
  }, [options.refreshMs, path]);

  const reload = useCallback(() => setNonce((n) => n + 1), []);
  return { ...state, reload, refreshMs: options.refreshMs };
}
