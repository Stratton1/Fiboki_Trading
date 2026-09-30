import type { ValidationRow } from "@/lib/types";
import { ProvenanceChip } from "../ProvenanceChip";
import { ViewStateTag } from "../ui/ViewStateTag";

/**
 * The promotion ladder as a step meter, one segment per rung the report
 * records (V-4). Drawn from the fields GET /api/research/validation already
 * sends (routers/research.py ValidationSummary), and nothing else:
 *
 *  - `rungs_total` is the number of rungs in the report. The ladder records
 *    every rung, running or not (validation/ladder.py appends `not_reached`
 *    once it stops), so this is the ladder's length, RUNG 0 to RUNG 6 today;
 *  - `rungs_passed` counts the rungs whose outcome is PASS. The ladder stops
 *    at its first rung that does not pass, so those are rungs 0 to N−1 and
 *    rung N is where it stopped (failed or could not be computed: the summary
 *    does not say which);
 *  - `binding_constraint` is the report's own description of the one thing
 *    that stopped it (a gate or a rung), named beside the meter.
 *
 * No count is computed here beyond placing those two integers on segments.
 * A strategy with no report is NOT EVALUATED and draws no segments at all:
 * an unevaluated ladder is not a ladder at rung zero. Per-rung names and
 * per-gate statuses are not in the summary (a backend ask), so segments are
 * labelled by index only.
 */

export type RungState = "pass" | "stopped" | "not_reached";

export interface RungSegments {
  total: number;
  passed: number;
  states: RungState[];
}

/** The segments for a report, or null when there is no report to draw. */
export function rungSegments(row: Pick<ValidationRow, "available" | "rungs_passed" | "rungs_total">): RungSegments | null {
  const total = row.rungs_total.value;
  const passed = row.rungs_passed.value;
  if (!row.available || total === null || passed === null || total <= 0) return null;
  const states: RungState[] = [];
  for (let i = 0; i < total; i += 1) {
    states.push(i < passed ? "pass" : i === passed ? "stopped" : "not_reached");
  }
  return { total, passed, states };
}

const SEG_W = 22;
const SEG_H = 12;
const GAP = 3;

export function RungMeter({ row, compact = false }: { row: ValidationRow; compact?: boolean }) {
  const segments = rungSegments(row);
  if (segments === null) {
    return (
      <span className="rung-meter rung-meter--none" data-testid="rung-meter" data-state="not_evaluated">
        <ViewStateTag state="absent">NOT EVALUATED</ViewStateTag>
        {compact ? null : <span className="muted"> No validation report: this is not rung zero, and not a pass.</span>}
      </span>
    );
  }
  const { total, passed, states } = segments;
  const width = total * (SEG_W + GAP) - GAP;
  const stoppedAt = passed < total ? passed : null;
  const binding = row.binding_constraint && row.binding_constraint !== "none" ? row.binding_constraint : null;
  const summary =
    stoppedAt === null
      ? `${passed} of ${total} rungs passed`
      : `${passed} of ${total} rungs passed; stopped at rung ${stoppedAt}`;
  return (
    <span
      className="rung-meter"
      data-testid="rung-meter"
      data-state={stoppedAt === null ? "complete" : "stopped"}
      data-passed={passed}
      data-total={total}
      data-stopped-at={stoppedAt ?? undefined}
    >
      <svg
        className="rung-meter__svg"
        viewBox={`0 0 ${width} ${SEG_H + (compact ? 0 : 11)}`}
        width={width}
        height={SEG_H + (compact ? 0 : 11)}
        role="img"
        aria-label={`${summary}.${binding ? ` Binding: ${binding}.` : ""}`}
      >
        {states.map((state, i) => (
          <g key={i} data-testid="rung-segment" data-rung={i} data-state={state}>
            <rect
              className="rung-seg"
              data-state={state}
              x={i * (SEG_W + GAP)}
              y={0}
              width={SEG_W}
              height={SEG_H}
              rx={2}
            />
            {state === "stopped" ? (
              <text className="rung-seg__glyph" x={i * (SEG_W + GAP) + SEG_W / 2} y={SEG_H - 2.5} textAnchor="middle">
                ✕
              </text>
            ) : null}
            {compact ? null : (
              <text className="rung-seg__label" x={i * (SEG_W + GAP) + SEG_W / 2} y={SEG_H + 10} textAnchor="middle">
                {i}
              </text>
            )}
          </g>
        ))}
      </svg>
      <span className="rung-meter__text">
        <span className="num" data-testid="rung-meter-summary">
          {summary}
        </span>
        <ProvenanceChip provenance={row.rungs_passed.provenance} />
        {binding ? (
          <span className="rung-meter__binding" data-testid="rung-meter-binding">
            binding: {binding}
          </span>
        ) : stoppedAt === null && row.verdict !== "promote" ? (
          <span className="rung-meter__binding">every rung passed; the verdict is the report&apos;s</span>
        ) : null}
      </span>
    </span>
  );
}
