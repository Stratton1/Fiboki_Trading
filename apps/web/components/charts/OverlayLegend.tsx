"use client";

import type { ChartModel, SeriesGroup } from "@/lib/chart-model";
import type { OverlayView, Provenance } from "@/lib/types";
import { Popover } from "../ui/Popover";
import { ViewStateTag } from "../ui/ViewStateTag";

/**
 * What is on the chart, what is not, and why (report G C4 to C6, C14).
 *
 *  - one toggle per overlay layer the chart draws (regimes, signals, fills,
 *    levels) with the number of items the API sent;
 *  - one toggle per indicator per pane, with its series-role swatch. State
 *    and ratio columns start hidden because each opens its own pane; they
 *    are listed, never dropped;
 *  - every section the API reports as unavailable, with the API's own reason
 *    ("No paper journal…"), so an empty layer is never mistaken for a quiet
 *    one;
 *  - items the API sent that this chart does not draw (calendar events and
 *    headlines: report G C8, not built yet), and items outside the bar
 *    window, counted.
 */

export type LayerKey = "regimes" | "signals" | "fills" | "levels";

export const LAYER_SECTIONS: Record<LayerKey, string[]> = {
  regimes: ["regimes"],
  signals: ["signals"],
  fills: ["fills"],
  levels: ["levels"],
};

const LAYER_LABEL: Record<LayerKey, string> = {
  regimes: "Regimes",
  signals: "Signals",
  fills: "Fills",
  levels: "Levels",
};

/** A marker glyph in the provenance grammar: filled executed, hollow simulated. */
export function MarkerGlyph({
  shape,
  fill,
  ink,
  struck = false,
}: {
  shape: "up" | "down" | "diamond" | "circle" | "square";
  fill: "filled" | "hollow";
  ink: Provenance | "neutral";
  struck?: boolean;
}) {
  const path: Record<typeof shape, string> = {
    up: "M6 1.5 L10.5 10 L1.5 10 Z",
    down: "M6 10.5 L10.5 2 L1.5 2 Z",
    diamond: "M6 1 L11 6 L6 11 L1 6 Z",
    circle: "M6 1.5 A4.5 4.5 0 1 1 5.99 1.5 Z",
    square: "M2 2 H10 V10 H2 Z",
  };
  return (
    <svg className="glyph chart-ink" data-ink={ink} data-fill={fill} viewBox="0 0 12 12" aria-hidden="true">
      <path d={path[shape]} />
      {struck ? <path className="glyph__strike" d="M1 11 L11 1" /> : null}
    </svg>
  );
}

function sectionDetails(overlays: OverlayView | null) {
  if (!overlays) return [];
  // Sections the API says are unavailable, grouped by the reason it gave.
  const byDetail = new Map<string, string[]>();
  for (const [name, status] of Object.entries(overlays.sections)) {
    if (status.available) continue;
    const list = byDetail.get(status.detail);
    if (list) list.push(name);
    else byDetail.set(status.detail, [name]);
  }
  return [...byDetail.entries()].map(([detail, names]) => ({ detail, names: names.sort() }));
}

function groupLabel(group: SeriesGroup): string {
  if (group.paneKind === "price") return `${group.indicatorId} · price`;
  if (group.paneKind === "state") return `${group.indicatorId} · states`;
  return `${group.indicatorId} · own pane`;
}

