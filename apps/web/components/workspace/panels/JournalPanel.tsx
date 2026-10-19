"use client";

import Link from "next/link";
import { AsyncBoundary } from "@/components/AsyncBoundary";
import { FigureValue } from "@/components/FigureValue";
import { formatUtcTime } from "@/lib/format";
import { useApi } from "@/lib/query";
import { route } from "@/lib/routes";
import type { Page, TradeRow } from "@/lib/types";
import { PanelStamp } from "../PanelStamp";
import type { PanelProps } from "../types";

/** JRN in a tile: closed trades on the linked instrument (every source, provenance per row). */
export default function JournalPanel({ symbol, onSymbol, maximised }: PanelProps) {
  const limit = maximised ? 200 : 40;
  const path = `/api/trading/trades?limit=${limit}${symbol ? `&instrument=${encodeURIComponent(symbol)}` : ""}`;
  const trades = useApi<Page<TradeRow>>(path, { refreshMs: 30_000 });
  return (
    <AsyncBoundary
      state={trades}
      label="the journal"
      onRetry={trades.reload}
      isEmpty={(p) => p.items.length === 0}
      emptyTitle="No closed trades"
      emptyBody={symbol ? `No closed trade on ${symbol} from any source.` : "No closed trade from any source."}
    >
      {(page) => (
        <>
          <PanelStamp source={page.source} extra={`${page.items.length} of ${page.total}${symbol ? ` · ${symbol}` : ""}`} />
          <div className="table-wrap">
            <table className="dense ws-table" data-testid="ws-journal">
              <thead>
                <tr>
                  <th scope="col">Exit (UTC)</th>
                  <th scope="col">Instr</th>
                  <th scope="col">Side</th>
                  <th scope="col">Source</th>
                  <th scope="col" className="num">Net</th>
                  <th scope="col" className="num">R</th>
                </tr>
              </thead>
              <tbody>
                {page.items.map((t) => (
                  <tr key={t.trade_id}>
                    <td className="mono">
                      <Link href={route.trade(t.trade_id)} prefetch={false}>
                        {formatUtcTime(t.exit_time)}
                      </Link>
                    </td>
                    <td>
                      <button type="button" className="ws-sym" onClick={() => onSymbol(t.instrument)}>
                        {t.instrument}
                      </button>
                    </td>
                    <td>{t.direction}</td>
                    <td className="mono">{t.provenance}</td>
                    <td className="num">
                      <FigureValue figure={t.net_pnl} showChip={false} colourSign />
                    </td>
                    <td className="num">
                      <FigureValue figure={t.r_multiple} showChip={false} colourSign />
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </>
      )}
    </AsyncBoundary>
  );
}
