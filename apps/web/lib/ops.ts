/**
 * Wave 3O (operator, paper-bot control, journal and audit): the typed
 * contracts and paths of the ops routes (src/fiboki/api/routers/ops.py).
 *
 * Every type here IS the generated OpenAPI schema type, not a hand mirror, so
 * a renamed field fails the type check. The workstation renders these; every
 * state, verdict, weight and probability in them was computed by the API.
 */
import type { components } from "./generated/openapi";
import type { Figure } from "./types";

type S = components["schemas"];

/**
 * A schema type with every generated Figure replaced by the LOCAL Figure
 * contract (lib/types.ts, checked against the schema in lib/api-contract.ts),
 * so ops payloads render through FigureValue like every other screen's.
 */
export type Local<T> = T extends S["Figure"]
  ? Figure
  : T extends (infer U)[]
    ? Local<U>[]
    : T extends object
      ? { [K in keyof T]: Local<T[K]> }
      : T;

export type BotControlView = Local<S["BotControlView"]>;
export type ControlRecordView = Local<S["ControlRecordView"]>;
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

export type ChainIntegrityView = Local<S["ChainIntegrityView"]>;
export type ChainTailView = Local<S["ChainTailView"]>;
export type ChainRowView = Local<S["ChainRowView"]>;

export const AUDIT_CHAINS_PATH = "/api/trading/audit-chain";
export const sessionChainPath = (sessionId: string, limit = 50) =>
  `/api/trading/sessions/${encodeURIComponent(sessionId)}/audit-chain?limit=${limit}`;

/** Badge tone per chain status (the status is the API's verdict). */
export const CHAIN_TONE: Record<string, "ok" | "down" | "unknown"> = { ok: "ok", broken: "down", absent: "unknown" };

export type ReconciliationView = Local<S["ReconciliationView"]>;
export type ReconciliationReportView = Local<S["ReconciliationReportView"]>;
export type DivergenceView = Local<S["DivergenceView"]>;
export type OpsJobView = Local<S["OpsJobView"]>;

export const sessionReconciliationPath = (sessionId: string) =>
  `/api/trading/sessions/${encodeURIComponent(sessionId)}/reconciliation`;

/** Badge tone per reconciliation status (the status is the API's). */
export const RECON_TONE: Record<string, "ok" | "degraded" | "unknown"> = {
  match: "ok",
  diverged: "degraded",
  not_evaluated: "unknown",
};

export type MaturityView = Local<S["MaturityView"]>;
export type MaturityItemView = Local<S["MaturityItemView"]>;
export const sessionMaturityPath = (sessionId: string) =>
  `/api/trading/sessions/${encodeURIComponent(sessionId)}/maturity`;

/** Badge tone and label per checklist status (the status is the API's). */
export const CHECK_TONE: Record<string, "ok" | "down" | "unknown"> = { pass: "ok", fail: "down", not_evaluated: "unknown" };
export const CHECK_LABEL: Record<string, string> = { pass: "PASS", fail: "FAIL", not_evaluated: "NOT EVALUATED" };

export type SelfCorrelationView = Local<S["SelfCorrelationView"]>;
export const SELF_CORRELATION_PATH = "/api/trading/candidates/correlation";

export type RiskCalculatorView = Local<S["RiskCalculatorView"]>;

/** Build the calculator's GET path from the form (inputs only; every number comes back from the API). */
export function riskCalculatorPath(form: {
  accountSize: string;
  accountCcy: string;
  riskPct: string;
  ruinPct: string;
  strategyId: string;
  rMultiples: string;
  entries: string;
}): string {
  const params = new URLSearchParams({
    account_size: form.accountSize.trim(),
    account_ccy: form.accountCcy.trim().toUpperCase(),
    risk_pct: form.riskPct.trim(),
    ruin_pct: form.ruinPct.trim(),
  });
  if (form.rMultiples.trim()) params.set("r_multiples", form.rMultiples.replace(/\s+/g, ""));
  else if (form.strategyId.trim()) params.set("strategy_id", form.strategyId.trim());
  if (form.entries.trim()) params.set("entries", form.entries.replace(/\s+/g, ""));
  return `/api/trading/risk/calculator?${params.toString()}`;
}

export type AllocationView = Local<S["AllocationView"]>;
export const allocationPath = (basis: "backtest" | "paper") => `/api/trading/allocation?basis=${basis}`;

export type TradeNoteView = Local<S["TradeNoteView"]>;
export type TradeNoteDetailView = Local<S["TradeNoteDetailView"]>;
export type JournalVocabularyView = Local<S["JournalVocabularyView"]>;
export const JOURNAL_NOTES_PATH = "/api/trading/journal/notes";
export const JOURNAL_VOCAB_PATH = "/api/trading/journal/vocabulary";
export const journalNotePath = (tradeId: string) => `${JOURNAL_NOTES_PATH}/${encodeURIComponent(tradeId)}`;
export const NOTE_GRADES = ["A", "B", "C"] as const;
/** The backend's TradeNoteRequest bounds (routers/ops.py, ops/journal_notes.py). */
export const NOTE_MAX = 4000;
export const NOTE_REASON_MAX = 500;
