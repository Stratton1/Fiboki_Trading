/**
 * Badge tones for server-supplied VALUES (report G W-06).
 *
 * A badge's colour must come from the value it shows, never from a sibling
 * flag. The validation page used to colour the verdict from `available`, so a
 * strategy the ladder REJECTED (a real, available report) wore the green OK
 * badge. Each map below is exhaustive over the values the backend defines
 * (the `Record` over a closed union fails typecheck if one is missing), and
 * any value it does not recognise renders as unknown, labelled as such,
 * never as OK.
 */

export type BadgeTone = "ok" | "degraded" | "down" | "unknown";

export interface ToneLabel {
  tone: BadgeTone;
  label: string;
  /** False when the value is not one the workstation knows. */
  known: boolean;
}

/** `validation/report.py` Verdict, plus the API's two non-report states. */
export type Verdict = "promote" | "reject" | "incomplete" | "not_validated" | "unreadable";

const VERDICT: Record<Verdict, Omit<ToneLabel, "known">> = {
  promote: { tone: "ok", label: "PROMOTE" },
  reject: { tone: "down", label: "REJECT" },
  incomplete: { tone: "degraded", label: "INCOMPLETE" },
  not_validated: { tone: "unknown", label: "NOT VALIDATED" },
  unreadable: { tone: "degraded", label: "UNREADABLE" },
};

/**
 * Data quality as `routers/markets.py` reports it today (`unknown` when no
 * dataset is mounted, `pending` when the integrity report is not wired), and
 * the states the integrity report will add.
 */
export type Quality = "unknown" | "pending" | "validated" | "warnings" | "defective";

const QUALITY: Record<Quality, Omit<ToneLabel, "known">> = {
  unknown: { tone: "unknown", label: "UNKNOWN" },
  pending: { tone: "unknown", label: "PENDING" },
  validated: { tone: "ok", label: "VALIDATED" },
  warnings: { tone: "degraded", label: "WARNINGS" },
  defective: { tone: "down", label: "DEFECTIVE" },
};

function lookup<K extends string>(
  table: Record<K, Omit<ToneLabel, "known">>,
  value: string,
): ToneLabel {
  if (Object.prototype.hasOwnProperty.call(table, value)) {
    return { ...table[value as K], known: true };
  }
  return {
    tone: "unknown",
    label: value ? `UNRECOGNISED: ${value.toUpperCase()}` : "NOT SUPPLIED",
    known: false,
  };
}

export function verdictTone(verdict: string): ToneLabel {
  return lookup(VERDICT, verdict);
}

export function qualityTone(quality: string): ToneLabel {
  return lookup(QUALITY, quality);
}
