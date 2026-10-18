/**
 * The freshness engine (report E §6.4). Pure, so it is tested directly.
 *
 *   live          stream connected and feeding this view, confirmed within 1.5x cadence
 *   fresh         current, but read by REST (the stream is not feeding this view)
 *   lagging       older than 1.5x its cadence
 *   stale         older than 3x its cadence, the last re-read failed, the
 *                 view is worker-produced and the PLATFORM says the worker is
 *                 not alive (stale, absent, down), or the view neither polls
 *                 nor streams and is older than its maximum age (default 5
 *                 minutes). The cadence factors are about this tab's copy of
 *                 the data; whether the platform's worker is alive is the
 *                 platform's verdict, never a threshold applied here.
 *   disconnected  the stream has given up (five failures) AND REST is failing
 *
 * A stale or disconnected view keeps its last good data on screen. Nothing in
 * this module can turn data into "loading" or into zero.
 */

export type Freshness = "live" | "fresh" | "lagging" | "stale" | "disconnected";

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
   * The platform's own verdict on the worker (`ok`, `stale`, `absent`, or a
   * health-check status), from the stream heartbeat or GET /api/health.
   * The workstation never derives one from an age: the platform decides.
   */
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
 * verdict and nothing else: GET /api/health `worker_state` or the stream
 * heartbeat's `worker_state`. The age beside it counts up from the last
 * report for display only; no threshold is applied to it here.
 *
 *  - `stale` / `absent` / `down` / `degraded`: critical;
 *  - `ok` from a report that is itself out of date (the health read is
 *    failing or stale, and the stream is not live): warn, "unconfirmed",
 *    never green, because an old "alive" cannot vouch for now;
 *  - `ok`: ok;
 *  - no verdict at all: unknown, said in words.
 */
export function workerStatus(input: {
  ageS: number | null | undefined;
  state: string | null | undefined;
  /** The report carrying the verdict is itself stale (REST health). */
  reportStale: boolean;
}): WorkerStatus {
  const { ageS, state } = input;
  const age = ageS === null ? "never" : ageS === undefined ? "unknown" : formatAgeShort(ageS);
  if (state === null || state === undefined) {
    return {
      tone: "unknown",
      text: ageS === undefined ? "unknown" : age,
      detail: "The platform has not stated the worker's condition.",
      stale: false,
    };
  }
  if (state === "absent" || ageS === null) {
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
  if (input.reportStale) {
    return {
      tone: "warn",
      text: `${age} · unconfirmed`,
      detail: "The platform last said the worker was alive, but that report is itself out of date.",
      stale: false,
    };
  }
  return { tone: "ok", text: age, detail: `The platform judges the worker alive; last heartbeat ${age}.`, stale: false };
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
  // The platform's verdict, never a client threshold on an age.
  if (input.workerDependent && (input.workerState === "absent" || workerStateIsBad(input.workerState))) {
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
