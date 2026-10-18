"use client";

import Link from "next/link";
import { lazy, Suspense, useEffect, useState } from "react";

/**
 * The terminal's top: ONE line. The brand, the command line, and the market
 * session clocks.
 *
 *   FIBOKI ▸ [ RSK, EURUSD H4, FUNC…  <GO> ]   ● SYD 18:02 ○ TOK … ● LDN 09:02
 *
 * The function keys used to be a second row repeating the rail; they now live
 * in the rail (each section icon carries its key) and on the keyboard (F1 to
 * F9, F5 left to the browser). The command line only NAVIGATES or OPENS a
 * dialog, exactly as the palette and the `g` chords do (plan §4).
 */

const Engine = lazy(() => import("./CommandLine"));

/**
 * The command line and the session clocks. Their engine loads straight after
 * hydration to keep the shell's first load small; until then boxes of the
 * same size hold the place, so nothing moves when it arrives.
 */
function CommandLineSlot() {
  const [ready, setReady] = useState(false);
  useEffect(() => {
    let live = true;
    void import("./CommandLine").then(() => live && setReady(true));
    return () => {
      live = false;
    };
  }, []);
  return ready ? (
    <Suspense fallback={null}>
      <Engine />
    </Suspense>
  ) : (
    <>
      <div className="cmdline" data-testid="command-line" data-ready="false" />
      <div className="sessions sessions--pending" aria-hidden="true" />
    </>
  );
}

export function TopBar() {
  return (
    <header className="topbar" data-testid="topbar">
      <div className="topbar__line">
        <Link href="/" className="topbar__brand" tabIndex={-1}>
          FIBOKI
        </Link>
        <CommandLineSlot />
      </div>
    </header>
  );
}
