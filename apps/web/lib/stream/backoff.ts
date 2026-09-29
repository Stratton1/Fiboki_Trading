/**
 * Application-level reconnect backoff (report E §6.3): 1 -> 2 -> 4 -> 8 -> 15 s,
 * capped, with +/-20% jitter so two tabs, or Joe's and Tom's browsers, do not
 * reconnect in lock-step after a server restart.
 */

export const BACKOFF_STEPS_MS = [1_000, 2_000, 4_000, 8_000, 15_000] as const;
export const JITTER = 0.2;
/** After this many consecutive failures the stream is DISCONNECTED. */
export const FAILURES_BEFORE_DISCONNECTED = 5;
/**
 * A stream that drops after working gets this long for the browser's own
 * reconnect (honouring the server's `retry:` hint, 3 s from the backend)
 * before the UI says "reconnecting": one heartbeat interval, so a normal
 * server-side reconnect does not flicker. Staleness is judged from
 * heartbeats, not from this.
 */
export const DROP_GRACE_MS = 5_000;

/** `failures` >= 1; `random` in [0, 1). */
export function backoffDelay(failures: number, random: number): number {
  const index = Math.min(Math.max(failures, 1), BACKOFF_STEPS_MS.length) - 1;
  const base = BACKOFF_STEPS_MS[index] as number;
  const jitter = (random * 2 - 1) * JITTER;
  return Math.round(base * (1 + jitter));
}

/** A uniform [0, 1) from the platform CSPRNG (no Math.random in this codebase). */
export function uniformRandom(): number {
  const buffer = new Uint32Array(1);
  globalThis.crypto.getRandomValues(buffer);
  return (buffer[0] as number) / 2 ** 32;
}
