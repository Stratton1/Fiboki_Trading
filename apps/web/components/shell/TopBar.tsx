"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { lazy, Suspense, useEffect, useState } from "react";
import { SECTIONS, sectionFor } from "./sections";

/**
 * The terminal's top: a command line and a row of function keys.
 *
 *   FIBOKI ▸ [ RSK, EURUSD, "audit"…  <GO> ]
 *   F1 CMD │ F2 FLT │ F3 LIF │ F4 LAB │ F5 MKT │ F6 RSK │ F7 DQ │ F8 SYS │ F9 JRN
 *
 * Both only NAVIGATE or OPEN a dialog, exactly as the palette and the `g`
 * chords do; nothing here changes anything on the platform (plan §4).
 *
 * The command line takes a mnemonic (every view has one, CommandLine.tsx), a
 * word from a view's name, or an instrument symbol (opens that chart, as the
 * palette's "Open chart" does; the platform decides whether it exists). `?`
 * opens the shortcut sheet, `KILL` opens the kill-switch dialog (it never
 * arms), and an empty Enter opens the full palette. `/` focuses the line.
 * F1 to F9 (except F5, which stays the browser's reload) go to a section.
 */

const Engine = lazy(() => import("./CommandLine"));

/**
 * The command line. Its engine and the bar's keys load straight after
 * hydration to keep the shell's first load small; until then an empty box of
 * the same size holds the place, so nothing moves when it arrives.
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
    <div className="cmdline" data-testid="command-line" data-ready="false" />
  );
}

function FunctionKeys() {
  const pathname = usePathname();
  const active = sectionFor(pathname);
  // One tab stop for the whole row (the current section's key, else the
  // first); arrows, Home and End move between keys. Nine more tab stops
  // before the rail and the page would be nine more for every keyboard user.
  const tabStop = active ?? SECTIONS[0];

  return (
    <nav
      className="fkeys"
      aria-label="Section keys"
      data-testid="function-keys"
    >
      {SECTIONS.map((section, index) => {
        const first = section.links[0];
        if (!first) return null;
        return (
          <Link
            key={section.id}
            href={first.href}
            className="fkey"
            data-testid={`fkey-${section.id}`}
            tabIndex={section === tabStop ? 0 : -1}
            aria-current={section === active ? "true" : undefined}
          >
            <span className="fkey__k">F{index + 1}</span>
            <span className="fkey__l">{section.mnemonic}</span>
          </Link>
        );
      })}
    </nav>
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
      <FunctionKeys />
    </header>
  );
}
