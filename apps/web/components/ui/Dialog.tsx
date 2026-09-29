"use client";

import { Dialog as Base } from "@base-ui/react/dialog";
import type { ComponentProps, ReactNode } from "react";
import { cn } from "./cn";

/**
 * Owned dialog parts on Base UI Dialog: focus trap, `inert` outside content,
 * scroll lock, and focus returned to whatever opened it.
 */
export const DialogRoot = Base.Root;
export const DialogClose = Base.Close;

export function DialogTitle({ className, ...rest }: ComponentProps<typeof Base.Title>) {
  return (
    <Base.Title
      className={cn("m-0 text-md font-semibold text-fg", className as string)}
      {...rest}
    />
  );
}

export function DialogDescription({ className, ...rest }: ComponentProps<typeof Base.Description>) {
  // A caller's class replaces the default rather than fighting it.
  return (
    <Base.Description
      className={(className as string | undefined) ?? "mt-1 text-sm text-fg-muted"}
      {...rest}
    />
  );
}

const BACKDROP =
  "fixed inset-0 z-[200] bg-[oklch(0_0_0/0.6)] transition-opacity duration-[var(--dur-lg)] " +
  "data-[starting-style]:opacity-0 data-[ending-style]:opacity-0 data-[ending-style]:duration-[var(--dur-lg-exit)]";

export function DialogContent({
  children,
  className,
  size = "md",
  testId,
  ...popupProps
}: Omit<ComponentProps<typeof Base.Popup>, "className" | "children"> & {
  children: ReactNode;
  className?: string;
  size?: "sm" | "md" | "lg";
  testId?: string;
}) {
  const width =
    size === "sm"
      ? "w-[min(420px,100%)]"
      : size === "lg"
        ? "w-[min(820px,100%)]"
        : "w-[min(620px,100%)]";
  return (
    <Base.Portal>
      <Base.Backdrop className={BACKDROP} />
      <Base.Viewport className="fixed inset-0 z-[201] flex items-start justify-center overflow-y-auto px-[var(--gutter)] py-6 sm:py-12">
        <Base.Popup
          data-testid={testId}
          className={cn(
            width,
            "relative rounded-lg border border-line-strong bg-raised p-4 text-fg shadow-e3 outline-none sm:p-5",
            "transition-[opacity,translate] duration-[var(--dur-lg)] ease-enter",
            "data-[starting-style]:translate-y-1 data-[starting-style]:opacity-0",
            "data-[ending-style]:opacity-0 data-[ending-style]:duration-[var(--dur-lg-exit)] data-[ending-style]:ease-exit",
            className,
          )}
          {...popupProps}
        >
          {children}
        </Base.Popup>
      </Base.Viewport>
    </Base.Portal>
  );
}
