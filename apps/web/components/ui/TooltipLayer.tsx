"use client";

import { Tooltip as Base } from "@base-ui/react/tooltip";
import type { ReactNode } from "react";

/** The Base UI half of <Tooltip>: positioned against the owner's trigger. */
export default function TooltipLayer({
  open,
  onOpenChange,
  anchor,
  id,
  label,
  side,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  anchor: HTMLElement;
  id: string;
  label: ReactNode;
  side: "top" | "right" | "bottom" | "left";
}) {
  return (
    <Base.Root
      open={open}
      onOpenChange={(next, details) => {
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
        <Base.Positioner anchor={anchor} side={side} sideOffset={8} className="z-[250]">
          <Base.Popup
            id={id}
            role="tooltip"
            data-testid="tooltip"
            className="pointer-events-none rounded-sm border border-line-strong bg-overlay px-2 py-1 text-xs text-fg shadow-e3 transition-opacity duration-[var(--dur-sm)] data-[ending-style]:opacity-0 data-[ending-style]:duration-[var(--dur-sm-exit)] data-[starting-style]:opacity-0"
          >
            {label}
          </Base.Popup>
        </Base.Positioner>
      </Base.Portal>
    </Base.Root>
  );
}
