import type { Severity } from "@/lib/types";

const CLASS: Record<Severity, string> = {
  critical: "badge--down",
  error: "badge--down",
  warning: "badge--degraded",
  info: "badge--neutral",
};

/** A severity as the platform stated it: word always, colour second. */
export function SeverityBadge({ severity }: { severity: string }) {
  const known = severity in CLASS ? CLASS[severity as Severity] : "badge--unknown";
  return (
    <span className={`badge ${known}`} data-severity={severity}>
      {severity.toUpperCase()}
    </span>
  );
}
