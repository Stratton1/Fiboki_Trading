"use client";

import { useEffect, useRef, useState } from "react";

/**
 * The rendered width of a box, kept current with a ResizeObserver, so an
 * owned SVG can draw at 1:1 (text never scales with a viewBox). Never
 * narrower than 240 px; `fallback` until the first measurement.
 */
export function useWidth(fallback = 720) {
  const ref = useRef<HTMLDivElement>(null);
  const [width, setWidth] = useState(fallback);
  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    const measure = () => {
      const w = Math.round(el.getBoundingClientRect().width);
      if (w > 0) setWidth(Math.max(240, w));
    };
    measure();
    const observer = new ResizeObserver(measure);
    observer.observe(el);
    return () => observer.disconnect();
  }, []);
  return [ref, width] as const;
}
