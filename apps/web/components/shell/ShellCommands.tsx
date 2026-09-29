"use client";

import { lazy, Suspense, useEffect, useState } from "react";
import { onCommand } from "./commands";

/**
 * Mounts what the command bus asks for, loading each piece on first use so
 * none of it is in the shell's first load (plan D-F7 budgets): the cmdk
 * palette, the shortcut sheet and the kill-switch dialog's flow. Once loaded,
 * each stays mounted so its close animation and focus return run.
 */
const CommandPalette = lazy(() => import("./CommandPalette"));
const ShortcutSheet = lazy(() => import("./ShortcutSheet"));
const GlobalKillSwitch = lazy(() => import("../GlobalKillSwitch"));

export function ShellCommands() {
  const [palette, setPalette] = useState<boolean | null>(null);
  const [sheet, setSheet] = useState<boolean | null>(null);
  // A request counter: every ⇧K re-opens the dialog, even if already mounted.
  const [killSwitch, setKillSwitch] = useState(0);

  useEffect(
    () =>
      onCommand((command) => {
        if (command === "palette") setPalette(true);
        else if (command === "shortcuts") setSheet(true);
        else if (command === "kill-switch") {
          setPalette((open) => (open ? false : open));
          setKillSwitch((n) => n + 1);
        }
      }),
    [],
  );

  return (
    <Suspense fallback={null}>
      {palette !== null ? <CommandPalette open={palette} onOpenChange={setPalette} /> : null}
      {sheet !== null ? <ShortcutSheet open={sheet} onOpenChange={setSheet} /> : null}
      {killSwitch > 0 ? <GlobalKillSwitch request={killSwitch} /> : null}
    </Suspense>
  );
}
