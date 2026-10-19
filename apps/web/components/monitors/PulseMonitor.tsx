"use client";

import Link from "next/link";
import { useEffect, useMemo } from "react";
import { AsyncBoundary } from "@/components/AsyncBoundary";
import { CaveatList } from "@/components/CaveatList";
import { FigureValue } from "@/components/FigureValue";
import { DataGrid, preloadGrid, type GridColumn } from "@/components/grid";
import { PanelStamp } from "@/components/workspace/PanelStamp";
import { formatAge, formatUtcTime } from "@/lib/format";
import { DATA_STATE_WORD, QUOTE_WINDOWS, pulsePath, type PulseView, type QuoteRow, type QuoteWindow } from "@/lib/monitors";
import { useApi } from "@/lib/query";
import { route } from "@/lib/routes";
import type { Envelope } from "@/lib/types";
import { Sparkline } from "./Sparkline";

/**
 * Market Pulse (PULSE): movers, currency strength, volatility and the session
 * clocks over EVERY instrument the recorder has quotes for, all computed by
 * GET /api/markets/pulse (marketstate/quote_stats.py). This component scales
 * the API's numbers to bars and draws them; it computes none of them.
 */
export function PulseMonitor({
  window,
  onWindow,
  onSymbol,
  compact = false,
}: {
  window: QuoteWindow;
  onWindow: (w: QuoteWindow) => void;
  onSymbol?: (symbol: string) => void;
  compact?: boolean;
}) {
  const state = useApi<Envelope<PulseView>>(pulsePath(window), { refreshMs: 30_000 });
  useEffect(() => {
    if (!compact) void preloadGrid();
  }, [compact]);
  const sym = (s: string) =>
    onSymbol ? (
      <button type="button" className="ws-sym" onClick={() => onSymbol(s)} data-testid="pulse-symbol">
        {s}
      </button>
    ) : (
      <Link href={route.instrument(s)} className="mono" prefetch={false} data-testid="pulse-symbol">
        {s}
      </Link>
    );
  return (
    <div className="pulse" data-testid="pulse" data-compact={compact || undefined}>
      <div className="ws-panel__bar">
        <label className="ws-inline">
          <span className="ws-toolbar__label">Window</span>
          <select value={window} onChange={(e) => onWindow(e.target.value as QuoteWindow)} data-testid="pulse-window">
            {QUOTE_WINDOWS.map((w) => (
              <option key={w} value={w}>
                {w}
              </option>
            ))}
          </select>
        </label>
      </div>
      <AsyncBoundary
        state={state}
        label="Market Pulse"
        onRetry={state.reload}
        isEmpty={(e) => e.data.rows.length === 0}
        emptyTitle="Nothing recorded in the window"
        emptyBody="The quote recorder has no quote for any instrument in this window, so there is no pulse to show. This is a missing input, not a calm market."
      >
        {(envelope) => {
          const v = envelope.data;
          return (
            <>
              <PanelStamp source={envelope.source} extra={`${v.instruments_recorded} of ${v.instruments_registered} registered instruments recorded`} />
              <CaveatList caveats={envelope.caveats} />
              <ol className="pulse__sessions" aria-label="Sessions" data-testid="pulse-sessions">
                {v.sessions.map((s) => (
                  <li key={s.key} data-open={s.open || undefined} title={s.detail}>
                    <span className="pulse__session-name">{s.label}</span>
                    <span className="mono">{s.local_time}</span>
                    <span className="pulse__session-state">{s.open ? "OPEN" : "CLOSED"}</span>
                    <span className="muted mono">
                      {s.open ? "closes" : "opens"} {formatUtcTime(s.next_change_at)}
                    </span>
                  </li>
                ))}
              </ol>
              <div className="pulse__grid">
                <section aria-label="Movers" className="pulse__card" data-testid="pulse-movers">
                  <h2 className="pulse__h">Movers · {v.window}</h2>
                  <div className="pulse__movers">
                    <ol data-testid="pulse-up">
                      {v.movers_up.map((m) => (
                        <li key={m.symbol}>
                          {sym(m.symbol)} <FigureValue figure={m.change_pct} showChip={false} />
                        </li>
                      ))}
                      {v.movers_up.length === 0 ? <li className="muted">none up</li> : null}
                    </ol>
                    <ol data-testid="pulse-down">
                      {v.movers_down.map((m) => (
                        <li key={m.symbol}>
                          {sym(m.symbol)} <FigureValue figure={m.change_pct} showChip={false} />
                        </li>
                      ))}
                      {v.movers_down.length === 0 ? <li className="muted">none down</li> : null}
                    </ol>
                  </div>
                </section>
                <Strength view={v} />
                {!compact ? (
                  <section aria-label="Volatility" className="pulse__card" data-testid="pulse-vol">
                    <h2 className="pulse__h">Realised volatility · {v.window}</h2>
                    <table className="dense ws-table">
                      <thead>
                        <tr>
                          <th scope="col">Instr</th>
                          <th scope="col" className="num">Vol</th>
                          <th scope="col" className="num">Range</th>
                          <th scope="col" className="num">Spread ×</th>
                        </tr>
                      </thead>
                      <tbody>
                        {v.volatility.map((r) => (
                          <tr key={r.symbol}>
                            <td>{sym(r.symbol)}</td>
                            <td className="num"><FigureValue figure={r.realised_vol_pct} showChip={false} /></td>
                            <td className="num"><FigureValue figure={r.range_pct} showChip={false} /></td>
                            <td className="num"><FigureValue figure={r.spread_ratio} showChip={false} /></td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </section>
                ) : null}
              </div>
              {!compact ? <Universe view={v} onSymbol={onSymbol} /> : null}
              {!compact ? (
                <details className="pulse__defs">
                  <summary>Definitions (the API&apos;s)</summary>
                  <dl>
                    {Object.entries(v.definitions).map(([k, d]) => (
                      <div key={k}>
                        <dt className="mono">{k}</dt>
                        <dd>{d}</dd>
                      </div>
                    ))}
                  </dl>
                </details>
              ) : null}
            </>
          );
        }}
      </AsyncBoundary>
    </div>
  );
}

function Strength({ view }: { view: PulseView }) {
  const known = view.strength.filter((s) => s.score_pct.value !== null);
  const max = Math.max(0, ...known.map((s) => Math.abs(s.score_pct.value as number)));
  return (
    <section aria-label="Currency strength" className="pulse__card" data-testid="pulse-strength">
      <h2 className="pulse__h">Currency strength · {view.window}</h2>
      <ol className="pulse__strength">
        {view.strength.map((s) => {
          const value = s.score_pct.value;
          const share = value === null || max === 0 ? 0 : (Math.abs(value) / max) * 50;
          return (
            <li key={s.currency} data-currency={s.currency} data-sign={value === null ? "none" : value >= 0 ? "pos" : "neg"}>
              <span className="mono pulse__ccy">{s.currency}</span>
              <span className="pulse__bar" aria-hidden="true">
                <span
                  className="pulse__fill"
                  style={value !== null && value < 0 ? { right: "50%", width: `${share}%` } : { left: "50%", width: `${share}%` }}
                />
              </span>
              <span className="num">
                <FigureValue figure={s.score_pct} showChip={false} />
              </span>
              <span className="muted mono">n={s.pairs.value ?? "—"}</span>
            </li>
          );
        })}
      </ol>
    </section>
  );
}

function Universe({ view, onSymbol }: { view: PulseView; onSymbol?: (symbol: string) => void }) {
  const columns = useMemo<GridColumn<QuoteRow>[]>(
    () => [
      { id: "symbol", header: "Symbol", pin: true, width: 96, value: (r) => r.symbol, cell: (r) => <Link href={route.instrument(r.symbol)} className="mono" prefetch={false}>{r.symbol}</Link> },
      { id: "spark", header: "Mid", width: 110, sortable: false, filterable: false, csv: false, cell: (r) => <Sparkline series={r.sparkline} /> },
      { id: "change", header: "Chg", kind: "figure", figure: (r) => r.change_pct },
      { id: "range", header: "Range", kind: "figure", figure: (r) => r.range_pct },
      { id: "spread", header: "Spread", kind: "figure", figure: (r) => r.spread_pips },
      { id: "age", header: "Data", width: 120, value: (r) => r.age_s.value, csv: (r) => r.data_state, cell: (r) => <span title={r.data_state_detail}>{DATA_STATE_WORD[r.data_state]} {r.age_s.value !== null ? <span className="muted">{formatAge(r.age_s.value)}</span> : null}</span> },
      { id: "asset", header: "Class", value: (r) => r.asset_class },
    ],
    [],
  );
  return (
    <DataGrid<QuoteRow>
      id="pulse"
      label="Every recorded instrument"
      rows={view.rows}
      columns={columns}
      rowKey={(r) => r.symbol}
      tones={(r) => r.tones}
      formatRules="pulse"
      serverSets
      onRowActivate={(r) => onSymbol?.(r.symbol)}
      source={{ source: null, path: pulsePath(view.window) }}
      testId="pulse-grid"
      rowTestId="pulse-row"
    />
  );
}
