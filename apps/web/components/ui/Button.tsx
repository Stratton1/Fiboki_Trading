import type { ComponentProps } from "react";
import { cn } from "./cn";

export type ButtonVariant = "secondary" | "primary" | "danger" | "warn" | "ghost";
export type ButtonSize = "sm" | "md" | "icon";

const BASE =
  "inline-flex items-center justify-center gap-1.5 rounded-sm border font-medium whitespace-nowrap select-none " +
  "transition-[background-color,border-color,color] duration-[var(--dur-xs)] ease-standard " +
  "disabled:opacity-45 disabled:pointer-events-none " +
  // aria-disabled keeps the control focusable, so the reason it cannot be
  // pressed stays reachable by keyboard (report G W-15).
  "aria-disabled:opacity-45 aria-disabled:cursor-not-allowed";

const VARIANT: Record<ButtonVariant, string> = {
  secondary: "border-line-control bg-raised text-fg hover:bg-overlay",
  primary: "border-accent bg-accent-bg text-fg hover:border-focus",
  danger: "border-critical bg-transparent text-critical hover:bg-critical-bg",
  warn: "border-warn bg-transparent text-warn hover:bg-warn-bg",
  ghost: "border-transparent bg-transparent text-fg-muted hover:bg-raised hover:text-fg",
};

const SIZE: Record<ButtonSize, string> = {
  sm: "h-6 px-2 text-xs",
  md: "h-[var(--control-h)] px-3 text-sm",
  // Square, no padding: an icon at 16px must not be squeezed by px-3.
  icon: "h-[var(--control-h)] w-[var(--control-h)] shrink-0 p-0 text-sm",
};

export function buttonClass(variant: ButtonVariant = "secondary", size: ButtonSize = "md") {
  return cn(BASE, VARIANT[variant], SIZE[size]);
}

/** The one button. `type` defaults to "button" so no button submits a form by accident. */
export function Button({
  variant = "secondary",
  size = "md",
  className,
  type = "button",
  ...rest
}: ComponentProps<"button"> & { variant?: ButtonVariant; size?: ButtonSize }) {
  return <button type={type} className={cn(buttonClass(variant, size), className)} {...rest} />;
}
