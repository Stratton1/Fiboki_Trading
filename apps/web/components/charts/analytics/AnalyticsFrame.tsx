"use client";

import { useState, type ReactNode } from "react";
import "./analytics.css";

/**
 * The frame every analytics chart sits in: a title row (title, unit, an
 * optional definition popover and actions), the plot, a legend, and a
 * keyboard-reachable "View data" table built only when opened, so no value is
 * only in a hover.
 */
export function AnalyticsFrame({
  title,
  unit,
  meta,
  legend,
  data,
  children,
  testId,
  note,
}: {
  title: string;
  unit?: string;
  /** Right-aligned extras in the title row (definition popover, chips). */
  meta?: ReactNode;
  legend?: ReactNode;
  data?: () => ReactNode;
  children: ReactNode;
  testId?: string;
  note?: ReactNode;
}) {
  return (
    <figure className="ax" data-testid={testId}>
      <figcaption className="ax__head">
        <span className="ax__title">{title}</span>
        {unit ? <span className="ax__unit">{unit}</span> : null}
        {meta ? <span className="ax__meta">{meta}</span> : null}
      </figcaption>
      {children}
      {legend ? <div className="ax__legend">{legend}</div> : null}
      {note ? <div className="ax__note">{note}</div> : null}
      {data ? <ViewData render={data} /> : null}
    </figure>
  );
}

function ViewData({ render }: { render: () => ReactNode }) {
  const [open, setOpen] = useState(false);
  return (
    <details className="ax__data" onToggle={(e) => setOpen(e.currentTarget.open)}>
      <summary>View data</summary>
      {open ? <div className="ax__data-table">{render()}</div> : null}
    </details>
  );
}

export function LegendSwatch({ ink, label, dashed = false }: { ink: string; label: ReactNode; dashed?: boolean }) {
  return (
    <span className="ax__key" data-ink={ink}>
      <svg width="18" height="8" aria-hidden="true">
        <line x1="0" y1="4" x2="18" y2="4" className="ax__key-line" data-dashed={dashed || undefined} />
      </svg>
      {label}
    </span>
  );
}

export function EmptyPlot({ message, height = 160 }: { message: string; height?: number }) {
  return (
    <div className="ax__empty" data-testid="ax-empty" data-height={height}>
      {message}
    </div>
  );
}
