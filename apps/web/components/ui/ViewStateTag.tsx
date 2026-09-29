/**
 * The eight view states (report G §2.5), each a token (`--state-*` in
 * globals.css), a glyph, a word and a border style, so none relies on colour:
 *
 *   loading       first read in flight (never after data has arrived)
 *   empty         the platform answered with nothing: a real, empty result
 *   absent        the source is not wired; the platform says so ("not wired")
 *   stale         last-known-good data, older than it should be
 *   disconnected  the stream gave up and REST is failing; numbers kept, struck
 *   error         never received; nothing on the panel is current
 *   forming       a bar or aggregate still accumulating; carries no signal
 *   replay        a past as-of, never "now"
 */
export const VIEW_STATES = [
  "loading",
  "empty",
  "absent",
  "stale",
  "disconnected",
  "error",
  "forming",
  "replay",
] as const;

export type ViewStateName = (typeof VIEW_STATES)[number];

export const VIEW_STATE_META: Record<ViewStateName, { glyph: string; label: string; meaning: string }> = {
  loading: {
    glyph: "…",
    label: "LOADING",
    meaning: "The first read is in flight. A view never returns to this after it has shown data.",
  },
  empty: {
    glyph: "∅",
    label: "EMPTY",
    meaning: "The platform answered with no rows. A real, empty result, not a failure.",
  },
  absent: {
    glyph: "⊘",
    label: "NOT WIRED",
    meaning: "The platform says this source is not connected yet. Nothing is hidden and nothing is zero.",
  },
  stale: {
    glyph: "◷",
    label: "STALE",
    meaning: "Last-known-good numbers, older than they should be. They stay on screen, marked.",
  },
  disconnected: {
    glyph: "⌁",
    label: "DISCONNECTED",
    meaning: "The stream gave up and REST is failing. Numbers are kept, their age struck through; nothing can be confirmed.",
  },
  error: {
    glyph: "✕",
    label: "FAILED",
    meaning: "No data was ever received. Nothing on the panel is current; the error and correlation id are shown.",
  },
  forming: {
    glyph: "◌",
    label: "FORMING",
    meaning: "Still accumulating (the current bar). It is drawn faded and never carries a signal.",
  },
  replay: {
    glyph: "⟲",
    label: "REPLAY",
    meaning: "Showing the platform as it was at a past as-of. Never mistaken for now.",
  },
};

export function ViewStateTag({ state, children }: { state: ViewStateName; children?: string }) {
  const meta = VIEW_STATE_META[state];
  return (
    <span className="view-state" data-state={state} data-testid={`view-state-${state}`}>
      <span aria-hidden="true">{meta.glyph}</span>
      {children ?? meta.label}
    </span>
  );
}
