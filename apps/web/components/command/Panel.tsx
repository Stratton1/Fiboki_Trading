"use client";

import { Maximize2, Minimize2 } from "lucide-react";
import { useEffect, useState, type ReactNode } from "react";

/**
 * A Command panel that can be maximised over the page (programme feature 5):
 * the button or a double-click on the title maximises it, the button or Esc
 * restores it. Display only; the panel's contents are the same component.
 */
export function Panel({
  id,
  title,
  className,
  actions,
  children,
}: {
  id: string;
  title: string;
  className: string;
  actions?: ReactNode;
  children: ReactNode;
}) {
  const [max, setMax] = useState(false);
  useEffect(() => {
    if (!max) return;
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape" && !event.defaultPrevented) setMax(false);
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [max]);
  const Icon = max ? Minimize2 : Maximize2;
  return (
    <section
      className={`card cmd-panel ${className}`}
      aria-labelledby={id}
      data-maximised={max || undefined}
      data-testid={`panel-${id}`}
    >
      <h2 id={id} className="card__title cmd-panel__title" onDoubleClick={() => setMax((m) => !m)}>
        <span>{title}</span>
        {actions}
        <button
          type="button"
          className="cmd-panel__max"
          aria-pressed={max}
          aria-label={max ? `Restore ${title}` : `Maximise ${title}`}
          title={max ? "Restore (Esc)" : "Maximise (double-click the title)"}
          data-testid={`panel-max-${id}`}
          onClick={() => setMax((m) => !m)}
        >
          <Icon size={12} aria-hidden="true" />
        </button>
      </h2>
      <div className="cmd-panel__body">{children}</div>
    </section>
  );
}
