"use client";

import { Menu as Base } from "@base-ui/react/menu";
import { Check } from "lucide-react";
import type { ComponentProps, ReactElement, ReactNode } from "react";
import { cn } from "./cn";

/** A menu of actions. Items never execute a trading mutation directly; those open ConfirmDialog. */
export function Menu({
  trigger,
  children,
  side = "bottom",
  align = "start",
}: {
  trigger: ReactElement;
  children: ReactNode;
  side?: "top" | "right" | "bottom" | "left";
  align?: "start" | "center" | "end";
}) {
  return (
    <Base.Root>
      <Base.Trigger render={trigger} />
      <Base.Portal>
        <Base.Positioner side={side} align={align} sideOffset={6} className="z-[250]">
          <Base.Popup className="min-w-48 rounded-md border border-line-strong bg-overlay p-1 text-sm text-fg shadow-e3 outline-none transition-opacity duration-[var(--dur-md)] data-[starting-style]:opacity-0 data-[ending-style]:opacity-0 data-[ending-style]:duration-[var(--dur-md-exit)]">
            {children}
          </Base.Popup>
        </Base.Positioner>
      </Base.Portal>
    </Base.Root>
  );
}

const ITEM =
  "flex cursor-default items-center gap-2 rounded-sm px-2 py-1 outline-none select-none data-[highlighted]:bg-raised data-[disabled]:opacity-45";

export function MenuItem({ className, ...rest }: ComponentProps<typeof Base.Item>) {
  return <Base.Item className={cn(ITEM, className as string)} {...rest} />;
}

export function MenuSeparator() {
  return <Base.Separator className="my-1 h-px bg-line" />;
}

export function MenuGroup({ label, children }: { label: string; children: ReactNode }) {
  return (
    <Base.Group>
      <Base.GroupLabel className="px-2 pb-0.5 pt-1.5 text-2xs font-semibold uppercase tracking-[0.06em] text-fg-subtle">
        {label}
      </Base.GroupLabel>
      {children}
    </Base.Group>
  );
}

export function MenuRadioGroup<T extends string>({
  value,
  onValueChange,
  options,
}: {
  value: T;
  onValueChange: (value: T) => void;
  options: { value: T; label: ReactNode }[];
}) {
  return (
    <Base.RadioGroup value={value} onValueChange={(next) => onValueChange(next as T)}>
      {options.map((option) => (
        <Base.RadioItem key={option.value} value={option.value} className={ITEM}>
          <span className="inline-flex w-3.5 justify-center">
            <Base.RadioItemIndicator>
              <Check size={14} aria-hidden="true" />
            </Base.RadioItemIndicator>
          </span>
          {option.label}
        </Base.RadioItem>
      ))}
    </Base.RadioGroup>
  );
}
