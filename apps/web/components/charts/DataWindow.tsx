"use client";

import { Pin, PinOff } from "lucide-react";
import { useState } from "react";
import type { DataWindowView } from "@/lib/chart-contract";
import { placeAt, toSeconds, type ChartModel, type PlacedTrade } from "@/lib/chart-model";
import { formatFigure, formatNumber } from "@/lib/format";
import { useApi } from "@/lib/query";
import type { Envelope, OverlayView } from "@/lib/types";
import { formatBarTime, type TimeZoneId } from "./time";

/**
 * The data window (programme feature 14): everything the platform sent for
 * the bar under the crosshair (or the keyboard cursor, or the latest bar):
 * OHLC and volume, EVERY indicator output (hidden ones included and marked),
 * the regime, signals, fills, standing levels, calendar events and the
 * trades that span the bar.
 *
 * Following the crosshair, it reads what the chart already holds from the
 * API (bars, overlays, trades), placed on the bar by time. PINNED, it asks
 * the platform for that one bar (GET /api/markets/datawindow/{symbol}): the
 * same computation, unfiltered, with headlines and section notes. Nothing is
 * computed here; a value the API did not send is "no data", never 0.
 */

export interface DataWindowProps {
  symbol: string;
  tf: string;
  model: ChartModel;
  overlays: OverlayView | null;
  index: number | null;
  hidden: ReadonlySet<string>;
  priceDecimals: number | null;
  paneDecimals: ReadonlyMap<string, number>;
  trades: readonly PlacedTrade[];
  tz: TimeZoneId;
}

function Num({ value, decimals, unit = "" }: { value: number | null | undefined; decimals: number | null | undefined; unit?: string }) {
  if (value === null || value === undefined) return <span className="figure__missing">no data</span>;
  return <span className="num">{formatNumber(value, unit, { decimals: decimals ?? null })}</span>;
}

function Row({ label, children, testId }: { label: string; children: React.ReactNode; testId?: string }) {
  return (
    <>
      <dt>{label}</dt>
      <dd data-testid={testId}>{children}</dd>
    </>
  );
}

export function DataWindow(props: DataWindowProps) {
  const { symbol, tf, model, overlays, index, hidden, priceDecimals, paneDecimals, trades, tz } = props;
  const n = model.times.length;
  const live = index !== null && index >= 0 && index < n ? index : n - 1;
  const [pinned, setPinned] = useState<string | null>(null);
  const pinPath = pinned
    ? `/api/markets/datawindow/${encodeURIComponent(symbol)}?timeframe=${tf}&t=${encodeURIComponent(pinned)}`
    : null;
  const pin = useApi<Envelope<DataWindowView>>(pinPath);
  const bar = model.bars[live];
  const time = model.times[live];
  if (!bar || time === undefined) {
    return (
      <p className="muted" data-testid="data-window-empty">
        No bar to read.
      </p>
    );
  }
  const pinIndex = pinned ? placeAt(model.times, pinned) : null;

  return (
    <section className="cw-dw" aria-label="Data window" data-testid="data-window" data-index={pinned ? (pinIndex ?? "") : live}>
      <header className="cw-dw__head">
        <span className="cw-dw__time num" data-testid="data-window-time">
          {formatBarTime(pinned ? toSeconds(pinned) : time, tz)}
        </span>
        <span className="cw-dw__mode muted">{pinned ? "pinned · from the platform" : "follows the crosshair"}</span>
        <button
          type="button"
          className="cw-dbar__btn cw-dbar__btn--text"
          data-testid="data-window-pin"
          aria-pressed={pinned !== null}
          onClick={() => setPinned(pinned ? null : bar.t)}
          title={pinned ? "Unpin: follow the crosshair again" : "Pin this bar and read it from the platform"}
        >
          {pinned ? <PinOff size={12} aria-hidden="true" /> : <Pin size={12} aria-hidden="true" />}
          {pinned ? "Unpin" : "Pin"}
        </button>
      </header>
      {pinned ? (
        pin.status === "loading" ? (
          <p className="muted" role="status" data-testid="data-window-reading">
            Reading the bar from the platform…
          </p>
        ) : pin.status === "error" ? (
          <p className="cw__note cw__note--error" role="alert" data-testid="data-window-error">
            Not loaded: {pin.error.message} <span className="state__code">code: {pin.error.code}</span>
          </p>
        ) : (
          <Pinned view={pin.data.data} priceDecimals={priceDecimals} paneDecimals={paneDecimals} hidden={hidden} model={model} />
        )
      ) : (
        <Live {...props} index={live} />
      )}
    </section>
  );
}

