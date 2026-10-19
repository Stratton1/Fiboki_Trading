"use client";

import { Eye, EyeOff } from "lucide-react";
import { useEffect } from "react";
import { ALL_LAYERS_OFF, ALL_LAYERS_ON, LAYER_LABEL, LAYER_NAMES, type ChartLayers, type LayerName } from "../layers";

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
  /** Items the API sent per layer, where it reports a count (null: not reported). */
  counts: Partial<Record<LayerName, number | null>>;
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
  return (
    <div className="cw-layers" role="group" aria-label="Chart layers" data-testid="layer-toolbar">
      {LAYER_NAMES.map((name, i) => {
        const count = counts[name];
        return (
          <button
            key={name}
            type="button"
            className="cw-layers__btn"
            aria-pressed={layers[name]}
            data-testid={`layer-${name}`}
            data-count={count ?? ""}
            title={`${layers[name] ? "Hide" : "Show"} ${LAYER_LABEL[name].toLowerCase()} · Alt ${i + 1}`}
            onClick={() => onLayers((l) => ({ ...l, [name]: !l[name] }))}
          >
            {LAYER_LABEL[name]}
            {count !== undefined && count !== null ? <span className="cw-layers__n">{count.toLocaleString("en-GB")}</span> : null}
          </button>
        );
      })}
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
    </div>
  );
}
