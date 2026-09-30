import type { Figure, Provenance, RiskStateView } from "./types";

/**
 * The limit rows every screen draws the same way (Risk & Exposure, Command):
 * the types, the display bands, and the daily-loss and drawdown rows. Split
 * from lib/limits.ts so Command's first load carries only these; lib/limits.ts
 * re-exports all of it and adds the board's families, exposure and ordering.
 *
 * The limit board's rows (Risk & Exposure v2), built from what the API sends.
 *
 * This module DECIDES nothing. The gateway decides whether an order may go;
 * these rows only place the API's figures on a bar. Three things are derived
 * here, each shown on screen with its formula next to it:
 *
 *  - utilisation of the daily-loss and drawdown limits. /api/trading/risk sends
 *    the value and the limit but no utilisation (ExposureRow does send one, and
 *    for exposure the API's own is used). The derivation follows the rule the
 *    API itself applies when it lists a breach (routers/trading.py
 *    `risk_state`): a daily breach is `daily_pct < -max_daily_loss_pct`, a
 *    drawdown breach is `drawdown_pct > max_drawdown_limit_pct`;
 *  - headroom, the distance from the value to that same boundary;
 *  - the display band (ok / warn / critical), from WARN_AT_PCT and
 *    CRITICAL_AT_PCT below.
 *
 * A derived number carries the provenance of the figure it came from and is
 * estimated when that figure is. A limit the API does not report at all is an
 * `absent` row with the reason, never a zero bar.
 */

/**
 * Display bands, as a percentage of the limit.
 *
 * The API supplies no warning or critical threshold for any limit (neither
 * RiskStateView nor ExposureRow has one), so these are the workstation's own
 * defaults: 70% and 90%. They colour a bar and nothing else; no control reads
 * them. When the API sends per-limit thresholds, use those instead.
 */
export const WARN_AT_PCT = 70;
export const CRITICAL_AT_PCT = 90;

export type LimitState = "ok" | "warn" | "critical" | "breached" | "absent";

export type FamilyId =
  | "account"
  | "loss"
  | "drawdown"
  | "margin"
  | "correlation"
  | "exposure"
  | "data_quality";

export interface LimitRow {
  key: string;
  family: FamilyId;
  label: string;
  /** Exposure rows: instrument, strategy or currency (the API's key prefix). */
  kind: string | null;
  /** The measured value, exactly as the API sent it. */
  value: Figure | null;
  /** The limit, exactly as the API sent it. */
  limit: Figure | null;
  /** Percent of the limit used. */
  utilisation: number | null;
  /** "api": the API's own `utilisation_pct`; "derived": computed here (see `formula`). */
  utilisationFrom: "api" | "derived" | null;
  /** Distance from the value to the breach boundary, in the value's unit (percentage points). */
  headroom: number | null;
  /** How utilisation and headroom were obtained, in words, for the row. */
  formula: string | null;
  provenance: Provenance | null;
  estimated: boolean;
  breached: boolean;
  state: LimitState;
  /** Why an absent row is absent. */
  absentReason: string | null;
  /** Where to look instead, for an absent row. */
  seeAlso: { href: string; label: string } | null;
}

/** The band for a utilisation. `breached` is the API's (or its rule's) verdict. */
export function bandOf(utilisation: number | null, breached: boolean): LimitState {
  if (breached) return "breached";
  if (utilisation === null) return "absent";
  if (utilisation >= CRITICAL_AT_PCT) return "critical";
  if (utilisation >= WARN_AT_PCT) return "warn";
  return "ok";
}

/** The reason the API attached to a missing figure (Figure.missing's caveat). */
export function missingReason(figure: Figure): string | null {
  if (figure.value !== null) return null;
  const caveat = figure.caveats.find((c) => c.code === "value_unavailable");
  return caveat ? caveat.message : "The API returned no value for this figure.";
}

export function absent(
  family: FamilyId,
  key: string,
  label: string,
  reason: string,
  seeAlso: LimitRow["seeAlso"] = null,
  value: Figure | null = null,
): LimitRow {
  return {
    key,
    family,
    label,
    kind: null,
    value,
    limit: null,
    utilisation: null,
    utilisationFrom: null,
    headroom: null,
    formula: null,
    provenance: value ? value.provenance : null,
    estimated: value ? value.estimated : false,
    breached: false,
    state: "absent",
    absentReason: reason,
    seeAlso,
  };
}

/** The two loss-limit rows (daily loss, total drawdown) from GET /api/trading/risk. */
export function lossRows(risk: RiskStateView): LimitRow[] {
  const rows: LimitRow[] = [];

  // Daily loss. `daily_loss_pct` is the day's net P&L as a signed percentage
  // of the starting balance (negative is a loss); the API breaches when it
  // falls below minus the limit.
  const daily = risk.daily_loss_pct;
  const dailyLimit = risk.max_daily_loss_pct;
  if (daily.value === null || dailyLimit.value === null || !(dailyLimit.value > 0)) {
    rows.push(
      absent(
        "loss",
        "risk:daily_loss",
        "Daily loss",
        missingReason(daily) ?? missingReason(dailyLimit) ?? "The API reported no usable daily-loss limit.",
        null,
        daily,
      ),
    );
  } else {
    const used = Math.max(0, -daily.value);
    const utilisation = (used / dailyLimit.value) * 100;
    const breached = daily.value < -dailyLimit.value;
    rows.push({
      key: "risk:daily_loss",
      family: "loss",
      label: "Daily loss",
      kind: null,
      value: daily,
      limit: dailyLimit,
      utilisation,
      utilisationFrom: "derived",
      headroom: daily.value + dailyLimit.value,
      formula:
        "used = the day's loss ÷ the limit; headroom = day P&L + limit (the API breaches below −limit)",
      provenance: daily.provenance,
      estimated: daily.estimated || dailyLimit.estimated,
      breached,
      state: bandOf(utilisation, breached),
      absentReason: null,
      seeAlso: null,
    });
  }

  // Total drawdown: breached above the limit.
  const dd = risk.drawdown_pct;
  const ddLimit = risk.max_drawdown_limit_pct;
  if (dd.value === null || ddLimit.value === null || !(ddLimit.value > 0)) {
    rows.push(
      absent(
        "drawdown",
        "risk:drawdown",
        "Total drawdown",
        missingReason(dd) ?? missingReason(ddLimit) ?? "The API reported no usable drawdown limit.",
        null,
        dd,
      ),
    );
  } else {
    const utilisation = (dd.value / ddLimit.value) * 100;
    const breached = dd.value > ddLimit.value;
    rows.push({
      key: "risk:drawdown",
      family: "drawdown",
      label: "Total drawdown",
      kind: null,
      value: dd,
      limit: ddLimit,
      utilisation,
      utilisationFrom: "derived",
      headroom: ddLimit.value - dd.value,
      formula: "used = drawdown ÷ limit; headroom = limit − drawdown (the API breaches above the limit)",
      provenance: dd.provenance,
      estimated: dd.estimated || ddLimit.estimated,
      breached,
      state: bandOf(utilisation, breached),
      absentReason: null,
      seeAlso: null,
    });
  }

  return rows;
}

/** The bar's filled length, 0 to 100 (a breach fills the track). */
export function barLength(row: LimitRow): number {
  if (row.breached) return 100;
  if (row.utilisation === null) return 0;
  return Math.min(100, Math.max(0, row.utilisation));
}

