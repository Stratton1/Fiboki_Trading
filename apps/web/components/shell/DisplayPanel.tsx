"use client";

import {
  uiPrefs,
  useUiPrefs,
  type DensityPref,
  type PnlPalette,
  type ThemePref,
} from "@/lib/ui-prefs";
import { Shortcut } from "../ui/Kbd";
import { Segmented } from "../ui/Segmented";

/** Theme, density and P&L colour controls, inside the display popover. */
export default function DisplayPanel() {
  const prefs = useUiPrefs();
  return (
    <div className="flex flex-col gap-3">
      <Segmented<ThemePref>
        label="Theme"
        testId="pref-theme"
        value={prefs.themePref}
        onValueChange={(v) => uiPrefs.setTheme(v)}
        options={[
          { value: "system", label: "System" },
          { value: "dark", label: "Dark" },
          { value: "light", label: "Light" },
        ]}
      />
      <Segmented<DensityPref>
        label="Density"
        testId="pref-density"
        value={prefs.densityPref}
        onValueChange={(v) => uiPrefs.setDensity(v)}
        options={[
          { value: "auto", label: "Auto" },
          { value: "compact", label: "Compact" },
          { value: "regular", label: "Regular" },
          { value: "comfortable", label: "Comfortable" },
        ]}
      />
      <Segmented<PnlPalette>
        label="Profit and loss colours"
        testId="pref-pnl"
        value={prefs.pnl}
        onValueChange={(v) => uiPrefs.setPnl(v)}
        options={[
          { value: "standard", label: "Green / red" },
          { value: "cvd", label: "Blue / orange" },
        ]}
      />
      <div className="flex flex-col gap-1 text-xs text-fg-muted">
        <span className="flex items-center justify-between gap-3">
          Toggle theme <Shortcut keys={["⌘", "⇧", "L"]} label="Command Shift L" />
        </span>
        <span className="flex items-center justify-between gap-3">
          Cycle density <Shortcut keys={["⌘", "⇧", "D"]} label="Command Shift D" />
        </span>
        <span>Auto density is comfortable below 1024 px wide, regular above.</span>
      </div>
    </div>
  );
}
