"use client";

import type { ReactNode } from "react";
import { isStale } from "@/lib/freshness";
import type { ViewState } from "@/lib/query";
import { Button } from "./ui/Button";
import { EmptyState } from "./ui/EmptyState";
import { Skeleton } from "./ui/Skeleton";
import { ResyncBadge, StaleBadge } from "./ui/StaleBadge";

export { StaleBadge, type StaleInfo } from "./ui/StaleBadge";

/**
 * Loading, error, empty and success are distinct visual states, and success
 * carries its freshness (report E §3.3, §6.4).
 *
 * The component takes a discriminated ViewState, so the success branch is
 * unreachable while the first request is in flight or has failed: TypeScript,
 * not discipline, keeps them apart. The error branch always names the failure
 * and the correlation id; it never degrades into an empty table.
 *
 * After a first success the view keeps its data through every later refresh
 * and every stream event. A lagging view shows LAG; a stale one STALE with the
 * last good age; a disconnected one DISCONNECTED with the age struck through.
 * None of them swaps the panel for a skeleton or an error: an operator can
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
  state: ViewState<T> & { refreshMs?: number };
  isEmpty?: (data: T) => boolean;
  emptyTitle?: string;
  emptyBody?: string;
  label: string;
  onRetry?: () => void;
  children: (data: T) => ReactNode;
}) {
  if (state.status === "loading") {
    return (
      <div
        className="state state--loading"
        data-testid="state-loading"
        data-label={label}
        role="status"
      >
        <div className="state__title">Loading {label}…</div>
        <Skeleton className="w-[72%]" />
        <Skeleton className="w-[54%]" />
        <Skeleton className="w-[63%]" />
      </div>
    );
  }

  if (state.status === "error") {
    return (
      <div className="state state--error" data-testid="state-error" data-label={label} role="alert">
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

  const stale = isStale(state.freshness);
  const badge =
    stale || state.freshness === "lagging" ? (
      <StaleBadge
        info={{
          freshness: state.freshness,
          asOf: state.asOf,
          sourceAsOf: state.sourceAsOf,
          refreshError: state.refreshError,
        }}
        polling={state.refreshMs !== undefined}
        onRetry={onRetry}
      />
    ) : null;
  const badges = (
    <>
      {badge}
      {state.resyncing ? <ResyncBadge /> : null}
    </>
  );

  if (isEmpty?.(state.data)) {
    return (
      <EmptyState
        title={emptyTitle}
        badge={badges}
        data-as-of={state.asOf}
        data-stale={stale}
        data-freshness={state.freshness}
        data-label={label}
      >
        {emptyBody}
      </EmptyState>
    );
  }

  return (
    <div
      data-testid="state-success"
      data-label={label}
      data-as-of={state.asOf}
      data-refreshing={state.refreshing}
      data-stale={stale}
      data-freshness={state.freshness}
      data-refresh-error={state.refreshError?.code}
    >
      {badges}
      {children(state.data)}
    </div>
  );
}
