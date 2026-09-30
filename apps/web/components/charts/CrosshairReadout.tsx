"use client";

import type { ChartModel, ModelSeries } from "@/lib/chart-model";
import { formatNumber } from "@/lib/format";
import { formatBarTime } from "./time";

/**
 * The crosshair readout (report G C15): the bar under the crosshair, or under
 * the keyboard cursor, or the latest bar when neither is on the chart, with
 * OHLC, volume and every VISIBLE series' value at that bar, in the mono font.
 *
 * Nothing on the canvas is only on the canvas: this is the text form of what
 * the crosshair points at, and the keyboard cursor (arrow keys on the chart)
 * makes it reachable without a pointer. It is a live region only while the
 * keyboard drives it, so pointer movement is not announced bar by bar.
 *
 * A value the API did not send is "no data", never 0.
 */

export type ReadoutSource = "keyboard" | "pointer" | "latest";

function shortName(s: ModelSeries): string {
  const prefix = `${s.indicatorId}_`;
  return s.name.startsWith(prefix) ? s.name.slice(prefix.length) : s.name;
}

function Value({ value, decimals, testId }: { value: number | null | undefined; decimals: number | null; testId: string }) {
  if (value === null || value === undefined) {
    return (
      <span className="figure__missing" data-testid={testId} data-value="">
        no data
      </span>
    );
  }
  return (
    <span className="readout__num" data-testid={testId} data-value={value}>
      {formatNumber(value, "", { decimals })}
    </span>
  );
}

export function CrosshairReadout({
  id,
  model,
  index,
  source,
  hidden,
  priceDecimals,
  paneDecimals,
  volumeKind,
}: {
  id: string;
  model: ChartModel;
  index: number | null;
  source: ReadoutSource;
  hidden: ReadonlySet<string>;
  priceDecimals: number | null;
  paneDecimals: ReadonlyMap<string, number>;
  volumeKind: string | null;
}) {
  const at = index === null ? model.times.length - 1 : index;
  const bar = model.bars[at];
  const time = model.times[at];
  if (!bar || time === undefined) {
    return (
      <div className="readout" id={id} data-testid="crosshair-readout" data-index="">
        <span className="muted">No bar under the crosshair.</span>
      </div>
    );
  }
  const visible = model.series.filter((s) => !hidden.has(s.group));
  const groups = new Map<string, ModelSeries[]>();
  for (const s of visible) {
    const list = groups.get(s.group);
    if (list) list.push(s);
    else groups.set(s.group, [s]);
  }
  const volumeLabel = volumeKind === "tick_volume" ? "Tick vol" : "Vol";

  return (
    <div
      className="readout"
      id={id}
      data-testid="crosshair-readout"
      data-index={at}
      data-time={time}
      data-source={source}
      aria-live={source === "keyboard" ? "polite" : "off"}
      aria-label="Crosshair readout"
      role="group"
      // Focusable so that, when many series make it scroll, the keyboard can
      // scroll it too (WCAG 2.1.1; axe scrollable-region-focusable).
      tabIndex={0}
    >
      <div className="readout__row readout__row--bar">
        <span className="readout__time" data-testid="readout-time">
          {formatBarTime(time)}
        </span>
        <span className="readout__tag">
          {source === "latest" ? "latest bar" : source === "keyboard" ? "cursor" : "crosshair"}
        </span>
        {(["o", "h", "l", "c"] as const).map((k) => (
          <span className="readout__pair" key={k}>
            <span className="readout__key">{k.toUpperCase()}</span>
            <Value value={bar[k]} decimals={priceDecimals} testId={`readout-${k}`} />
          </span>
        ))}
        {model.hasVolume ? (
          <span className="readout__pair">
            <span className="readout__key">{volumeLabel}</span>
            <Value value={bar.v} decimals={0} testId="readout-v" />
          </span>
        ) : null}
      </div>
      {[...groups.entries()].map(([group, list]) => {
        const first = list[0] as ModelSeries;
        const decimals = first.pane === "price" ? priceDecimals : (paneDecimals.get(first.pane) ?? null);
        return (
          <div className="readout__row" key={group} data-testid="readout-group" data-group={group}>
            <span className="readout__group">{first.indicatorId}</span>
            {list.map((s) => (
              <span className="readout__pair" key={s.id} data-testid="readout-series" data-series={s.id}>
                <span className="series-swatch" data-role={s.role} data-style={s.lineStyle} aria-hidden="true" />
                <span className="readout__key">
                  {shortName(s)}
                  {s.displayOnly ? " (display only)" : ""}
                </span>
                <Value value={s.values[at]} decimals={decimals} testId="readout-series-value" />
              </span>
            ))}
          </div>
        );
      })}
    </div>
  );
}
