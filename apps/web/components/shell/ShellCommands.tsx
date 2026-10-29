"use client";

import { lazy, Suspense, useEffect, useRef, useState } from "react";
import { onCommand } from "./commands";

const GUIDE_SEEN_KEY = "fiboki.guide";
const GUIDE_STEP_KEY = "fiboki.guide.step";

function storedGuideStep(): number {
  try {
    const raw = window.sessionStorage.getItem(GUIDE_STEP_KEY);
    const step = raw === null ? 0 : Number(raw);
    return Number.isFinite(step) && step >= 0 ? step : 0;
  } catch {
    return 0;
  }
}

/**
 * Mounts what the command bus asks for, loading each piece on first use so
 * none of it is in the shell's first load (plan D-F7 budgets): the cmdk
 * palette, the shortcut sheet and the kill-switch dialog's flow. Once loaded,
 * each stays mounted so its close animation and focus return run.
 */
const CommandPalette = lazy(() => import("./CommandPalette"));
const ShortcutSheet = lazy(() => import("./ShortcutSheet"));
const Onboarding = lazy(() => import("./Onboarding"));
const GlobalKillSwitch = lazy(() => import("../GlobalKillSwitch"));

export function ShellCommands() {
  const [palette, setPalette] = useState<boolean | null>(null);
  const [sheet, setSheet] = useState<boolean | null>(null);
  const [guide, setGuide] = useState<boolean | null>(null);
  const [guideAt, setGuideAt] = useState(0);
  const resumeStep = useRef(false);
  // A request counter: every ⇧K re-opens the dialog, even if already mounted.
  const [killSwitch, setKillSwitch] = useState(0);

  const openGuide = () => {
    setGuideAt(storedGuideStep());
    setGuide(true);
  };

  useEffect(
    () =>
      onCommand((command) => {
        if (command === "palette") setPalette(true);
        else if (command === "shortcuts") setSheet(true);
        else if (command === "kill-switch") {
          setPalette((open) => (open ? false : open));
          setKillSwitch((n) => n + 1);
        } else if (command === "guide") {
          openGuide();
        }
      }),
    [],
  );

  useEffect(() => {
    try {
      if (window.localStorage.getItem(GUIDE_SEEN_KEY) !== "seen") openGuide();
    } catch {
      // No storage: the status-bar Guide button still opens it.
    }
  }, []);

  return (
    <Suspense fallback={null}>
      {palette !== null ? <CommandPalette open={palette} onOpenChange={setPalette} /> : null}
      {sheet !== null ? <ShortcutSheet open={sheet} onOpenChange={setSheet} /> : null}
      {guide !== null ? (
        <Onboarding
          open={guide}
          startAt={guideAt}
          onNavigate={(index) => {
            resumeStep.current = true;
            try {
              window.sessionStorage.setItem(GUIDE_STEP_KEY, String(index));
            } catch {
              // The page still opens. The next Guide starts at the first step.
            }
          }}
          onOpenChange={(next) => {
            if (!next) {
              if (!resumeStep.current) {
                try {
                  window.sessionStorage.removeItem(GUIDE_STEP_KEY);
                } catch {
                  // Closing still dismisses it for this view.
                }
              }
              resumeStep.current = false;
              try {
                window.localStorage.setItem(GUIDE_SEEN_KEY, "seen");
              } catch {
                // Closing still dismisses it for this view.
              }
            }
            setGuide(next);
          }}
        />
      ) : null}
      {killSwitch > 0 ? <GlobalKillSwitch request={killSwitch} /> : null}
    </Suspense>
  );
}
