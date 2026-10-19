"use client";

import { useEffect, useId, useRef, useState } from "react";
import { settingString, type PanelProps } from "../types";

const MAX = 4000;

/**
 * NOTE: free text kept with the layout, server-side and versioned with it.
 * An operator note, never strategy input. Typing is committed to the layout
 * after a pause (the layout then autosaves on its own debounce).
 */
export default function NotesPanel({ panel, onSettings, readOnly }: PanelProps) {
  const saved = settingString(panel, "text", "");
  const [text, setText] = useState(saved);
  const [lastSaved, setLastSaved] = useState(saved);
  const timer = useRef<number | null>(null);
  const id = useId();
  // A newer text from the server (another tab, a reload) replaces the draft
  // only when the operator has not typed since.
  if (saved !== lastSaved) {
    setLastSaved(saved);
    if (text === lastSaved) setText(saved);
  }
  useEffect(() => () => {
    if (timer.current !== null) window.clearTimeout(timer.current);
  }, []);
  return (
    <div className="ws-notes">
      <label htmlFor={id} className="sr-only">
        Notes
      </label>
      <textarea
        id={id}
        className="ws-notes__text"
        data-testid="ws-notes"
        value={text}
        maxLength={MAX}
        readOnly={readOnly}
        placeholder={readOnly ? "Read-only." : "Notes kept with this layout (not strategy input)."}
        onChange={(event) => {
          const next = event.target.value;
          setText(next);
          if (timer.current !== null) window.clearTimeout(timer.current);
          timer.current = window.setTimeout(() => onSettings({ text: next }), 600);
        }}
      />
      <p className="ws-stamp" data-testid="panel-stamp">
        <span className="ws-stamp__kind">LAYOUT</span>
        <span>
          {text.length}/{MAX} · saved with the layout
        </span>
      </p>
    </div>
  );
}
