import type { BadgeTone, ToneLabel } from "@/lib/tones";

/**
 * Badge tones for the research analytics values, exhaustive over what the API
 * sends (routers/research_analytics.py and agents/jobs.py). An unrecognised
 * value renders unknown and says so; it is never green.
 */
function lookup(table: Record<string, [BadgeTone, string]>, value: string): ToneLabel {
  const hit = Object.prototype.hasOwnProperty.call(table, value) ? table[value] : undefined;
  if (hit) return { tone: hit[0], label: hit[1], known: true };
  return {
    tone: "unknown",
    label: value ? `UNRECOGNISED: ${value.toUpperCase()}` : "NOT SUPPLIED",
    known: false,
  };
}

/** A deterministic gate verdict filed against a run (ValidationReportRecord.verdict). */
const GATE_VERDICT: Record<string, [BadgeTone, string]> = {
  pass: ["ok", "GATES PASS"],
  fail: ["down", "GATES FAIL"],
  inconclusive: ["degraded", "INCONCLUSIVE"],
  not_evaluated: ["unknown", "NOT EVALUATED"],
};

/** One gate's status (validation/gates.py GateStatus). */
const GATE_STATUS: Record<string, [BadgeTone, string]> = {
  pass: ["ok", "PASS"],
  fail: ["down", "FAIL"],
  not_evaluated: ["degraded", "NOT EVALUATED"],
  not_applicable: ["unknown", "N/A"],
};

/** A ladder rung outcome (validation/ladder.py). */
const RUNG: Record<string, [BadgeTone, string]> = {
  pass: ["ok", "PASS"],
  fail: ["down", "FAIL"],
  not_reached: ["unknown", "NOT REACHED"],
  error: ["down", "ERROR"],
  not_evaluated: ["degraded", "NOT EVALUATED"],
  inconclusive: ["degraded", "INCONCLUSIVE"],
};

/** A job's status (agents/orchestrator.py JobStatus). */
const JOB: Record<string, [BadgeTone, string]> = {
  running: ["ok", "RUNNING"],
  pending: ["unknown", "QUEUED"],
  succeeded: ["ok", "DONE"],
  failed: ["degraded", "FAILED, WILL RETRY"],
  dead_letter: ["down", "DEAD LETTER"],
};

/** The ladder report's own verdict (validation/report.py). */
const LADDER: Record<string, [BadgeTone, string]> = {
  promote: ["ok", "PROMOTE"],
  reject: ["down", "REJECT"],
  incomplete: ["degraded", "INCOMPLETE"],
};

export const gateVerdictTone = (v: string) => lookup(GATE_VERDICT, v);
export const gateStatusTone = (v: string) => lookup(GATE_STATUS, v);
export const rungTone = (v: string) => lookup(RUNG, v);
export const jobTone = (v: string) => lookup(JOB, v);
export const ladderTone = (v: string) => lookup(LADDER, v);
