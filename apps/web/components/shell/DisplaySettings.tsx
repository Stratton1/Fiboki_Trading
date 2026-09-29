"use client";

import { SlidersHorizontal } from "lucide-react";
import { lazy, Suspense } from "react";
import { useUiPrefs } from "@/lib/ui-prefs";
import { Popover } from "../ui/Popover";

// The controls (Base UI radio groups) load with the popover, not the shell.
const DisplayPanel = lazy(() => import("./DisplayPanel"));

/** Theme, density and P&L colours. Display only: none of it reaches the platform. */
export function DisplaySettings() {
  const prefs = useUiPrefs();
  return (
    <Popover
      title="Display"
      side="top"
      align="end"
      testId="display-settings"
      trigger={
        <button
          type="button"
          className="status-bar__item"
          data-testid="display-settings-trigger"
          aria-label="Display settings"
        >
          <SlidersHorizontal size={12} aria-hidden="true" />
          {prefs.density} · {prefs.theme}
        </button>
      }
    >
      <Suspense fallback={<p className="muted">Loading display settings.</p>}>
        <DisplayPanel />
      </Suspense>
    </Popover>
  );
}
