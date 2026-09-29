"use client";

import { Dialog as Base } from "@base-ui/react/dialog";
import { X } from "lucide-react";
import type { ReactNode } from "react";
import { cn } from "./cn";

/**
 * The Base UI half of <Sheet> (loaded on first open; see layers.ts).
 *
 * A panel that slides in from the right edge: the inspector. A positioned
 * Base UI Dialog, so it traps focus, closes on Escape and returns focus to
 * whatever opened it.
 */
export default function SheetLayer({
  open,
  onOpenChange,
  title,
  children,
  testId = "sheet",
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  title: ReactNode;
  children: ReactNode;
  testId?: string;
}) {
  return (
    <Base.Root open={open} onOpenChange={(next) => onOpenChange(next)}>
      <Base.Portal>
        <Base.Backdrop className="fixed inset-0 z-[200] bg-[oklch(0_0_0/0.4)] transition-opacity duration-[var(--dur-lg)] data-[starting-style]:opacity-0 data-[ending-style]:opacity-0 data-[ending-style]:duration-[var(--dur-lg-exit)]" />
        <Base.Popup
          data-testid={testId}
          className={cn(
            "fixed inset-y-0 right-0 z-[201] flex w-[min(440px,100vw)] flex-col border-l border-line-strong bg-raised text-fg shadow-e3 outline-none",
            "transition-transform duration-[var(--dur-xl)] ease-enter",
            "data-[starting-style]:translate-x-full data-[ending-style]:translate-x-full data-[ending-style]:duration-[var(--dur-xl-exit)] data-[ending-style]:ease-exit",
          )}
        >
          <div className="flex items-center gap-2 border-b border-line px-4 py-3">
            <Base.Title className="m-0 flex-1 text-md font-semibold">{title}</Base.Title>
            <Base.Close
              aria-label="Close inspector"
              className="inline-flex h-7 w-7 items-center justify-center rounded-sm text-fg-muted hover:bg-overlay hover:text-fg"
            >
              <X size={16} aria-hidden="true" />
            </Base.Close>
          </div>
          <div className="flex-1 overflow-y-auto p-4">{children}</div>
        </Base.Popup>
      </Base.Portal>
    </Base.Root>
  );
}
