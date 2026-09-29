"use client";

import { useRouter } from "next/navigation";
import { useEffect, useRef } from "react";
import { uiPrefs } from "@/lib/ui-prefs";
import { useToast } from "../ui/Toast";
import { openCommand } from "./commands";
import { CHORDS } from "./sections";

/**
 * Shell shortcuts. Navigation, display and dialogs only, never a mutation: no
 * single keystroke changes anything on the platform (plan §4).
 *
 *   ⌘K (Ctrl+K)          command palette
 *   ?                    shortcut sheet
 *   g then a letter      go to a screen (sections.ts CHORDS)
 *   ⇧K                   OPEN the kill-switch dialog (never arms)
 *   ⌘⇧D (Ctrl+Shift+D)   cycle density
 *   ⌘⇧L (Ctrl+Shift+L)   toggle light / dark
 *
 * Single-key shortcuts are ignored while typing in a field and while a dialog
 * is open, so a reason typed into the kill-switch dialog can never navigate.
 */

export const CHORD_TIMEOUT_MS = 1_500;

function typing(target: EventTarget | null): boolean {
  if (!(target instanceof HTMLElement)) return false;
  if (target.isContentEditable) return true;
  const tag = target.tagName;
  if (tag === "TEXTAREA" || tag === "SELECT") return true;
  if (tag === "INPUT") {
    const type = (target as HTMLInputElement).type;
    return !["checkbox", "radio", "button", "submit", "reset"].includes(type);
  }
  return false;
}

function inDialog(): boolean {
  // A dialog in its exit transition is closing, not open.
  return (
    document.querySelector(
      '[role="dialog"][data-open]:not([data-ending-style]), [role="alertdialog"][data-open]:not([data-ending-style])',
    ) !== null
  );
}

export function ShellHotkeys() {
  const notify = useToast();
  const router = useRouter();
  const chordAt = useRef<number | null>(null);

  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if (event.defaultPrevented || event.isComposing) return;
      const key = event.key.toLowerCase();
      const mod = event.metaKey || event.ctrlKey;

      // Display: ⌘⇧D, ⌘⇧L (work anywhere; they touch nothing on the platform).
      if (mod && event.shiftKey && !event.altKey) {
        if (key === "d") {
          event.preventDefault();
          notify(`Density: ${uiPrefs.cycleDensity()}`);
        } else if (key === "l") {
          event.preventDefault();
          notify(`Theme: ${uiPrefs.toggleTheme()}`);
        }
        return;
      }
      // ⌘K / Ctrl+K: the palette, even from a field (it is how you leave one).
      if (mod && !event.shiftKey && !event.altKey && key === "k") {
        if (inDialog()) return;
        event.preventDefault();
        openCommand("palette");
        return;
      }
      if (mod || event.altKey) return;
      if (typing(event.target) || inDialog()) {
        chordAt.current = null;
        return;
      }

      // A pending `g` chord.
      if (chordAt.current !== null) {
        const fresh = performance.now() - chordAt.current <= CHORD_TIMEOUT_MS;
        chordAt.current = null;
        const target = fresh && !event.shiftKey ? CHORDS[key] : undefined;
        if (target) {
          event.preventDefault();
          router.push(target.href);
          return;
        }
      }
      if (event.key === "?") {
        event.preventDefault();
        openCommand("shortcuts");
        return;
      }
      if (event.key === "K" && event.shiftKey) {
        event.preventDefault();
        openCommand("kill-switch");
        return;
      }
      if (key === "g" && !event.shiftKey) {
        chordAt.current = performance.now();
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [notify, router]);
  return null;
}
