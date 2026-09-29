import type { ReactNode } from "react";
import type { Caveat } from "@/lib/types";
import { CaveatList } from "./CaveatList";
import { Popover } from "./ui/Popover";

/**
 * Caveats and reasons, reachable by keyboard and touch (report G W-15, plan
 * §3 "no tooltip-only information").
 *
 * A `title` attribute is invisible to a keyboard user, never appears on touch,
 * and does not appear at all on a disabled control. Anything an operator needs
 * in order to trust or act on a figure (a realism caveat, the reason a
 * candidate cannot be promoted) goes behind a real button that opens a
 * popover, with an accessible name that says what is behind it.
 *
 *  - `caveats`: server-computed caveats, rendered in full by CaveatList;
 *  - `reasons`: plain server-supplied reasons (blocking reasons), as a list.
 *
 * The trigger is a compact, focusable button. When there is nothing to show
 * it renders nothing: no empty "0 caveats" control.
 */
export function CaveatPopover({
  caveats = [],
  reasons = [],
  title,
  label,
  accessibleName,
  testId = "caveat-popover",
  severity,
}: {
  caveats?: Caveat[];
  reasons?: string[];
  /** The popover heading. */
  title: string;
  /** The visible trigger content; defaults to ⚠ and the count. */
  label?: ReactNode;
  /** What a screen reader hears for the trigger. */
  accessibleName?: string;
  testId?: string;
  severity?: "warning" | "critical" | "info";
}) {
  const count = caveats.length + reasons.length;
  if (count === 0) return null;
  const tone =
    severity ??
    (caveats.some((c) => c.severity === "critical")
      ? "critical"
      : caveats.length > 0 && caveats.every((c) => c.severity === "info")
        ? "info"
        : "warning");
  return (
    <Popover
      title={title}
      testId={testId}
      trigger={
        <button
          type="button"
          className="caveat-trigger"
          data-testid={`${testId}-trigger`}
          data-severity={tone}
          aria-label={accessibleName ?? `${title}: ${count} item${count === 1 ? "" : "s"}. Show them.`}
        >
          {label ?? `⚠${count}`}
        </button>
      }
    >
      {caveats.length > 0 ? <CaveatList caveats={caveats} /> : null}
      {reasons.length > 0 ? (
        <ul className="caveat-reasons" data-testid={`${testId}-reasons`}>
          {reasons.map((reason) => (
            <li key={reason}>{reason}</li>
          ))}
        </ul>
      ) : null}
    </Popover>
  );
}
