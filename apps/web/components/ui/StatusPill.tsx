import { CircleAlert, CircleCheck, CircleHelp, CircleX } from "lucide-react";
import type { ReactNode } from "react";
import { cn } from "./cn";

export type Tone = "ok" | "warn" | "critical" | "unknown";

const TONE: Record<Tone, string> = {
  ok: "text-ok",
  warn: "text-warn",
  critical: "text-critical",
  unknown: "text-unknown",
};

const ICON = {
  ok: CircleCheck,
  warn: CircleAlert,
  critical: CircleX,
  unknown: CircleHelp,
} as const;

/**
 * Health or severity, always as glyph + word + colour. The worst-wins roll-up
 * is the backend's; this renders the tone it is handed and decides nothing.
 */
export function StatusPill({
  tone,
  children,
  testId,
  className,
}: {
  tone: Tone;
  children: ReactNode;
  testId?: string;
  className?: string;
}) {
  const Icon = ICON[tone];
  return (
    <span
      data-testid={testId}
      data-tone={tone}
      className={cn(
        "inline-flex items-center gap-1 rounded-xs border border-current px-1.5 text-2xs font-semibold uppercase tracking-[0.06em]",
        TONE[tone],
        className,
      )}
    >
      <Icon size={11} aria-hidden="true" strokeWidth={2.25} />
      {children}
    </span>
  );
}

/** A neutral label: a tag, a version, a count. Carries no state meaning. */
export function Badge({ children, className }: { children: ReactNode; className?: string }) {
  return (
    <span
      className={cn(
        "inline-flex items-center gap-1 rounded-xs border border-line-strong px-1.5 text-2xs font-medium text-fg-muted",
        className,
      )}
    >
      {children}
    </span>
  );
}
