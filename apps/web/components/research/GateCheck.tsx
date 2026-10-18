import { formatNumber } from "@/lib/format";
import type { GateCheckView } from "@/lib/research";
import { gateStatusTone } from "./tones";

const CMP: Record<string, string> = { gte: "≥", gt: ">", lte: "≤", lt: "<", ">=": "≥", "<=": "≤" };

/**
 * A gate's verdict beside the number it judged (feature 11): the badge's glyph
 * and a word (✓ PASS / ✕ FAIL / ◐ NOT EVALUATED), never colour alone, with the
 * threshold it was held to. The status is the one the gate set produced.
 */
/** A gate name with break opportunities after underscores (no mid-word breaks). */
export function gateName(name: string): string {
  return name.replace(/_/g, "_\u200b");
}

export function GateCheck({
  check,
  compact = false,
  showName = false,
}: {
  check: GateCheckView;
  compact?: boolean;
  /** Name the gate (beside a stat whose own label is a different quantity). */
  showName?: boolean;
}) {
  const tone = gateStatusTone(check.status);
  const cmp = CMP[check.comparison] ?? check.comparison;
  const threshold = check.threshold === null ? "" : `${cmp} ${formatNumber(check.threshold, "")}`;
  return (
    <span
      className={`rs-gate badge badge--${tone.tone}`}
      data-testid="gate-check"
      data-status={check.status}
      data-gate={check.name}
      title={check.detail || undefined}
    >
      {compact ? tone.label : `${tone.label}${showName ? ` ${gateName(check.name)}` : ""} ${threshold}`.trim()}
      <span className="sr-only">
        {" "}
        gate {check.name}: {check.detail}
      </span>
    </span>
  );
}
