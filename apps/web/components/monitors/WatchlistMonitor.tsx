"use client";

import Link from "next/link";
import { useEffect, useMemo, useState } from "react";
import { AsyncBoundary } from "@/components/AsyncBoundary";
import { CaveatList } from "@/components/CaveatList";
import { DataGrid, preloadGrid, type GridColumn } from "@/components/grid";
import { PanelStamp } from "@/components/workspace/PanelStamp";
import { useToast } from "@/components/ui/Toast";
import { ApiError } from "@/lib/api";
import { formatAge, formatUtcTime, priceDecimals } from "@/lib/format";
import {
  DATA_STATE_WORD,
  MONITOR_PATHS,
  QUOTE_WINDOWS,
  deleteWatchlist,
  saveWatchlist,
  type QuoteRow,
  type QuoteWindow,
  type Watchlist,
} from "@/lib/monitors";
import { invalidatePath, useApi } from "@/lib/query";
import { route } from "@/lib/routes";
import type { InstrumentRow, Page } from "@/lib/types";
import { useQueryClient } from "@tanstack/react-query";
import { Sparkline } from "./Sparkline";

/**
 * WL: a watchlist over RECORDED quotes. The list is the operator's, kept on
 * the server (versioned); the rows are GET /api/markets/watchlists/<name>/quotes:
 * the recorder's latest quote, the change and sparkline over the window, the
 * session state, the data age and the API's verdict on it (LIVE, STALE,
 * CLOSED, NO DATA), with the API's conditional-format tones. The grid keeps
 * saved column sets on the server.
 */
export function WatchlistMonitor({
  list,
  onList,
  window,
  onWindow,
  onSymbol,
  selected,
  compact = false,
  readOnly = false,
}: {
  list: string;
  onList: (name: string) => void;
  window: QuoteWindow;
  onWindow: (w: QuoteWindow) => void;
  /** Activate a row: publish to the link group (panel) or open the chart (page). */
  onSymbol?: (symbol: string) => void;
  selected?: string | null;
  compact?: boolean;
  readOnly?: boolean;
}) {
  const lists = useApi<Page<Watchlist>>(MONITOR_PATHS.watchlists);
  const rows = useApi<Page<QuoteRow>>(MONITOR_PATHS.watchlistQuotes(list, window), { refreshMs: 15_000 });
  const instruments = useApi<Page<InstrumentRow>>("/api/markets/instruments");
  useEffect(() => {
    void preloadGrid();
  }, []);
  const current = lists.status === "success" ? lists.data.items.find((l) => l.name === list) : undefined;
  const pips = useMemo(() => {
    const out = new Map<string, number | null>();
    if (instruments.status === "success") for (const i of instruments.data.items) out.set(i.symbol, i.pip_size.value);
    return out;
  }, [instruments]);

  const columns = useMemo<GridColumn<QuoteRow>[]>(() => {
    const dec = (row: QuoteRow) => priceDecimals(pips.get(row.symbol) ?? null);
    return [
      {
        id: "symbol",
        header: "Symbol",
        pin: true,
        width: 96,
        value: (r) => r.symbol,
        cell: (r) =>
          onSymbol ? (
            <button type="button" className="ws-sym" onClick={() => onSymbol(r.symbol)} data-testid="wl-symbol">
              {r.symbol}
            </button>
          ) : (
            <Link href={route.instrument(r.symbol)} className="mono" prefetch={false} data-testid="wl-symbol">
              {r.symbol}
            </Link>
          ),
      },
      { id: "spark", header: `Mid ${window}`, width: 110, sortable: false, filterable: false, csv: false, cell: (r) => <Sparkline series={r.sparkline} /> },
      { id: "mid", header: "Mid", kind: "figure", figure: (r) => r.mid, decimals: dec },
      { id: "bid", header: "Bid", kind: "figure", figure: (r) => r.bid, decimals: dec, hidden: compact },
      { id: "ask", header: "Ask", kind: "figure", figure: (r) => r.ask, decimals: dec, hidden: compact },
      { id: "spread", header: "Spread", kind: "figure", figure: (r) => r.spread_pips, width: 90 },
      { id: "change", header: `Chg ${window}`, kind: "figure", figure: (r) => r.change_pct, width: 96 },
      { id: "range", header: `Range ${window}`, kind: "figure", figure: (r) => r.range_pct, width: 96, hidden: compact },
      {
        id: "age",
        header: "Data",
        width: 120,
        value: (r) => r.age_s.value,
        csv: (r) => r.data_state,
        cell: (r) => (
          <span title={r.data_state_detail} data-state={r.data_state} data-testid="wl-state">
            {DATA_STATE_WORD[r.data_state]}
            {r.age_s.value !== null ? <span className="muted"> {formatAge(r.age_s.value)}</span> : null}
          </span>
        ),
      },
      {
        id: "session",
        header: "Session",
        width: 80,
        value: (r) => (r.session.open ? "open" : "closed"),
        cell: (r) => <span title={`${r.session.calendar}: ${r.session.trading_hours}`}>{r.session.open ? "OPEN" : "CLOSED"}</span>,
      },
      { id: "quote_time", header: "Quote (UTC)", kind: "time", hidden: true, value: (r) => r.quote_time, cell: (r) => (r.quote_time ? formatUtcTime(r.quote_time) : "—") },
      { id: "provider", header: "Provider", hidden: true, value: (r) => r.provider },
    ];
  }, [pips, window, onSymbol, compact]);

  return (
    <div className="wl" data-testid="watchlist" data-list={list}>
      <div className="ws-panel__bar">
        <label className="ws-inline">
          <span className="ws-toolbar__label">List</span>
          <select value={list} onChange={(e) => onList(e.target.value)} data-testid="wl-list">
            {lists.status === "success"
              ? lists.data.items.map((l) => (
                  <option key={l.name} value={l.name}>
                    {l.name}
                    {l.builtin ? " (built-in)" : ""}
                  </option>
                ))
              : <option value={list}>{list}</option>}
          </select>
        </label>
        <label className="ws-inline">
          <span className="sr-only">Window</span>
          <select value={window} onChange={(e) => onWindow(e.target.value as QuoteWindow)} data-testid="wl-window">
            {QUOTE_WINDOWS.map((w) => (
              <option key={w} value={w}>
                {w}
              </option>
            ))}
          </select>
        </label>
        {!readOnly && current ? (
          <ListEditor list={current} instruments={instruments.status === "success" ? instruments.data.items : []} onList={onList} />
        ) : null}
      </div>
      <AsyncBoundary
        state={rows}
        label={`watchlist ${list}`}
        onRetry={rows.reload}
        isEmpty={(p) => p.items.length === 0}
        emptyTitle="An empty list"
        emptyBody="This watchlist names no symbols. Add one above."
      >
        {(page) => (
          <>
            <PanelStamp source={page.source} extra={`${page.items.length} symbols · stale after the API's threshold`} />
            <CaveatList caveats={page.caveats.filter((c) => c.severity !== "info")} />
            <DataGrid<QuoteRow>
              id="watchlist"
              label={`Watchlist ${list}`}
              rows={page.items}
              columns={columns}
              rowKey={(r) => r.symbol}
              tones={(r) => r.tones}
              formatRules="watchlist"
              serverSets
              source={{ source: page.source, path: MONITOR_PATHS.watchlistQuotes(list, window) }}
              onRowActivate={(r) => onSymbol?.(r.symbol)}
              maxHeight={compact ? "min(60vh, 520px)" : "min(70vh, 720px)"}
              testId="wl-grid"
              rowTestId="wl-row"
            />
            {selected ? <span className="sr-only">Selected {selected}</span> : null}
          </>
        )}
      </AsyncBoundary>
    </div>
  );
}

