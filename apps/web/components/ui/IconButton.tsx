import type { ComponentProps, ReactNode } from "react";
import { buttonClass, type ButtonVariant } from "./Button";
import { cn } from "./cn";
import { Tooltip } from "./Tooltip";

/**
 * A square button whose only content is an icon. `label` is mandatory: it is
 * the accessible name, and the tooltip repeats it for sighted users.
 */
export function IconButton({
  label,
  children,
  variant = "ghost",
  tooltip = true,
  side = "top",
  className,
  type = "button",
  ...rest
}: Omit<ComponentProps<"button">, "aria-label"> & {
  label: string;
  children: ReactNode;
  variant?: ButtonVariant;
  tooltip?: boolean;
  side?: "top" | "right" | "bottom" | "left";
}) {
  const button = (
    <button
      type={type}
      aria-label={label}
      className={cn(buttonClass(variant, "icon"), className)}
      {...rest}
    >
      {children}
    </button>
  );
  return tooltip ? (
    <Tooltip label={label} side={side}>
      {button}
    </Tooltip>
  ) : (
    button
  );
}
