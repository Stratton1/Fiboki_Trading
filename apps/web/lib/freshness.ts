/**
 * The freshness engine (report E §6.4). Pure, so it is tested directly.
 *
 *   live          stream connected and feeding this view, confirmed within 1.5x cadence
 *   fresh         current, but read by REST (the stream is not feeding this view)
 *   lagging       older than 1.5x its cadence
 *   stale         older than 3x its cadence, the last re-read failed, the
 *                 view is worker-produced and the worker heartbeat is >= 120 s
 *                 (or the worker has never beaten, or the platform says the
 *                 worker is not alive), or the view neither polls nor streams
 *                 and is older than its maximum age (default 5 minutes)
 *   disconnected  the stream has given up (five failures) AND REST is failing
 *
 * A stale or disconnected view keeps its last good data on screen. Nothing in
 * this module can turn data into "loading" or into zero.
 */

export type Freshness = "live" | "fresh" | "lagging" | "stale" | "disconnected";

/**
 * The worker heartbeat stale threshold when the platform has not said one:
 * the stream heartbeat carries the backend's own (`worker_stale_after_s`),
 * and that wins.
 */
export const WORKER_STALE_AFTER_S = 120;
export const LAGGING_FACTOR = 1.5;
export const STALE_FACTOR = 3;

export interface FreshnessInput {
  now: number;
  /** Stream connection state for this tab. */
  connection: "idle" | "connecting" | "live" | "reconnecting" | "disconnected";
  /** The stream is feeding this view's topic and has confirmed it at least once. */
  streamFed: boolean;
  /** Client ms the stream last confirmed the topic (null when never). */
  confirmedAt: number | null;
  /** The expected cadence of the stream topic, in ms. */
  topicCadenceMs: number | null;
  /** Client ms the REST payload was received. */
  receivedAt: number;
  /** The REST poll interval in force, when the view polls. */
  refreshMs: number | undefined;
  /** The latest REST refresh failed (data is last-known-good). */
  refreshFailing: boolean;
  /** Whether a dead worker makes this view stale. */
  workerDependent: boolean;
  /**
   * The worker heartbeat age now, in seconds: undefined when unknown (no
   * report received), null when the platform says the worker never beat.
   */
  workerAgeS: number | null | undefined;
  /** The platform's stale threshold in seconds (null/undefined: the default). */
  workerStaleAfterS?: number | null;
  /** The platform's own worker verdict (`ok`, `stale`, `absent`, or a health status). */
  workerState?: string | null;
  /**
   * The maximum age of a view that neither polls nor is fed by the stream,
   * in ms (report G W-05). Past it the view is stale, with its age shown,
   * however "fresh" its one successful read once was. Default 5 minutes.
   */
  maxAgeMs?: number;
}

/** A read that neither polls nor streams is stale after this long (report G W-05). */
export const DEFAULT_MAX_AGE_MS = 5 * 60_000;

function level(ageMs: number, cadenceMs: number): "ok" | "lagging" | "stale" {
  if (ageMs > STALE_FACTOR * cadenceMs) return "stale";
  if (ageMs > LAGGING_FACTOR * cadenceMs) return "lagging";
  return "ok";
}

export function workerIsStale(
  ageS: number | null | undefined,
  staleAfterS: number | null | undefined = WORKER_STALE_AFTER_S,
): boolean {
  if (ageS === undefined) return false;
  const threshold = typeof staleAfterS === "number" ? staleAfterS : WORKER_STALE_AFTER_S;
  return ageS === null || ageS >= threshold;
}

/**
 * The platform's worker verdicts that mean "not alive now". `ok` is the only
 * healthy one; `stale` and `absent` come from the stream heartbeat
 * (`HeartbeatReading.state`), `degraded` and `down` from the health report's
 * `worker_heartbeat` check.
 */
export function workerStateIsBad(state: string | null | undefined): boolean {
  return state === "stale" || state === "absent" || state === "down" || state === "degraded";
}

export interface WorkerStatus {
  tone: "ok" | "warn" | "critical" | "unknown";
  /** Status-bar text after "worker hb ". */
  text: string;
  /** Why, in words, for the title and the screen reader. */
  detail: string;
  stale: boolean;
}

