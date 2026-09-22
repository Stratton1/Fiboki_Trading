"use client";

import type { ReactNode } from "react";
import type { AsyncState } from "@/lib/api";

/**
 * Loading, error, empty and success are four distinct visual states.
 *
 * The component takes a discriminated `AsyncState`, so the success branch is
 * unreachable while the request is in flight or has failed — TypeScript, not
 * discipline, keeps them apart. The error branch always names the failure and
 * the correlation id; it never degrades into an empty table.
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
  state: AsyncState<T>;
  isEmpty?: (data: T) => boolean;
  emptyTitle?: string;
  emptyBody?: string;
  label: string;
  onRetry?: () => void;
  children: (data: T) => ReactNode;
}) {
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

  if (isEmpty?.(state.data)) {
    return (
      <div className="state state--empty" data-testid="state-empty">
        <div className="state__title">
          <span>{emptyTitle}</span>
          <span className="badge badge--unknown">EMPTY</span>
        </div>
        <div className="state__body">{emptyBody}</div>
      </div>
    );
  }

  return (
    <div data-testid="state-success">{children(state.data)}</div>
  );
}