function Live({ model, overlays, index, hidden, priceDecimals, paneDecimals, trades }: DataWindowProps & { index: number }) {
  const bar = model.bars[index];
  if (!bar) return null;
  const regime = model.regimes.find((r) => r.start <= index && index <= r.end)?.item ?? null;
  const signals = model.signals.filter((s) => s.index === index).map((s) => s.item);
  const fills = model.fills.filter((f) => f.index === index).map((f) => f.item);
  const t = model.times[index] as number;
  const next = model.times[index + 1];
  const levels = model.levels.filter((l) => {
    const from = toSeconds(l.from);
    const to = l.to === null ? null : toSeconds(l.to);
    return from <= (next ?? t) && (to === null || to >= t);
  });
  const events = (overlays?.events ?? []).filter((e) => placeAt(model.times, e.t) === index);
  const spanning = trades.filter((tr) => tr.entryIndex <= index && index <= tr.exitIndex);
  return (
    <div className="cw-dw__body">
      <dl className="cw-dw__grid" data-testid="data-window-ohlc">
        <Row label="Open" testId="dw-open">
          <Num value={bar.o} decimals={priceDecimals} />
        </Row>
        <Row label="High" testId="dw-high">
          <Num value={bar.h} decimals={priceDecimals} />
        </Row>
        <Row label="Low" testId="dw-low">
          <Num value={bar.l} decimals={priceDecimals} />
        </Row>
        <Row label="Close" testId="dw-close">
          <Num value={bar.c} decimals={priceDecimals} />
        </Row>
        {model.hasVolume ? (
          <Row label="Volume" testId="dw-volume">
            <Num value={bar.v} decimals={0} />
          </Row>
        ) : null}
      </dl>
      <h3 className="cw-dw__h">Indicators ({model.series.length})</h3>
      {model.series.length === 0 ? (
        <p className="muted">No indicator series in this payload.</p>
      ) : (
        <dl className="cw-dw__grid" data-testid="data-window-values">
          {model.series.map((s) => (
            <Row key={s.id} label={`${s.name}${hidden.has(s.group) ? " (hidden)" : ""}${s.displayOnly ? " (display only)" : ""}`}>
              <Num
                value={s.values[index]}
                decimals={s.pane === "price" ? priceDecimals : paneDecimals.get(s.pane)}
              />
            </Row>
          ))}
        </dl>
      )}
      <Overlays
        regime={regime}
        signals={signals.map((s) => `${s.outcome} ${s.side} · ${s.strategy_id} · ${s.reason}`)}
        fills={fills.map(
          (f) =>
            `${f.role} ${f.side} ${f.price.value === null ? "no price" : formatNumber(f.price.value, "", { decimals: priceDecimals })}${
              f.net_pnl ? ` · ${formatFigure(f.net_pnl, { signed: true })}` : ""
            }`,
        )}
        levels={levels.map(
          (l) => `${l.role} ${l.price.value === null ? "no price" : formatNumber(l.price.value, "", { decimals: priceDecimals })} · ${l.strategy_id}`,
        )}
        events={events.map((e) => `${e.impact.toUpperCase()} ${e.currency} ${e.name}`)}
        trades={spanning.map((tr) => {
          const sl = tr.segments.find((s) => s.role === "stop")?.price ?? null;
          const tp = tr.segments.find((s) => s.role === "target")?.price ?? null;
          const px = (v: number | null) => (v === null ? "—" : formatNumber(v, "", { decimals: priceDecimals }));
          return `${tr.source === "backtest" ? "BT" : "PAPER"} ${tr.direction ?? "?"} ${tr.tradeId} · entry ${px(tr.entryPrice)} · SL ${px(sl)} · TP ${px(tp)}`;
        })}
      />
    </div>
  );
}

