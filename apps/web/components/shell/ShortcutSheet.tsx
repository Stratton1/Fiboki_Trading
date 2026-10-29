"use client";

import { Dialog as Base } from "@base-ui/react/dialog";
import { Button } from "../ui/Button";
import { DialogContent, DialogDescription, DialogTitle } from "../ui/Dialog";
import Link from "next/link";
import { Kbd } from "../ui/Kbd";
import { SECTIONS } from "./sections";

/** The documented shortcuts, for the ? sheet and the palette hints. */
export const SHORTCUTS: readonly { keys: string[]; label: string; what: string }[] = [
  { keys: ["⌘", "K"], label: "Command K", what: "Open the command palette (Ctrl+K on Windows and Linux)" },
  { keys: ["?"], label: "Question mark", what: "Show these shortcuts" },
  { keys: ["GUIDE"], label: "GUIDE", what: "Open the operator walkthrough. The status bar’s Guide button does the same." },
  { keys: ["g", "…"], label: "g then a letter", what: "Go to a screen (see the list below)" },
  {
    keys: ["/"],
    label: "Slash",
    what: "Focus the command line: a code (RSK, FLT, LAB), a symbol (EURUSD), or a symbol and a function (EURUSD H4, EURUSD BT), then Enter. ↑ recalls the last 50 commands. FUNC lists every function, KILL opens the kill-switch dialog, an empty Enter opens the palette.",
  },
  {
    keys: ["F1", "…", "F9"],
    label: "F1 to F9",
    what: "Go to a section: F1 Command, F2 Fleet, F3 Lifecycle, F4 Lab, F6 Risk, F7 Data, F8 System, F9 Journal. F5 stays the browser's reload. The rail shows each section's key.",
  },
  {
    keys: ["⇧", "K"],
    label: "Shift K",
    what: "Open the kill-switch dialog. It never arms by itself: you still choose PAUSE or FLATTEN and give a reason.",
  },
  { keys: ["j", "k"], label: "j or k", what: "Next or previous row in a focused grid (also ↓ ↑)" },
  { keys: ["Home", "End"], label: "Home or End", what: "First or last row in a focused grid" },
  { keys: ["Enter"], label: "Enter", what: "Open the focused row, where the grid supports it" },
  { keys: ["⌘", "⇧", "D"], label: "Command Shift D", what: "Cycle row density" },
  { keys: ["⌘", "⇧", "L"], label: "Command Shift L", what: "Toggle light and dark" },
];

/**
 * The chart workstation's drawing shortcuts (components/charts/ChartWorkbench.tsx).
 * They edit the operator's own annotations only, never the platform, and every
 * edit is undoable. Alt with a letter is matched by key position, so they work
 * on any keyboard layout.
 */
export const CHART_SHORTCUTS: readonly { keys: string[]; label: string; what: string }[] = [
  { keys: ["Alt", "T"], label: "Alt T", what: "Chart: trend line" },
  { keys: ["Alt", "R"], label: "Alt R", what: "Chart: ray" },
  { keys: ["Alt", "H"], label: "Alt H", what: "Chart: horizontal level" },
  { keys: ["Alt", "V"], label: "Alt V", what: "Chart: vertical line" },
  { keys: ["Alt", "B"], label: "Alt B", what: "Chart: rectangle zone" },
  { keys: ["Alt", "F"], label: "Alt F", what: "Chart: Fibonacci retracement (a drawing, never a strategy level)" },
  { keys: ["Alt", "N"], label: "Alt N", what: "Chart: text note" },
  { keys: ["Alt", "M"], label: "Alt M", what: "Chart: measure price, time and percent (display only, not saved)" },
  { keys: ["Alt", "S"], label: "Alt S", what: "Chart: select and move (Escape also returns to select)" },
  { keys: ["Del"], label: "Delete", what: "Chart: delete the selected drawing" },
  { keys: ["⌘", "Z"], label: "Command Z", what: "Chart: undo a drawing edit (⌘⇧Z or Ctrl+Y redoes)" },
  { keys: ["Alt", "⇧", "L"], label: "Alt Shift L", what: "Chart: lock the selected drawing, or all drawings" },
  { keys: ["Alt", "⇧", "H"], label: "Alt Shift H", what: "Chart: hide or show all drawings" },
];

/** The ? sheet: every shortcut, and every `g` chord. Loaded on first `?`. */
export default function ShortcutSheet({
  open,
  onOpenChange,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
}) {
  return (
    <Base.Root open={open} onOpenChange={(next) => onOpenChange(next)}>
      <DialogContent testId="shortcut-sheet" size="md">
        <DialogTitle>Keyboard shortcuts</DialogTitle>
        <DialogDescription>
          Shortcuts navigate, change the display or open a dialog. None of them changes anything on
          the platform; the chart&apos;s drawing shortcuts edit only your own annotations, and undo
          restores them.
        </DialogDescription>
        <div className="shortcut-scroll" tabIndex={0} role="region" aria-label="Shortcut list, scrolls">
        <table className="shortcut-table">
          <caption className="sr-only">Shortcuts</caption>
          <thead>
            <tr>
              <th scope="col">Keys</th>
              <th scope="col">What it does</th>
            </tr>
          </thead>
          <tbody>
            {SHORTCUTS.map((s) => (
              <tr key={s.label}>
                <td>
                  <span className="inline-flex gap-0.5" aria-label={s.label}>
                    {s.keys.map((k) => (
                      <Kbd key={k}>{k}</Kbd>
                    ))}
                  </span>
                </td>
                <td className="wrap">{s.what}</td>
              </tr>
            ))}
            {CHART_SHORTCUTS.map((s) => (
              <tr key={s.label} data-testid="shortcut-chart">
                <td>
                  <span className="inline-flex gap-0.5" aria-label={s.label}>
                    {s.keys.map((k) => (
                      <Kbd key={k}>{k}</Kbd>
                    ))}
                  </span>
                </td>
                <td className="wrap">{s.what}</td>
              </tr>
            ))}
            {SECTIONS.map((section) => (
              <tr key={section.id} data-testid={`shortcut-chord-${section.chord}`}>
                <td>
                  <span className="inline-flex gap-0.5" aria-label={`g then ${section.chord}`}>
                    <Kbd>g</Kbd>
                    <Kbd>{section.chord}</Kbd>
                  </span>
                </td>
                <td className="wrap">Go to {section.title}</td>
              </tr>
            ))}
          </tbody>
        </table>
        </div>
        <div className="dialog__actions">
          <Link href="/system/functions" className="mr-auto" onClick={() => onOpenChange(false)}>
            Every function (FUNC)
          </Link>
          <Button onClick={() => onOpenChange(false)} data-testid="shortcut-sheet-close">
            Close
          </Button>
        </div>
      </DialogContent>
    </Base.Root>
  );
}
