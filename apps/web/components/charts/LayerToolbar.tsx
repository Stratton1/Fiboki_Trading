"use client";

import { Eye, EyeOff, Layers } from "lucide-react";
import { useEffect } from "react";
import { Popover } from "@/components/ui/Popover";
import { ALL_LAYERS_OFF, ALL_LAYERS_ON, LAYER_LABEL, LAYER_NAMES, type ChartLayers, type LayerName } from "./layers";

/** A layer's item count as drawn: a number, still reading, or not available from the API. */
export type LayerCount = number | "pending" | "n/a";

/**
 * The layer toolbar (programme feature 13): show or hide each layer the chart
 * draws (drawings, trades, stops and targets, calendar events, indicators,
 * signals, fills, regimes), and hide or show all at once. Display only:
 * hiding a layer never hides a figure from a table or changes a count the
 * API sent. Alt+1 … Alt+8 toggle the layers in order; Alt+0 hides or shows all.
 */
export function LayerToolbar({
  layers,
  onLayers,
  counts,
}: {
  layers: ChartLayers;
  onLayers: (update: (layers: ChartLayers) => ChartLayers) => void;
  /** Items drawn per layer, where the chart knows them (absent: not counted). */
  counts: Partial<Record<LayerName, LayerCount>>;
}) {
  const anyOn = LAYER_NAMES.some((name) => layers[name]);
  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if (!event.altKey || event.ctrlKey || event.metaKey || event.shiftKey) return;
      const target = event.target as HTMLElement | null;
      if (target && (target.tagName === "INPUT" || target.tagName === "TEXTAREA" || target.isContentEditable)) return;
      const digit = /^Digit([0-8])$/.exec(event.code)?.[1];
      if (digit === undefined) return;
      event.preventDefault();
      if (digit === "0") {
        onLayers((l) => (LAYER_NAMES.some((n) => l[n]) ? ALL_LAYERS_OFF : ALL_LAYERS_ON));
        return;
      }
      const name = LAYER_NAMES[Number(digit) - 1];
      if (name) onLayers((l) => ({ ...l, [name]: !l[name] }));
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onLayers]);
  const onCount = LAYER_NAMES.filter((name) => layers[name]).length;
  const toggles = (
    <div className="cw-layers cw-layers--list" role="group" aria-label="Chart layers" data-testid="layer-list">
      {LAYER_NAMES.map((name, i) => {
        const count = counts[name];
        return (
          <button
            key={name}
            type="button"
            className="cw-layers__btn"
            aria-pressed={layers[name]}
            data-testid={`layer-${name}`}
            data-count={typeof count === "number" ? count : ""}
            data-available={count === undefined || count === "pending" ? "" : String(count !== "n/a")}
            title={`${layers[name] ? "Hide" : "Show"} ${LAYER_LABEL[name].toLowerCase()} · Alt ${i + 1}`}
            onClick={() => onLayers((l) => ({ ...l, [name]: !l[name] }))}
          >
            {LAYER_LABEL[name]}
            {count === undefined ? null : (
              <span className="cw-layers__n">
                {count === "pending" ? "…" : count === "n/a" ? "n/a" : count.toLocaleString("en-GB")}
              </span>
            )}
          </button>
        );
      })}
    </div>
  );
  return (
    <span className="cw-layers" data-testid="layer-toolbar" data-on={onCount}>
      <Popover
        title="Chart layers"
        testId="layer-popover"
        trigger={
          <button type="button" className="cw-layers__btn cw-layers__btn--menu" data-testid="layers-menu" aria-pressed={onCount > 0}>
            <Layers size={12} aria-hidden="true" />
            Layers {onCount}/{LAYER_NAMES.length}
          </button>
        }
      >
        {toggles}
        <p className="muted cw-layers__hint">Alt 1 to Alt 8 toggle these in order; Alt 0 hides or shows all.</p>
      </Popover>
      <button
        type="button"
        className="cw-layers__btn cw-layers__btn--all"
        data-testid="layer-all"
        aria-pressed={!anyOn}
        title={`${anyOn ? "Hide" : "Show"} every layer · Alt 0`}
        onClick={() => onLayers(() => (anyOn ? ALL_LAYERS_OFF : ALL_LAYERS_ON))}
      >
        {anyOn ? <EyeOff size={12} aria-hidden="true" /> : <Eye size={12} aria-hidden="true" />}
        {anyOn ? "Hide all" : "Show all"}
      </button>
    </span>
  );
}
