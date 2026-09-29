import type { ReactNode } from "react";

/**
 * A successful, empty result. Visually distinct from loading (solid heading,
 * no shimmer) and from error (no critical colour): an empty answer is an
 * answer, and must never be confused with a failure to get one.
 */
export function EmptyState({
  title,
  children,
  badge,
  testId = "state-empty",
  ...data
}: {
  title: ReactNode;
  children?: ReactNode;
  badge?: ReactNode;
  testId?: string;
} & Record<`data-${string}`, string | boolean | undefined>) {
  return (
    <div className="state state--empty" data-testid={testId} {...data}>
      {badge}
      <div className="state__title">
        <span>{title}</span>
        <span className="badge badge--unknown">EMPTY</span>
      </div>
      {children ? <div className="state__body">{children}</div> : null}
    </div>
  );
}
