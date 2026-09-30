"use client";

import { Group, Panel, Separator, useDefaultLayout } from "react-resizable-panels";
import type { ReactNode } from "react";
import { cn } from "./cn";

const storage = {
  getItem(key: string) {
    try {
      return window.localStorage.getItem(key);
    } catch {
      return null;
    }
  },
  setItem(key: string, value: string) {
    try {
      window.localStorage.setItem(key, value);
    } catch {
      // Unavailable storage: the split resets on the next load, nothing else.
    }
  },
};

/**
 * Two resizable panes with a keyboard-operable separator (arrow keys, Home,
 * End). The layout is remembered per browser under `id`. Fixed layouts with
 * splits, not free docking (plan D-F8).
 */
export function SplitPane({
  id,
  orientation = "horizontal",
  first,
  second,
  firstLabel,
  secondLabel,
  defaultFirst = 60,
  minFirst = 20,
  minSecond = 20,
  className,
  padded = true,
}: {
  id: string;
  orientation?: "horizontal" | "vertical";
  first: ReactNode;
  second: ReactNode;
  firstLabel: string;
  secondLabel: string;
  defaultFirst?: number;
  minFirst?: number;
  minSecond?: number;
  /** Extra classes on the group (e.g. a height when it must fill its box). */
  className?: string;
  /** Pad each pane (default); a chart that draws edge to edge sets false. */
  padded?: boolean;
}) {
  const { defaultLayout, onLayoutChanged } = useDefaultLayout({ id, storage });
  const vertical = orientation === "vertical";
  return (
    <Group
      id={id}
      orientation={orientation}
      defaultLayout={defaultLayout}
      onLayoutChanged={onLayoutChanged}
      className={cn("min-h-40 rounded-md border border-line-subtle", className)}
    >
      <Panel
        id={`${id}-a`}
        defaultSize={`${defaultFirst}`}
        minSize={`${minFirst}`}
        aria-label={firstLabel}
      >
        <div className={cn("h-full overflow-auto", padded && "p-3")}>{first}</div>
      </Panel>
      <Separator
        aria-label={`Resize ${firstLabel} and ${secondLabel}`}
        className={
          vertical
            ? "h-1.5 bg-line-subtle outline-none hover:bg-line-strong focus-visible:bg-focus data-[separator=active]:bg-focus"
            : "w-1.5 bg-line-subtle outline-none hover:bg-line-strong focus-visible:bg-focus data-[separator=active]:bg-focus"
        }
      />
      <Panel id={`${id}-b`} minSize={`${minSecond}`} aria-label={secondLabel}>
        <div className={cn("h-full overflow-auto", padded && "p-3")}>{second}</div>
      </Panel>
    </Group>
  );
}
