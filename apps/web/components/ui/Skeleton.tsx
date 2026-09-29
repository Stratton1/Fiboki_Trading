import { cn } from "./cn";

/** A loading placeholder bar. The shimmer stops under reduced motion. */
export function Skeleton({ className }: { className?: string }) {
  return <div aria-hidden="true" className={cn("skeleton", className)} />;
}
