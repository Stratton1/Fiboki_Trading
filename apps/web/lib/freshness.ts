/**
 * The freshness engine (report E §6.4). Pure, so it is tested directly.
 *
 *   live          stream connected and feeding this view, confirmed within 1.5x cadence
 *   fresh         current, but read by REST (the stream is not feeding this view)
 *   lagging       older than 1.5x its cadence
 *   stale         older than 3x its cadence, the last re-read failed, or the
 *                 view is worker-produced and the worker heartbeat is >= 120 s
 *                 (or the worker has never beaten)
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
}

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

export function computeFreshness(input: FreshnessInput): Freshness {
  const streamLive =
    input.connection === "live" && input.streamFed && input.confirmedAt !== null;

  if (input.connection === "disconnected" && input.refreshFailing) return "disconnected";
  if (input.workerDependent && workerIsStale(input.workerAgeS, input.workerStaleAfterS)) {
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

  if (input.refreshMs === undefined) return "fresh";
  const byPoll = level(input.now - input.receivedAt, input.refreshMs);
  if (byPoll === "stale") return "stale";
  if (byPoll === "lagging") return "lagging";
  return "fresh";
}

/** Stale and disconnected views show the last-known-good marker. */
export function isStale(freshness: Freshness): boolean {
  return freshness === "stale" || freshness === "disconnected";
}
