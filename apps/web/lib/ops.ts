/**
 * Wave 3O (operator, paper-bot control, journal and audit): the typed
 * contracts and paths of the ops routes (src/fiboki/api/routers/ops.py).
 *
 * Every type here IS the generated OpenAPI schema type, not a hand mirror, so
 * a renamed field fails the type check. The workstation renders these; every
 * state, verdict, weight and probability in them was computed by the API.
 */
import type { components } from "./generated/openapi";

type S = components["schemas"];

export type BotControlView = S["BotControlView"];
export type ControlRecordView = S["ControlRecordView"];
export type ControlAction = "pause" | "resume" | "drain";

export const CONTROLS_PATH = "/api/trading/controls";
export const sessionControlPath = (sessionId: string) =>
  `/api/trading/sessions/${encodeURIComponent(sessionId)}/control`;

/** The bot control states, in the order an operator reads them. */
export const CONTROL_STATES = ["active", "paused", "draining", "stopped"] as const;
export type ControlState = (typeof CONTROL_STATES)[number];

/**
 * Display tone per control state (a lookup, not a judgement: the state itself
 * is the API's). Amber for anything that is not trading as wired.
 */
export const CONTROL_TONE: Record<string, "ok" | "degraded" | "neutral"> = {
  active: "ok",
  paused: "degraded",
  draining: "degraded",
  stopped: "neutral",
};