/**
 * The status bar's worker entry (report G W-03). The TONE is the platform's
 * verdict, not a client threshold:
 *
 *  - the platform says `stale`/`absent`/`down`: critical, whatever the age;
 *  - the platform says `ok`, but the age has since counted past the
 *    threshold the platform itself supplied: critical (a report that was
 *    true a while ago cannot vouch for now);
 *  - the platform says `ok` over REST and the report itself is stale:
 *    warn, "unconfirmed", never green;
 *  - no verdict at all (an older API): judged against the platform's
 *    threshold if supplied, else the documented default, and the detail
 *    says so.
 */
export function workerStatus(input: {
  ageS: number | null | undefined;
  state: string | null | undefined;
  staleAfterS: number | null | undefined;
  /** The report carrying the verdict is itself stale (REST health). */
  reportStale: boolean;
}): WorkerStatus {
  const { ageS, state } = input;
  const age = ageS === null ? "never" : ageS === undefined ? "unknown" : formatAgeShort(ageS);
  if (ageS === undefined && (state === null || state === undefined)) {
    return { tone: "unknown", text: "unknown", detail: "No worker report has been received.", stale: false };
  }
  if (ageS === null || state === "absent") {
    return {
      tone: "critical",
      text: "never",
      detail: "The platform reports no worker heartbeat at all: nothing is evaluating signals.",
      stale: true,
    };
  }
  if (workerStateIsBad(state)) {
    return {
      tone: "critical",
      text: `${age} · stale`,
      detail: `The platform judges the worker ${state}; its last heartbeat was ${age}.`,
      stale: true,
    };
  }
  const threshold = typeof input.staleAfterS === "number" ? input.staleAfterS : null;
  if (typeof ageS === "number" && threshold !== null && ageS >= threshold) {
    return {
      tone: "critical",
      text: `${age} · stale`,
      detail: `The heartbeat has aged past the platform's ${Math.round(threshold)} s threshold since the last report.`,
      stale: true,
    };
  }
  if (state === "ok") {
    if (input.reportStale) {
      return {
        tone: "warn",
        text: `${age} · unconfirmed`,
        detail: "The platform last said the worker was alive, but that report is itself stale.",
        stale: false,
      };
    }
    return { tone: "ok", text: age, detail: `The platform judges the worker alive; last heartbeat ${age}.`, stale: false };
  }
  // No verdict supplied: the documented default threshold, said out loud.
  const dead = workerIsStale(ageS, threshold);
  return {
    tone: dead ? "critical" : "ok",
    text: dead ? `${age} · stale` : age,
    detail: `The platform did not state the worker's condition; judged against a ${Math.round(
      threshold ?? WORKER_STALE_AFTER_S,
    )} s threshold.`,
    stale: dead,
  };
}

function formatAgeShort(seconds: number): string {
  if (seconds < 60) return `${Math.round(seconds)}s ago`;
  if (seconds < 3600) return `${Math.round(seconds / 60)}m ago`;
  return `${Math.round(seconds / 3600)}h ago`;
}

export function computeFreshness(input: FreshnessInput): Freshness {
  const streamLive =
    input.connection === "live" && input.streamFed && input.confirmedAt !== null;

  if (input.connection === "disconnected" && input.refreshFailing) return "disconnected";
  if (
    input.workerDependent &&
    (workerIsStale(input.workerAgeS, input.workerStaleAfterS) || workerStateIsBad(input.workerState))
  ) {
    return "stale";
  }
  // A stream-fed view is only re-read by REST when its stream data is suspect
  // (a sequence gap, or a heartbeat that disagrees). If that re-read failed,
  // the data on screen is not known to be current, however live the stream.
  if (input.refreshFailing) return "stale";

  if (streamLive && input.topicCadenceMs !== null && input.confirmedAt !== null) {
    const byStream = level(input.now - input.confirmedAt, input.topicCadenceMs);
    if (byStream === "stale") return "stale";
    if (byStream === "lagging") return "lagging";
    return "live";
  }

  if (input.refreshMs === undefined) {
    // Neither polled nor streamed: fresh only until its maximum age.
    const maxAge = input.maxAgeMs ?? DEFAULT_MAX_AGE_MS;
    return input.now - input.receivedAt > maxAge ? "stale" : "fresh";
  }
  const byPoll = level(input.now - input.receivedAt, input.refreshMs);
  if (byPoll === "stale") return "stale";
  if (byPoll === "lagging") return "lagging";
  return "fresh";
}

/** Stale and disconnected views show the last-known-good marker. */
export function isStale(freshness: Freshness): boolean {
  return freshness === "stale" || freshness === "disconnected";
}