function Pinned({
  view,
  priceDecimals,
  paneDecimals,
  hidden,
  model,
}: {
  view: DataWindowView;
  priceDecimals: number | null;
  paneDecimals: ReadonlyMap<string, number>;
  hidden: ReadonlySet<string>;
  model: ChartModel;
}) {
  const groupOf = new Map(model.series.map((s) => [s.name, s.group]));
  return (
    <div className="cw-dw__body" data-testid="data-window-pinned">
      <dl className="cw-dw__grid">
        <Row label="Open">
          <Num value={view.bar.o} decimals={priceDecimals} />
        </Row>
        <Row label="High">
          <Num value={view.bar.h} decimals={priceDecimals} />
        </Row>
        <Row label="Low">
          <Num value={view.bar.l} decimals={priceDecimals} />
        </Row>
        <Row label="Close" testId="dw-pinned-close">
          <Num value={view.bar.c} decimals={priceDecimals} />
        </Row>
        {view.bar.v !== undefined ? (
          <Row label="Volume">
            <Num value={view.bar.v} decimals={0} />
          </Row>
        ) : null}
      </dl>
      <h3 className="cw-dw__h">Indicators ({view.values.length})</h3>
      <dl className="cw-dw__grid" data-testid="data-window-pinned-values">
        {view.values.map((v) => {
          const group = groupOf.get(v.name);
          return (
            <Row key={`${v.indicator_id}:${v.name}`} label={`${v.name}${group && hidden.has(group) ? " (hidden)" : ""}`}>
              <Num value={v.value} decimals={v.pane === "price" ? priceDecimals : paneDecimals.get(v.pane)} />
            </Row>
          );
        })}
      </dl>
      <Overlays
        regime={view.regime}
        signals={view.signals.map((s) => `${s.outcome} ${s.side} · ${s.strategy_id} · ${s.reason}`)}
        fills={view.fills.map((f) => `${f.role} ${f.side} ${f.price.value === null ? "no price" : formatNumber(f.price.value, "", { decimals: priceDecimals })}`)}
        levels={view.levels.map((l) => `${l.role} ${l.price.value === null ? "no price" : formatNumber(l.price.value, "", { decimals: priceDecimals })} · ${l.strategy_id}`)}
        events={view.events.map((e) => `${e.impact.toUpperCase()} ${e.currency} ${e.name}`)}
        headlines={view.headlines.map((h) => `${h.news_source}: ${h.title}`)}
        trades={null}
      />
      <p className="muted cw-dw__src">Dataset {view.dataset_version_id || "not reported"}.</p>
    </div>
  );
}

function Overlays({
  regime,
  signals,
  fills,
  levels,
  events,
  headlines,
  trades,
}: {
  regime: { label: string; axes: Record<string, string> } | null;
  signals: string[];
  fills: string[];
  levels: string[];
  events: string[];
  headlines?: string[];
  trades: string[] | null;
}) {
  const list = (title: string, items: string[], testId: string) => (
    <>
      <h3 className="cw-dw__h">
        {title} ({items.length})
      </h3>
      {items.length === 0 ? (
        <p className="muted cw-dw__none">none at this bar</p>
      ) : (
        <ul className="cw-dw__list" data-testid={testId}>
          {items.map((text, i) => (
            <li key={`${i}:${text}`}>{text}</li>
          ))}
        </ul>
      )}
    </>
  );
  return (
    <>
      <h3 className="cw-dw__h">Regime</h3>
      <p className="cw-dw__none" data-testid="dw-regime">
        {regime
          ? `${regime.label} · ${Object.entries(regime.axes)
              .map(([k, v]) => `${k} ${v}`)
              .join(", ")}`
          : "no regime run covers this bar"}
      </p>
      {list("Signals", signals, "dw-signals")}
      {list("Fills", fills, "dw-fills")}
      {list("Levels standing", levels, "dw-levels")}
      {list("Calendar events", events, "dw-events")}
      {headlines ? list("Headlines", headlines, "dw-headlines") : null}
      {trades ? list("Trades spanning the bar", trades, "dw-trades") : null}
    </>
  );
}
