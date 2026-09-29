import type { ReactNode } from "react";

/** A keyboard key, e.g. <Kbd>⌘</Kbd><Kbd>⇧</Kbd><Kbd>D</Kbd>. */
export function Kbd({ children }: { children: ReactNode }) {
  return (
    <kbd className="inline-flex min-w-[18px] items-center justify-center rounded-xs border border-line-strong bg-sunken px-1 font-sans text-2xs text-fg-muted">
      {children}
    </kbd>
  );
}

/** A chord such as ⌘⇧D, rendered as separate keys with an accessible name. */
export function Shortcut({ keys, label }: { keys: string[]; label: string }) {
  return (
    <span className="inline-flex items-center gap-0.5" aria-label={label} role="img">
      {keys.map((key) => (
        <Kbd key={key}>{key}</Kbd>
      ))}
    </span>
  );
}