export function OverlayLegend({
  model,
  overlays,
  overlaysPending,
  layers,
  onLayer,
  hidden,
  onGroup,
}: {
  model: ChartModel;
  overlays: OverlayView | null;
  /** The overlays read has not answered yet (or failed; the page says which). */
  overlaysPending: boolean;
  layers: Record<LayerKey, boolean>;
  onLayer: (layer: LayerKey) => void;
  hidden: ReadonlySet<string>;
  onGroup: (group: string) => void;
}) {
  const counts: Record<LayerKey, number> = {
    regimes: model.regimes.length,
    signals: model.signals.length,
    fills: model.fills.length,
    levels: model.levels.length,
  };
  const unavailable = sectionDetails(overlays);
  const unavailableNames = new Set(unavailable.flatMap((u) => u.names));
  const u = model.unplaced;
  const outside = [
    u.signals ? `${u.signals} signal${u.signals === 1 ? "" : "s"}` : "",
    u.fills ? `${u.fills} fill${u.fills === 1 ? "" : "s"}` : "",
    u.regimes ? `${u.regimes} regime run${u.regimes === 1 ? "" : "s"}` : "",
    u.seriesPoints ? `${u.seriesPoints} series point${u.seriesPoints === 1 ? "" : "s"}` : "",
  ].filter(Boolean);
  // A layer the chart DRAWS being unavailable is the one thing an operator
  // must not miss: the notes open themselves when that is so.
  const drawnMissing = ["regimes", "signals", "fills", "levels", "series"].some((name) =>
    unavailableNames.has(name),
  );
  const notDrawn = overlays !== null && (overlays.events.length > 0 || overlays.headlines.length > 0);

  return (
    <div className="legend" data-testid="overlay-legend">
      <div className="legend__row" role="group" aria-label="Overlay layers">
        {(Object.keys(LAYER_LABEL) as LayerKey[]).map((layer) => {
          const absent = LAYER_SECTIONS[layer].some((s) => unavailableNames.has(s));
          return (
            <button
              key={layer}
              type="button"
              className="legend__toggle"
              aria-pressed={layers[layer]}
              data-testid={`legend-layer-${layer}`}
              data-count={overlaysPending ? "" : counts[layer]}
              data-available={overlaysPending ? "" : String(!absent)}
              onClick={() => onLayer(layer)}
            >
              <span>{LAYER_LABEL[layer]}</span>
              <span className="legend__count">
                {overlaysPending ? "…" : absent ? "n/a" : counts[layer].toLocaleString("en-GB")}
              </span>
            </button>
          );
        })}
        <Popover
          title="Chart key"
          testId="chart-key-popover"
          trigger={
            <button type="button" className="legend__toggle" data-testid="chart-key">
              Key
            </button>
          }
        >
          <ul className="legend__key">
            <li>
              <MarkerGlyph shape="up" fill="filled" ink="paper" /> long signal, below the bar
            </li>
            <li>
              <MarkerGlyph shape="down" fill="filled" ink="paper" /> short signal, above the bar
            </li>
            <li>
              <MarkerGlyph shape="diamond" fill="filled" ink="paper" /> signal whose side the platform
              could not match to a fill
            </li>
            <li>
              <MarkerGlyph shape="up" fill="filled" ink="paper" struck /> struck through: the gateway
              BLOCKED it
            </li>
            <li>
              <MarkerGlyph shape="circle" fill="filled" ink="paper" /> entry fill,{" "}
              <MarkerGlyph shape="square" fill="filled" ink="paper" /> exit fill, at the recorded price
            </li>
            <li>
              <MarkerGlyph shape="circle" fill="filled" ink="paper" /> filled = executed (paper, shadow,
              demo, live) ·{" "}
              <MarkerGlyph shape="circle" fill="hollow" ink="backtest" /> hollow = simulated
            </li>
            <li>
              Entry-to-exit line: coloured by the sign of the platform&apos;s net P&amp;L, dashed when
              simulated.
            </li>
            <li>Levels: entry solid, stop dashed, target dotted, labelled on the price axis.</li>
            <li>
              Candles: rising bars hollow, falling bars filled. Neutral ink; green and red mean P&amp;L
              only.
            </li>
            <li>
              Regimes: a tint per run and a ribbon with its label. UNKNOWN is hatched, never drawn as
              range.
            </li>
            <li>
              Signals sit on the bar open at their decision time: the API reports when the gateway
              decided, not which closed bar produced the signal.
            </li>
          </ul>
        </Popover>
      </div>

      {model.groups.length > 0 ? (
        <div className="legend__row" role="group" aria-label="Indicator series">
          {model.groups.map((group) => (
            <button
              key={group.id}
              type="button"
              className="legend__toggle"
              aria-pressed={!hidden.has(group.id)}
              data-testid="legend-series-group"
              data-group={group.id}
              data-pane={group.pane}
              data-lines={group.seriesIds.length}
              onClick={() => onGroup(group.id)}
            >
              <span className="series-swatch" data-role={group.role} data-style="solid" aria-hidden="true" />
              <span>{groupLabel(group)}</span>
              <span className="legend__count">{group.seriesIds.length}</span>
            </button>
          ))}
        </div>
      ) : null}

      {unavailable.length > 0 || outside.length > 0 || notDrawn ? (
        <details className="legend__details" open={drawnMissing} data-testid="legend-details">
          <summary className="legend__summary">
            {[
              unavailable.length > 0
                ? `${unavailableNames.size} section${unavailableNames.size === 1 ? "" : "s"} not available`
                : "",
              notDrawn ? "received, not drawn" : "",
              outside.length > 0 ? "outside the bar window" : "",
            ]
              .filter(Boolean)
              .join(" · ")}
          </summary>
          <ul className="legend__notes" data-testid="legend-notes">
            {unavailable.map((u) => (
              <li key={u.detail} data-testid="legend-unavailable" data-sections={u.names.join(",")}>
                <ViewStateTag state="absent" /> <strong>{u.names.join(", ")}</strong>: {u.detail}
              </li>
            ))}
            {overlays && notDrawn ? (
              <li data-testid="legend-not-drawn">
                Received but not drawn on this chart yet: {overlays.events.length.toLocaleString("en-GB")}{" "}
                calendar event(s), {overlays.headlines.length.toLocaleString("en-GB")} headline(s).
              </li>
            ) : null}
            {outside.length > 0 ? (
              <li data-testid="legend-unplaced">
                Outside the bar window, not drawn: {outside.join(", ")}.
              </li>
            ) : null}
          </ul>
        </details>
      ) : null}
    </div>
  );
}
