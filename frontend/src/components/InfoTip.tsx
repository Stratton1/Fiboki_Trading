"use client";

import { useState, useRef, useEffect } from "react";
import { Info } from "lucide-react";

/**
 * Inline info tooltip. Renders a small (i) icon that shows help text on hover/click.
 * Usage: <InfoTip text="Explanation here" />
 */
export function InfoTip({ text, className }: { text: string; className?: string }) {
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLSpanElement>(null);

  useEffect(() => {
    if (!open) return;
    function handleClick(e: MouseEvent) {
      if (ref.current && !ref.current.contains(e.target as Node)) {
        setOpen(false);
      }
    }
    document.addEventListener("mousedown", handleClick);
    return () => document.removeEventListener("mousedown", handleClick);
  }, [open]);

  return (
    <span ref={ref} className={`relative inline-flex items-center ${className ?? ""}`}>
      <button
        type="button"
        onClick={() => setOpen(!open)}
        onMouseEnter={() => setOpen(true)}
        onMouseLeave={() => setOpen(false)}
        className="text-foreground-muted hover:text-foreground transition-colors ml-1"
        aria-label="More info"
      >
        <Info size={13} />
      </button>
      {open && (
        <span className="absolute z-50 bottom-full left-1/2 -translate-x-1/2 mb-1.5 w-56 px-3 py-2 text-xs text-foreground bg-background-card border border-gray-200 rounded-lg shadow-lg leading-relaxed pointer-events-none">
          {text}
        </span>
      )}
    </span>
  );
}
