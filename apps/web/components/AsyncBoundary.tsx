"use client";

import { useEffect, useState, type ReactNode } from "react";
import type { AsyncState } from "@/lib/api";
import { Button } from "./ui/Button";
import { EmptyState } from "./ui/EmptyState";
import { Skeleton } from "./ui/Skeleton";
import { StaleBadge, type Freshness } from "./ui/StaleBadge";

export { StaleBadge, type Freshness } from "./ui/StaleBadge";

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
        <Skeleton className="w-[72%]" />
        <Skeleton className="w-[54%]" />
        <Skeleton className="w-[63%]" />
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
          <div className="state__actions">
            <Button onClick={onRetry} data-testid="state-error-retry">
              Retry
            </Button>
          </div>
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
      <EmptyState
        title={emptyTitle}
        badge={badge}
        data-as-of={state.asOf}
        data-stale={stale}
      >
        {emptyBody}
      </EmptyState>
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