function ListEditor({
  list,
  instruments,
  onList,
}: {
  list: Watchlist;
  instruments: InstrumentRow[];
  onList: (name: string) => void;
}) {
  const client = useQueryClient();
  const notify = useToast();
  const [symbol, setSymbol] = useState("");
  const [newName, setNewName] = useState("");
  const known = new Set(instruments.map((i) => i.symbol));
  const refresh = () => {
    void invalidatePath(client, MONITOR_PATHS.watchlists);
    void invalidatePath(client, MONITOR_PATHS.watchlistQuotes(list.name, "24h"));
  };
  const write = async (name: string, base: number, symbols: string[]) => {
    try {
      await saveWatchlist(name, base, symbols);
      refresh();
      return true;
    } catch (error) {
      notify(
        error instanceof ApiError && error.code === "workspace_conflict"
          ? `${name} changed elsewhere; reloaded. Make the change again.`
          : error instanceof Error
            ? error.message
            : String(error),
      );
      refresh();
      return false;
    }
  };
  const sym = symbol.trim().toUpperCase();
  return (
    <>
      <form
        className="ws-inline"
        onSubmit={async (e) => {
          e.preventDefault();
          if (!known.has(sym) || list.symbols.includes(sym)) return;
          if (await write(list.name, list.version, [...list.symbols, sym])) setSymbol("");
        }}
      >
        <label className="sr-only" htmlFor="wl-add">
          Add a symbol to {list.name}
        </label>
        <input
          id="wl-add"
          className="ws-input mono"
          list="wl-symbols"
          placeholder="+ symbol"
          value={symbol}
          onChange={(e) => setSymbol(e.target.value.toUpperCase())}
          data-testid="wl-add"
        />
        <datalist id="wl-symbols">
          {instruments.map((i) => (
            <option key={i.symbol} value={i.symbol} />
          ))}
        </datalist>
        <button type="submit" className="ws-btn" disabled={!known.has(sym) || list.symbols.includes(sym)} data-testid="wl-add-go">
          Add
        </button>
      </form>
      <label className="ws-inline">
        <span className="sr-only">Remove a symbol</span>
        <select
          value=""
          onChange={(e) => {
            const drop = e.target.value;
            if (drop) void write(list.name, list.version, list.symbols.filter((s) => s !== drop));
          }}
          data-testid="wl-remove"
        >
          <option value="">− symbol…</option>
          {list.symbols.map((s) => (
            <option key={s} value={s}>
              {s}
            </option>
          ))}
        </select>
      </label>
      <form
        className="ws-inline"
        onSubmit={async (e) => {
          e.preventDefault();
          const name = newName.trim();
          if (!name) return;
          if (await write(name, 0, [])) {
            setNewName("");
            onList(name);
          }
        }}
      >
        <label className="sr-only" htmlFor="wl-new">
          New watchlist name
        </label>
        <input id="wl-new" className="ws-input" placeholder="New list…" maxLength={48} value={newName} onChange={(e) => setNewName(e.target.value)} data-testid="wl-new" />
      </form>
      {!list.builtin ? (
        <button
          type="button"
          className="ws-btn ws-btn--ghost"
          data-testid="wl-delete"
          onClick={async () => {
            if (!window.confirm(`Delete watchlist ${list.name}?`)) return;
            try {
              await deleteWatchlist(list.name, list.version);
              refresh();
              onList("Majors");
            } catch (error) {
              notify(error instanceof Error ? error.message : String(error));
            }
          }}
        >
          Delete list
        </button>
      ) : (
        <span className="muted" title="A built-in list is code; adding a symbol saves your own copy under this name.">
          built-in
        </span>
      )}
    </>
  );
}
