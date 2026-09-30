import type { ExposureRow, Figure, Provenance, RiskStateView } from "./types";

/**
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

export interface FamilyMeta {
  id: FamilyId;
  title: string;
  /** Where the family's rows come from. */
  source: "risk" | "exposure" | "none";
}

/** Canonical family order: the tie-break when utilisation does not decide. */
export const FAMILIES: readonly FamilyMeta[] = [
  { id: "account", title: "Account", source: "none" },
  { id: "loss", title: "Daily / weekly loss", source: "risk" },
  { id: "drawdown", title: "Drawdown", source: "risk" },
  { id: "margin", title: "Margin", source: "risk" },
  { id: "correlation", title: "Correlation", source: "none" },
  { id: "exposure", title: "Exposure", source: "exposure" },
  { id: "data_quality", title: "Data quality", source: "none" },
];

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

function absent(
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

/** Rows from GET /api/trading/risk (RiskStateView): loss, drawdown and margin. */
export function riskRows(risk: RiskStateView): LimitRow[] {
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

  rows.push(
    absent(
      "loss",
      "risk:weekly_loss",
      "Weekly loss",
      "Not reported: /api/trading/risk carries the daily loss only. The gateway has a weekly-loss check, but no weekly figure or limit reaches the workstation.",
    ),
  );

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

  // Margin: the API sends a utilisation figure (usually missing, with its
  // reason) and no margin limit at all.
  const margin = risk.margin_utilisation_pct;
  rows.push(
    absent(
      "margin",
      "risk:margin",
      "Margin utilisation",
      margin.value === null
        ? (missingReason(margin) ?? "No margin figure was reported.")
        : "The API reports margin utilisation but no margin limit, so how close it is to one is unknown.",
      null,
      margin,
    ),
  );

  return rows;
}

/**
 * Limits the platform enforces but the API does not report, whatever the
 * sources say. Listed so that their absence is visible, never drawn as bars.
 */
export function unreportedRows(): LimitRow[] {
  return [
    absent(
      "account",
      "none:account_risk",
      "Open risk against the account limit",
      "Not reported: the API exposes no open-risk (risk at stop) figure and no account-risk limit.",
    ),
    absent(
      "correlation",
      "none:correlated_exposure",
      "Correlated exposure",
      "Not reported: the gateway checks correlated exposure per order, but no correlated-exposure figure or limit is exposed by the API.",
    ),
    absent(
      "data_quality",
      "none:data_quality",
      "Data-quality tolerances",
      "Not reported: no data-quality limit utilisation is exposed by the API.",
      { href: "/markets/data-quality", label: "Data Quality" },
    ),
  ];
}

/** The kind of an exposure bucket, from the API's key (`instrument:EURUSD`). */
export function exposureKind(row: ExposureRow): string {
  const colon = row.key.indexOf(":");
  return colon === -1 ? "bucket" : row.key.slice(0, colon);
}

/** Rows from GET /api/trading/exposure: the API's utilisation, never recomputed. */
export function exposureRows(items: readonly ExposureRow[]): LimitRow[] {
  return items.map((row) => {
    const utilisation = row.utilisation_pct.value;
    const value = row.exposure_pct;
    const limit = row.limit_pct;
    if (utilisation === null || value.value === null || limit.value === null) {
      return {
        ...absent(
          "exposure",
          `exposure:${row.key}`,
          row.label,
          missingReason(value) ?? missingReason(row.utilisation_pct) ?? "The API returned no utilisation for this bucket.",
          null,
          value,
        ),
        kind: exposureKind(row),
        limit,
        breached: row.breached,
        state: row.breached ? "breached" : "absent",
      };
    }
    return {
      key: `exposure:${row.key}`,
      family: "exposure",
      label: row.label,
      kind: exposureKind(row),
      value,
      limit,
      utilisation,
      utilisationFrom: "api",
      headroom: limit.value - value.value,
      formula: "used: the API's utilisation; headroom = limit − exposure",
      provenance: value.provenance,
      estimated: value.estimated || row.utilisation_pct.estimated,
      breached: row.breached,
      state: bandOf(utilisation, row.breached),
      absentReason: null,
      seeAlso: null,
    };
  });
}

/** For ordering: breached above everything, absent below everything. */
function urgency(row: LimitRow): number {
  const BREACH = 1e9;
  if (row.breached) return row.utilisation === null ? BREACH : BREACH + row.utilisation;
  return row.utilisation === null ? Number.NEGATIVE_INFINITY : row.utilisation;
}

/** Most used first; absent rows last, in their given order (Array.sort is stable). */
export function byUtilisation(rows: readonly LimitRow[]): LimitRow[] {
  return [...rows].sort((a, b) => {
    const ua = urgency(a);
    const ub = urgency(b);
    if (ua === ub) return 0;
    return ub > ua ? 1 : -1;
  });
}

export interface FamilyGroup {
  meta: FamilyMeta;
  rows: LimitRow[];
  /** The family's most-used row, or null when none is measured. */
  worst: LimitRow | null;
}

/**
 * Group rows by family, most-used row first within a family, and order the
 * families by their most-used row. Families with nothing measured follow, in
 * the canonical order. Only the families in `include` are returned (a family
 * whose source failed or is loading is rendered by the caller instead).
 */
export function groupFamilies(rows: readonly LimitRow[], include: readonly FamilyId[]): FamilyGroup[] {
  const groups = FAMILIES.filter((meta) => include.includes(meta.id)).map((meta) => {
    const sorted = byUtilisation(rows.filter((r) => r.family === meta.id));
    const first = sorted[0];
    const worst = first !== undefined && first.state !== "absent" ? first : null;
    return { meta, rows: sorted, worst };
  });
  return groups
    .map((group, index) => ({ group, index }))
    .sort((a, b) => {
      const ua = a.group.worst ? urgency(a.group.worst) : Number.NEGATIVE_INFINITY;
      const ub = b.group.worst ? urgency(b.group.worst) : Number.NEGATIVE_INFINITY;
      if (ua === ub) return a.index - b.index;
      return ub > ua ? 1 : -1;
    })
    .map(({ group }) => group);
}

/** The single row closest to (or furthest over) its limit, among measured rows. */
export function closestToLimit(rows: readonly LimitRow[]): LimitRow | null {
  const first = byUtilisation(rows.filter((r) => r.state !== "absent"))[0];
  return first === undefined ? null : first;
}

/** The bar's filled length, 0 to 100 (a breach fills the track). */
export function barLength(row: LimitRow): number {
  if (row.breached) return 100;
  if (row.utilisation === null) return 0;
  return Math.min(100, Math.max(0, row.utilisation));
}

/** Heat bin (0 to 5) for the exposure matrix: 20% of the limit per step. */
export function utilisationBin(utilisation: number): number {
  if (!(utilisation > 0)) return 0;
  return Math.min(5, Math.ceil(utilisation / 20));
}
