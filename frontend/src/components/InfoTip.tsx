"use client";

import { useState, useRef, useEffect, useLayoutEffect } from "react";
import { createPortal } from "react-dom";
import { Info } from "lucide-react";

/**
 * Inline info tooltip. Renders a small (i) icon that shows help text on hover/click.
 *
 * The tooltip is rendered into a body-level portal so it escapes any parent
 * `text-transform: uppercase` / overflow rules from its host (e.g. KPI labels),
 * and it auto-flips between above/below the icon based on viewport room — so
 * tooltips on the top row of the page no longer collide with the page heading.
 *
 * Usage: <InfoTip text="Explanation here" />
 */
export function InfoTip({ text, className }: { text: string; className?: string }) {
  const [open, setOpen] = useState(false);
  const [mounted, setMounted] = useState(false);
  const [coords, setCoords] = useState<{ top: number; left: number; placement: "top" | "bottom" }>(
    { top: 0, left: 0, placement: "top" }
  );
  const triggerRef = useRef<HTMLButtonElement>(null);
  const tipRef = useRef<HTMLSpanElement>(null);

  useEffect(() => {
    setMounted(true);
  }, []);

  // Position the floating tooltip relative to the trigger after it mounts so
  // we can measure its real height/width.
  useLayoutEffect(() => {
    if (!open || !triggerRef.current) return;
    const rect = triggerRef.current.getBoundingClientRect();
    const tipHeight = tipRef.current?.offsetHeight ?? 56;
    const tipWidth = tipRef.current?.offsetWidth ?? 224;
    const margin = 8;
    const spaceBelow = window.innerHeight - rect.bottom;
    // Prefer "bottom" placement so the tooltip doesn't collide with the page
    // heading on top-row cards. Only flip to "top" when there isn't room below.
    const placement: "top" | "bottom" =
      spaceBelow >= tipHeight + margin ? "bottom" : "top";

    let left = rect.left + rect.width / 2 - tipWidth / 2;
    left = Math.max(8, Math.min(left, window.innerWidth - tipWidth - 8));
    const top = placement === "top" ? rect.top - tipHeight - margin : rect.bottom + margin;
    setCoords({ top, left, placement });
  }, [open]);

  useEffect(() => {
    if (!open) return;
    function handleClickOutside(e: MouseEvent) {
      const target = e.target as Node;
      if (
        triggerRef.current &&
        !triggerRef.current.contains(target) &&
        tipRef.current &&
        !tipRef.current.contains(target)
      ) {
        setOpen(false);
      }
    }
    function handleEscape(e: KeyboardEvent) {
      if (e.key === "Escape") setOpen(false);
    }
    function handleScroll() {
      setOpen(false);
    }
    document.addEventListener("mousedown", handleClickOutside);
    document.addEventListener("keydown", handleEscape);
    window.addEventListener("scroll", handleScroll, true);
    return () => {
      document.removeEventListener("mousedown", handleClickOutside);
      document.removeEventListener("keydown", handleEscape);
      window.removeEventListener("scroll", handleScroll, true);
    };
  }, [open]);

  return (
    <span className={`relative inline-flex items-center ${className ?? ""}`}>
      <button
        ref={triggerRef}
        type="button"
        onClick={() => setOpen((v) => !v)}
        onMouseEnter={() => setOpen(true)}
        onMouseLeave={() => setOpen(false)}
        onFocus={() => setOpen(true)}
        onBlur={() => setOpen(false)}
        className="text-foreground-muted hover:text-foreground transition-colors ml-1"
        aria-label="More info"
        aria-expanded={open}
      >
        <Info size={13} />
      </button>
      {mounted && open
        ? createPortal(
            <span
              ref={tipRef}
              role="tooltip"
              style={{
                position: "fixed",
                top: coords.top,
                left: coords.left,
                zIndex: 9999,
              }}
              className="w-56 px-3 py-2 text-xs font-normal normal-case tracking-normal text-foreground bg-background-card border border-gray-200 rounded-lg shadow-lg leading-relaxed pointer-events-none"
            >
              {text}
            </span>,
            document.body
          )
        : null}
    </span>
  );
}
