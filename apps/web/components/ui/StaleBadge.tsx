"use client";

import { Clock, RefreshCw, Unplug } from "lucide-react";
import { useClock } from "@/lib/clock";
import type { ApiError } from "@/lib/api";
import type { Freshness } from "@/lib/freshness";
import { formatAge, formatTimestamp, formatUtcTime } from "@/lib/format";
import { Button } from "./Button";
import { Popover } from "./Popover";

/** What a view knows about how current its data is. */
export interface StaleInfo {
  freshness: Freshness;
  /** When the data was last known good (ISO, UTC). */
  asOf: string;
  /** The platform's own as_of for the data, when the stream supplies it. */
  sourceAsOf?: string | null;
  refreshError: ApiError | null;
}

function useAgeSeconds(iso: string): number {
  const now = useClock();
  return Math.max(0, (now - Date.parse(iso)) / 1_000);
}

/**
 * "These numbers are the last good ones, not current ones." It replaces
 * nothing: the data stays on screen beside it (report E §6.4).
 *
 *  - lagging: a small LAG badge with the age;
 *  - stale: STALE, last good age, and the platform's as-of when known;
 *  - disconnected: DISCONNECTED with a struck-through age tag; the numbers
 *    remain, and mutations are disabled elsewhere.
 *
 * The why (last good time, the failing refresh's code and correlation id) is
 * in a popover reachable by keyboard and touch, not only in a hover title.
 */
export function StaleBadge({
  info,
  polling,
  onRetry,
}: {
  info: StaleInfo;
  polling: boolean;
  onRetry?: () => void;
}) {
  const ageSeconds = useAgeSeconds(info.asOf);
  if (info.freshness === "lagging") {
    return (
      <span className="stale-badge" data-testid="state-lagging" role="status">
        <span className="badge badge--neutral" title={`Last received ${formatTimestamp(info.asOf)}.`}>
          LAG {formatAge(ageSeconds).replace(/ ago$/, "")}
        </span>
      </span>
    );
  }
  const disconnected = info.freshness === "disconnected";
  const error = info.refreshError;
  // A view that neither polls nor streams is stale by AGE alone (report G
  // W-05): nothing failed, it simply has not been re-read.
  const aged = !polling && error === null && !disconnected;
  const detail = [
    `Last good data received ${formatTimestamp(info.asOf)}.`,
    info.sourceAsOf ? `The platform's as-of for it is ${formatTimestamp(info.sourceAsOf)}.` : "",
    disconnected
      ? "The live stream has given up after repeated failures and the REST fallback is failing too."
      : "",
    error
      ? `The latest refresh failed: ${error.message} (code ${error.code}` +
        `${error.status ? `, http ${error.status}` : ""}` +
        `${error.correlationId ? `, correlation ${error.correlationId}` : ""}).`
      : aged
        ? "This view does not refresh on its own and is past its maximum age. Retry to read it again."
        : "No refresh has completed since.",
  ]
    .filter(Boolean)
    .join(" ");
  const label = disconnected ? "DISCONNECTED" : "STALE";
  return (
    <span
      className="stale-badge"
      data-testid="state-stale"
      data-freshness={info.freshness}
      role="status"
      title={detail}
    >
      <Popover
        title={disconnected ? "Disconnected" : "Stale data"}
        trigger={
          <button
            type="button"
            className={`badge ${disconnected ? "badge--down" : "badge--degraded"}`}
            aria-label={`${label}: why these numbers may be out of date`}
          >
            {disconnected ? (
              <Unplug size={11} aria-hidden="true" />
            ) : (
              <Clock size={11} aria-hidden="true" />
            )}
            {label}
          </button>
        }
      >
        <p>{detail}</p>
      </Popover>
      <span className={disconnected ? "line-through" : undefined} data-testid="state-stale-age">
        {aged ? "loaded" : "last good"} {formatAge(ageSeconds)}
      </span>
      {info.sourceAsOf ? <span>· as of {formatUtcTime(info.sourceAsOf)}</span> : null}
      {polling ? <span> · retrying</span> : null}
      {!polling && onRetry ? (
        <Button size="sm" onClick={onRetry} data-testid="state-stale-retry">
          Retry
        </Button>
      ) : null}
    </span>
  );
}

/** A sequence gap was seen on the stream; the REST snapshot is being re-read. */
export function ResyncBadge() {
  return (
    <span className="stale-badge" data-testid="state-resyncing" role="status">
      <span className="badge badge--neutral">
        <RefreshCw size={11} aria-hidden="true" />
        RESYNCING
      </span>
    </span>
  );
}
