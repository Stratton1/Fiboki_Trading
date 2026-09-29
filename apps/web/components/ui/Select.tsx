"use client";

import { CSPProvider } from "@base-ui/react/csp-provider";
import { Select as Base } from "@base-ui/react/select";
import { Check, ChevronDown } from "lucide-react";

/**
 * A single-choice select on Base UI. Native <select> remains fine inside forms
 * and filters. `disableStyleElements`: Base UI would otherwise inject an
 * inline <style> (scrollbar hiding); that rule ships in globals.css instead,
 * so the CSP needs no inline-style allowance for <style> elements.
 */
export function Select<T extends string>({
  label,
  value,
  onValueChange,
  options,
  testId,
}: {
  label: string;
  value: T;
  onValueChange: (value: T) => void;
  options: { value: T; label: string }[];
  testId?: string;
}) {
  const items = Object.fromEntries(options.map((o) => [o.value, o.label]));
  return (
    <CSPProvider disableStyleElements>
      <Base.Root
        items={items}
        value={value}
        onValueChange={(next) => {
          if (typeof next === "string") onValueChange(next as T);
        }}
      >
        <div className="flex flex-col gap-1">
          <Base.Label className="m-0 text-2xs font-semibold uppercase tracking-[0.06em] text-fg-subtle">
            {label}
          </Base.Label>
          <Base.Trigger
            data-testid={testId}
            className="inline-flex h-[var(--control-h)] min-w-40 items-center justify-between gap-2 rounded-sm border border-line-control bg-sunken px-2.5 text-sm text-fg hover:bg-raised"
          >
            <Base.Value />
            <Base.Icon className="text-fg-muted">
              <ChevronDown size={14} aria-hidden="true" />
            </Base.Icon>
          </Base.Trigger>
        </div>
        <Base.Portal>
          <Base.Positioner sideOffset={4} alignItemWithTrigger={false} className="z-[250]">
            <Base.Popup className="min-w-[var(--anchor-width)] rounded-md border border-line-strong bg-overlay p-1 shadow-e3 outline-none">
              <Base.List>
                {options.map((option) => (
                  <Base.Item
                    key={option.value}
                    value={option.value}
                    className="flex cursor-default items-center gap-2 rounded-sm px-2 py-1 text-sm text-fg outline-none data-[highlighted]:bg-raised"
                  >
                    <Base.ItemIndicator className="w-3.5 text-accent" keepMounted={false}>
                      <Check size={14} aria-hidden="true" />
                    </Base.ItemIndicator>
                    <Base.ItemText>{option.label}</Base.ItemText>
                  </Base.Item>
                ))}
              </Base.List>
            </Base.Popup>
          </Base.Positioner>
        </Base.Portal>
      </Base.Root>
    </CSPProvider>
  );
}
