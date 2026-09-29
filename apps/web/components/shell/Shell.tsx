"use client";

import { useEffect, type ReactNode } from "react";
import { useUiPrefs } from "@/lib/ui-prefs";
import { ModeBanner } from "../ModeBanner";
import { prefetchLayers } from "../ui/layers";
import { ToastProvider } from "../ui/Toast";
import { ShellHotkeys } from "./Hotkeys";
import { InspectorProvider } from "./Inspector";
import { ModeFrame, ModeHead } from "./Mode";
import { PageHeader, PageHeaderProvider } from "./PageHeader";
import { PlatformProvider } from "./platform";
import { Rail } from "./Rail";
import { StatusBar } from "./StatusBar";

/**
 * The workstation shell (report E §5.1):
 *
 *   mode banner (sticky, whole width)
 *   rail │ page header (section · view tabs · actions)
 *        │ page
 *   status bar
 *
 * plus the mode frame around the viewport, the tab title and favicon, the
 * inspector sheet and the toast region. The banner is rendered here ONCE,
 * outside the page, so no page can opt out of it or re-implement it; that is
 * how V1 ended up with 14 of 19 pages that never read the execution mode.
 */
export function Shell({ children }: { children: ReactNode }) {
  const { railExpanded } = useUiPrefs();
  usePrefetchLayersWhenIdle();
  return (
    <ToastProvider>
      <PlatformProvider>
        <InspectorProvider>
          <PageHeaderProvider>
            <ModeHead />
            <ModeFrame />
            <a className="skip-link" href="#main">
              Skip to content
            </a>
            <ModeBanner />
            <div className="shell" data-rail={railExpanded ? "expanded" : "collapsed"}>
              <Rail />
              <div className="shell__main">
                <PageHeader />
                <main id="main" className="content" tabIndex={-1}>
                  {children}
                </main>
              </div>
            </div>
            <StatusBar />
            <ShellHotkeys />
          </PageHeaderProvider>
        </InspectorProvider>
      </PlatformProvider>
    </ToastProvider>
  );
}

/**
 * Base UI popups are not part of the first load (components/ui/layers.ts).
 * Fetch them once the browser is idle so the first dialog or popover opens
 * without a network wait.
 */
function usePrefetchLayersWhenIdle() {
  useEffect(() => {
    const w = window as Window & {
      requestIdleCallback?: (cb: () => void, opts?: { timeout: number }) => number;
      cancelIdleCallback?: (id: number) => void;
    };
    if (w.requestIdleCallback) {
      const id = w.requestIdleCallback(prefetchLayers, { timeout: 4000 });
      return () => w.cancelIdleCallback?.(id);
    }
    const timer = setTimeout(prefetchLayers, 1500);
    return () => clearTimeout(timer);
  }, []);
}
