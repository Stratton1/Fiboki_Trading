"use client";

import { Tabs as Base } from "@base-ui/react/tabs";
import type { ComponentProps } from "react";
import { cn } from "./cn";

/** In-page tabs (ARIA tablist). Section navigation between routes uses links, not these. */
export const TabsRoot = Base.Root;

export function TabsList({ className, children, ...rest }: ComponentProps<typeof Base.List>) {
  return (
    <Base.List
      className={cn("relative mb-3 flex gap-1 border-b border-line", className as string)}
      {...rest}
    >
      {children}
      <Base.Indicator className="absolute bottom-[-1px] left-[var(--active-tab-left)] h-0.5 w-[var(--active-tab-width)] bg-accent transition-[left,width] duration-[var(--dur-md)] ease-standard" />
    </Base.List>
  );
}

export function Tab({ className, ...rest }: ComponentProps<typeof Base.Tab>) {
  return (
    <Base.Tab
      className={cn(
        "h-8 rounded-t-sm px-3 text-sm text-fg-muted hover:text-fg data-[active]:text-fg",
        className as string,
      )}
      {...rest}
    />
  );
}

export function TabsPanel({ className, ...rest }: ComponentProps<typeof Base.Panel>) {
  return <Base.Panel className={cn("outline-none", className as string)} {...rest} />;
}
