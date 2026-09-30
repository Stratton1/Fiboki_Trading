import type { ExposureRow, RiskStateView } from "./types";
import { absent, bandOf, lossRows, missingReason, type FamilyId, type LimitRow } from "./limits-core";

export * from "./limits-core";

/**
 * The limit board's families, exposure rows and ordering, over the rows in
 * lib/limits-core.ts (which Command uses on its own).
 */

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

/** Rows from GET /api/trading/risk (RiskStateView): loss, drawdown and margin. */
export function riskRows(risk: RiskStateView): LimitRow[] {
  const [daily, drawdown] = lossRows(risk);
  const rows: LimitRow[] = [];
  if (daily) rows.push(daily);
  rows.push(
    absent(
      "loss",
      "risk:weekly_loss",
      "Weekly loss",
      "Not reported: /api/trading/risk carries the daily loss only. The gateway has a weekly-loss check, but no weekly figure or limit reaches the workstation.",
    ),
  );

  if (drawdown) rows.push(drawdown);

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

/** Heat bin (0 to 5) for the exposure matrix: 20% of the limit per step. */
export function utilisationBin(utilisation: number): number {
  if (!(utilisation > 0)) return 0;
  return Math.min(5, Math.ceil(utilisation / 20));
}
