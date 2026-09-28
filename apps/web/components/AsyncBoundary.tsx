"use client";

import { useEffect, useState, type ReactNode } from "react";
import type { ApiError, AsyncState } from "@/lib/api";
import { formatAge, formatTimestamp } from "@/lib/format";

/** Data older than this many poll intervals is stale even with no error. */
export const STALE_AFTER_INTERVALS = 3;

/**
 * A wall clock that ticks only while something on screen depends on it, so a
 * "last good 40s ago" badge keeps counting between polls.
 */
export function useNow(active: boolean, tickMs = 1000): number {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    if (!active) return;
    const timer = setInterval(() => setNow(Date.now()), tickMs);
    return () => clearInterval(timer);
  }, [active, tickMs]);
  return now;
}

export interface Freshness {
  stale: boolean;
  ageSeconds: number;
  refreshError: ApiError | null;
}

/**
 * How current a successful payload is. Stale when the last refresh failed, or
 * when the payload is older than {@link STALE_AFTER_INTERVALS} poll intervals
 * (a refresh that hangs never errors, so age is checked independently).
 */
export function useFreshness<T>(
  state: AsyncState<T>,
  refreshMs: number | undefined,
): Freshness | null {
  const success = state.status === "success";
  const now = useNow(success && (refreshMs !== undefined || state.refreshError !== null));
  if (state.status !== "success") return null;
  const ageSeconds = Math.max(0, (now - Date.parse(state.asOf)) / 1000);
  const overdue =
    refreshMs !== undefined && ageSeconds * 1000 > STALE_AFTER_INTERVALS * refreshMs;
  return {
    stale: state.refreshError !== null || overdue,
    ageSeconds,
    refreshError: state.refreshError,
  };
}

/**
 * The marker for "these numbers are the last good ones, not current ones".
 * It replaces nothing: the data stays on screen beside it.
 */
export function StaleBadge({
  freshness,
  asOf,
  polling,
  onRetry,
}: {
  freshness: Freshness;
  asOf: string;
  polling: boolean;
  onRetry?: () => void;
}) {
  const error = freshness.refreshError;
  const title = [
    `Last good data received ${formatTimestamp(asOf)}.`,
    error
      ? `The latest refresh failed: ${error.message} (code ${error.code}` +
        `${error.status ? `, http ${error.status}` : ""}` +
        `${error.correlationId ? `, correlation ${error.correlationId}` : ""}).`
      : "No refresh has completed since.",
  ].join(" ");
  return (
    <span className="stale-badge" data-testid="state-stale" role="status" title={title}>
      <span className="badge badge--degraded">STALE</span>
      <span>
        last good {formatAge(freshness.ageSeconds)}
        {polling ? " · retrying" : ""}
      </span>
      {!polling && onRetry ? (
        <button type="button" onClick={onRetry} data-testid="state-stale-retry">
          Retry
        </button>
      ) : null}
    </span>
  );
}

/**
 * Loading, error, empty and success are four distinct visual states.
 *
 * The component takes a discriminated `AsyncState`, so the success branch is
 * unreachable while the request is in flight or has failed — TypeScript, not
 * discipline, keeps them apart. The error branch always names the failure and
 * the correlation id; it never degrades into an empty table.
 *
 * After a first success the view keeps its data through every later refresh.
 * A failed or overdue refresh adds a STALE badge beside the last good data
 * rather than swapping the panel for a skeleton or an error: an operator can
 * still read the numbers, and cannot mistake them for current ones.
 */
export function AsyncBoundary<T>({
  state,
  isEmpty,
  emptyTitle = "Nothing here yet",
  emptyBody = "The platform returned a successful, empty result for this view.",
  label,
  onRetry,
  children,
}: {
  state: AsyncState<T> & { refreshMs?: number };
  isEmpty?: (data: T) => boolean;
  emptyTitle?: string;
  emptyBody?: string;
  label: string;
  onRetry?: () => void;
  children: (data: T) => ReactNode;
}) {
  // Called unconditionally: hooks may not follow the early returns below.
  const freshness = useFreshness(state, state.refreshMs);

  if (state.status === "loading") {
    return (
      <div className="state state--loading" data-testid="state-loading" role="status">
        <div className="state__title">Loading {label}…</div>
        <div className="skeleton" style={{ width: "72%" }} />
        <div className="skeleton" style={{ width: "54%" }} />
        <div className="skeleton" style={{ width: "63%" }} />
      </div>
    );
  }

  if (state.status === "error") {
    return (
      <div className="state state--error" data-testid="state-error" role="alert">
        <div className="state__title">
          <span>Could not load {label}</span>
          <span className="badge badge--down">FAILED</span>
        </div>
        <div className="state__body">
          {state.error.message}
          <p>
            <strong>Nothing on this panel is current.</strong> Do not read the
            absence of rows here as an absence of activity on the platform.
          </p>
        </div>
        <div className="state__code">
          code: {state.error.code}
          {state.error.status ? ` · http ${state.error.status}` : ""}
          {state.error.correlationId
            ? ` · correlation ${state.error.correlationId}`
            : ""}
        </div>
        {onRetry ? (
          <p>
            <button type="button" onClick={onRetry} data-testid="state-error-retry">
              Retry
            </button>
          </p>
        ) : null}
      </div>
    );
  }

  const stale = freshness?.stale === true;
  const badge =
    freshness && stale ? (
      <StaleBadge
        freshness={freshness}
        asOf={state.asOf}
        polling={state.refreshMs !== undefined}
        onRetry={onRetry}
      />
    ) : null;

  if (isEmpty?.(state.data)) {
    return (
      <div
        className="state state--empty"
        data-testid="state-empty"
        data-as-of={state.asOf}
        data-stale={stale}
      >
        {badge}
        <div className="state__title">
          <span>{emptyTitle}</span>
          <span className="badge badge--unknown">EMPTY</span>
        </div>
        <div className="state__body">{emptyBody}</div>
      </div>
    );
  }

  return (
    <div
      data-testid="state-success"
      data-as-of={state.asOf}
      data-refreshing={state.refreshing}
      data-stale={stale}
    >
      {badge}
      {children(state.data)}
    </div>
  );
}
