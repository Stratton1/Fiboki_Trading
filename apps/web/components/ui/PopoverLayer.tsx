"use client";

import { Popover as Base } from "@base-ui/react/popover";
import type { ReactNode } from "react";
import { cn } from "./cn";

/** The Base UI half of <Popover>: positioned against the owner's trigger. */
export default function PopoverLayer({
  open,
  onOpenChange,
  anchor,
  id,
  title,
  children,
  side,
  align,
  className,
  testId,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  anchor: HTMLElement;
  id: string;
  title?: ReactNode;
  children: ReactNode;
  side: "top" | "right" | "bottom" | "left";
  align: "start" | "center" | "end";
  className?: string;
  testId?: string;
}) {
  return (
    <Base.Root
      open={open}
      onOpenChange={(next, details) => {
        // A press on our own trigger is not an "outside" press: the trigger's
        // click handler toggles, and closing here first would reopen it.
        if (
          !next &&
          details.reason === "outside-press" &&
          details.event.target instanceof Node &&
          anchor.contains(details.event.target)
        ) {
          return;
        }
        onOpenChange(next);
      }}
    >
      <Base.Portal>
        <Base.Positioner
          anchor={anchor}
          side={side}
          align={align}
          sideOffset={6}
          collisionPadding={8}
          className="z-[250]"
        >
          <Base.Popup
            id={id}
            data-testid={testId}
            finalFocus={() => anchor}
            className={cn(
              "popover-body max-w-[min(420px,calc(100vw-24px))] rounded-md border border-line-strong bg-overlay p-3 shadow-e3 outline-none",
              "origin-[var(--transform-origin)] transition-[opacity,scale] duration-[var(--dur-md)] ease-enter",
              "data-[starting-style]:scale-[0.98] data-[starting-style]:opacity-0",
              "data-[ending-style]:scale-[0.98] data-[ending-style]:opacity-0 data-[ending-style]:duration-[var(--dur-md-exit)]",
              className,
            )}
          >
            {title ? (
              <Base.Title className="mb-1.5 text-sm font-semibold text-fg">{title}</Base.Title>
            ) : null}
            {children}
          </Base.Popup>
        </Base.Positioner>
      </Base.Portal>
    </Base.Root>
  );
}
