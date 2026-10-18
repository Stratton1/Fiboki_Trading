"use client";

import type { PlacedTrade } from "@/lib/chart-model";
import { formatFigure, formatNumber, PROVENANCE_LABEL, signClass } from "@/lib/format";
import type { Figure } from "@/lib/types";
import { formatBarTime } from "./time";

/**
 * The figures of the trade under the pointer, exactly as the API sent them:
 * a backtest row from the research ledger, or a paper trade's two journal
 * fills. Pointer-only by nature; every value here is also in the trades table,
 * which is keyboard-reachable (report G C15). Nothing is computed: a missing
 * stop says "not recorded", never a number.
 */

const secs = (iso: string) => Math.floor(Date.parse(iso) / 1_000);

function value(figure: Figure | null | undefined, decimals: number | null, signed = false) {
  if (!figure) return <span className="muted">not recorded</span>;
  const text = formatFigure(figure, { decimals, signed });
  return text === null ? <span className="muted">not recorded</span> : <span className="num">{text}</span>;
}

export function TradeTooltip({
  trade,
  x,
  y,
  priceDecimals,
}: {
  trade: PlacedTrade;
  x: number;
  y: number;
  priceDecimals: number | null;
}) {
  const row = trade.row;
  const fills = trade.fills;
  const pnl = row ? row.net_pnl : (fills?.exit.net_pnl ?? null);
  const entryIso = row ? row.entry_time : (fills?.entry.t ?? null);
  const exitIso = row ? row.exit_time : (fills?.exit.t ?? null);
  const reason = row ? row.exit_reason : (fills?.exit.exit_reason ?? null);
  const stop = trade.segments.find((s) => s.role === "stop");
  const targets = trade.segments.filter((s) => s.role === "target");
  return (
    <div
      className="trade-tip"
      data-testid="trade-tooltip"
      data-trade={trade.tradeId}
      data-source={trade.source}
      role="tooltip"
      ref={(el) => {
        // Keep the card inside the chart: beside the pointer, flipped and then
        // clamped at the edges (a phone's chart is narrower than the card).
        if (!el) return;
        const parent = el.parentElement;
        const width = el.offsetWidth;
        const height = el.offsetHeight;
        const maxX = parent ? parent.clientWidth - width - 4 : x + 14;
        const maxY = parent ? parent.clientHeight - height - 4 : y + 14;
        const left = x + 14 + width > (parent?.clientWidth ?? Infinity) ? x - 14 - width : x + 14;
        const top = y + 14 + height > (parent?.clientHeight ?? Infinity) ? y - 14 - height : y + 14;
        el.style.setProperty("--tt-x", `${Math.max(4, Math.min(left, maxX))}px`);
        el.style.setProperty("--tt-y", `${Math.max(4, Math.min(top, maxY))}px`);
      }}
    >
      <div className="trade-tip__head">
        <span className="trade-tip__dir" data-direction={trade.direction ?? "unknown"}>
          {trade.direction === "short" ? "▼ SHORT" : trade.direction === "long" ? "▲ LONG" : "SIDE ?"}
        </span>
        <span className="chart-ink trade-tip__prov" data-ink={trade.provenance}>
          {PROVENANCE_LABEL[trade.provenance]}
        </span>
        <span className="muted trade-tip__id">{trade.tradeId}</span>
      </div>
      <dl className="trade-tip__grid">
        <dt>Entry</dt>
        <dd>
          {value(row ? row.entry_price : fills?.entry.price, priceDecimals)}
          <span className="muted"> · {entryIso ? formatBarTime(secs(entryIso)) : "—"}</span>
        </dd>
        <dt>Exit</dt>
        <dd>
          {value(row ? row.exit_price : fills?.exit.price, priceDecimals)}
          <span className="muted"> · {exitIso ? formatBarTime(secs(exitIso)) : "—"}</span>
        </dd>
        {row ? (
          <>
            <dt>Size</dt>
            <dd>{value(row.size, null)}</dd>
          </>
        ) : null}
        <dt>Exit reason</dt>
        <dd>{reason ? reason.replaceAll("_", " ") : <span className="muted">not recorded</span>}</dd>
        <dt>Net P&amp;L</dt>
        <dd className={pnl && pnl.value !== null ? signClass(pnl.value, pnl.unit) : ""}>
          {value(pnl, null, true)}
        </dd>
        <dt>Stop</dt>
        <dd>
          {stop ? (
            <span className="num">{formatNumber(stop.price, "", { decimals: priceDecimals })}</span>
          ) : (
            <span className="muted">not recorded</span>
          )}
          {row?.stop_moved_by ? (
            <span className="muted">
              {" "}
              · moved by {row.stop_moved_by} to {value(row.final_stop, priceDecimals)}
            </span>
          ) : null}
        </dd>
        <dt>Target{targets.length > 1 ? "s" : ""}</dt>
        <dd>
          {targets.length > 0 ? (
            <span className="num">
              {targets
                .map((t) => formatNumber(t.price, "", { decimals: priceDecimals }))
                .join(" · ")}
            </span>
          ) : (
            <span className="muted">not recorded</span>
          )}
        </dd>
      </dl>
      <p className="trade-tip__foot muted">
        {trade.source === "backtest" ? `Backtest run ${trade.origin}` : `Paper session ${trade.origin}`}
      </p>
    </div>
  );
}

