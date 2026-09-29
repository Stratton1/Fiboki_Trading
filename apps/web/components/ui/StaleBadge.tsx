import { Clock } from "lucide-react";
import { formatAge, formatTimestamp } from "@/lib/format";
import type { ApiError } from "@/lib/api";
import { Button } from "./Button";
import { Popover } from "./Popover";

export interface Freshness {
  stale: boolean;
  ageSeconds: number;
  refreshError: ApiError | null;
}

/**
 * "These numbers are the last good ones, not current ones." It replaces
 * nothing: the data stays on screen beside it. The why (last good time, the
 * failing refresh's code and correlation id) is in a popover reachable by
 * keyboard and touch, not only in a hover title.
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
  const detail = [
    `Last good data received ${formatTimestamp(asOf)}.`,
    error
      ? `The latest refresh failed: ${error.message} (code ${error.code}` +
        `${error.status ? `, http ${error.status}` : ""}` +
        `${error.correlationId ? `, correlation ${error.correlationId}` : ""}).`
      : "No refresh has completed since.",
  ].join(" ");
  return (
    <span className="stale-badge" data-testid="state-stale" role="status" title={detail}>
      <Popover
        title="Stale data"
        trigger={
          <button
            type="button"
            className="badge badge--degraded"
            aria-label="Stale: why these numbers may be out of date"
          >
            <Clock size={11} aria-hidden="true" />
            STALE
          </button>
        }
      >
        <p>{detail}</p>
      </Popover>
      <span>
        last good {formatAge(freshness.ageSeconds)}
        {polling ? " · retrying" : ""}
      </span>
      {!polling && onRetry ? (
        <Button size="sm" onClick={onRetry} data-testid="state-stale-retry">
          Retry
        </Button>
      ) : null}
    </span>
  );
}
