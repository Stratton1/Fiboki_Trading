"use client";

import { Popover as Base } from "@base-ui/react/popover";
import { useRef, type ReactNode } from "react";
import { cn } from "./cn";

/**
 * Where focus goes when the popup has finished closing: back to the trigger,
 * UNLESS the operator has already moved on.
 *
 * Base UI returns focus when the popup unmounts, which is at the END of the
 * exit transition, not when Escape is pressed. An explicit `finalFocus`
 * function also bypasses Base UI's own "focus moved elsewhere, leave it"
 * guard. Together they made a ~150 ms dead zone after closing a popover
 * (inventory F-6): a Select opened in that window was closed again when focus
 * was yanked back to the old trigger. So the return is conditional: only when
 * focus is still inside this popup, on its trigger, or nowhere (body).
 */
export function returnFocusTarget(anchor: HTMLElement, popup: HTMLElement | null): HTMLElement | false {
  const active = anchor.ownerDocument.activeElement;
  const nowhere = active === null || active === anchor.ownerDocument.body;
  const stillOurs = active === anchor || (popup !== null && active !== null && popup.contains(active));
  return nowhere || stillOurs ? anchor : false;
}

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
  const popup = useRef<HTMLDivElement>(null);
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
            ref={popup}
            id={id}
            data-testid={testId}
            finalFocus={() => returnFocusTarget(anchor, popup.current)}
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
