"use client";

import { useExecutionMode, type ModeKey } from "./platform";

/**
 * Execution mode changes the whole shell, not only the banner: a viewport
 * frame, the favicon and the tab title. Report E §4.4 / plan §3.
 *
 * | mode     | frame                   | title prefix    | favicon           |
 * | backtest | none                    | [BT]            | grey dot          |
 * | paper    | 1px cyan                | [PAPER]         | cyan dot          |
 * | shadow   | 2px dashed violet       | [SHADOW]        | violet ring       |
 * | demo     | 3px amber               | [DEMO]          | amber dot         |
 * | live     | 4px magenta             | ● LIVE          | magenta square    |
 * | unknown  | 2px grey striped        | [MODE UNKNOWN]  | grey ?            |
 */

export const TITLE_PREFIX: Record<ModeKey, string> = {
  loading: "",
  unknown: "[MODE UNKNOWN]",
  backtest: "[BT]",
  paper: "[PAPER]",
  shadow: "[SHADOW]",
  demo: "[DEMO]",
  live: "● LIVE",
};

// Hex approximations of the dark-theme mode tokens (a favicon cannot read CSS).
const COLOUR: Record<ModeKey, string> = {
  loading: "#9099a5",
  unknown: "#9099a5",
  backtest: "#8c9aab",
  paper: "#51cade",
  shadow: "#b995f6",
  demo: "#fea92f",
  live: "#ff65ab",
};

function faviconSvg(mode: ModeKey): string {
  const c = COLOUR[mode];
  switch (mode) {
    case "live":
      return `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 32 32"><rect x="3" y="3" width="26" height="26" rx="5" fill="${c}"/><circle cx="16" cy="16" r="5" fill="#fff"/></svg>`;
    case "shadow":
      return `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 32 32"><circle cx="16" cy="16" r="11" fill="none" stroke="${c}" stroke-width="6"/></svg>`;
    case "unknown":
    case "loading":
      return `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 32 32"><circle cx="16" cy="16" r="14" fill="${c}"/><text x="16" y="23" text-anchor="middle" font-family="sans-serif" font-weight="700" font-size="20" fill="#111">?</text></svg>`;
    default:
      return `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 32 32"><circle cx="16" cy="16" r="13" fill="${c}"/></svg>`;
  }
}

export function faviconHref(mode: ModeKey): string {
  return `data:image/svg+xml,${encodeURIComponent(faviconSvg(mode))}`;
}

export function titleFor(mode: ModeKey): string {
  const prefix = TITLE_PREFIX[mode];
  return prefix ? `${prefix} Fiboki` : "Fiboki";
}

/** Tab title and favicon, hoisted into <head> by React. */
export function ModeHead() {
  const { mode } = useExecutionMode();
  return (
    <>
      <title>{titleFor(mode)}</title>
      <link rel="icon" href={faviconHref(mode)} data-mode={mode} />
    </>
  );
}

/** The viewport frame. Decorative: the banner and status bar carry the words. */
export function ModeFrame() {
  const { mode } = useExecutionMode();
  return (
    <div className="mode-frame" data-testid="mode-frame" data-mode={mode} aria-hidden="true" />
  );
}
