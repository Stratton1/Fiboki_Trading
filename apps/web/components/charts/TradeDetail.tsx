"use client";

import Link from "next/link";
import type { PlacedTrade } from "@/lib/chart-model";
import { formatFigure, formatNumber, signClass } from "@/lib/format";
import { route } from "@/lib/routes";
import type { Figure } from "@/lib/types";
import { formatBarTime, type TimeZoneId } from "./time";

/**
 * The selected trade's detail (Wave 2B): MAE and MFE (account currency and
 * in R), the R-multiple, the entry and exit reasons and every cost component,
 * all as the API served them (routers/chart.py: the stored values, R by the
 * research tear sheet's own definition). A figure the API could not supply
 * shows its reason, never 0. A paper trade shows what the journal records
 * (its fills, exit reason and net P&L) and links to the journal row.
 */

function Fig({ figure, signed = false, decimals, testId }: { figure: Figure; signed?: boolean; decimals?: number | null; testId?: string }) {
  const text = formatFigure(figure, { signed, decimals });
  if (text === null) {
    const why = figure.caveats[0]?.message;
    return (
      <span className="figure__missing" data-testid={testId} data-value="" title={why}>
        no data{why ? <span className="sr-only">: {why}</span> : null}
      </span>
    );
  }
  return (
    <span
      className={`num ${signed && figure.value !== null ? signClass(figure.value, figure.unit) : ""}`}
      data-testid={testId}
      data-value={figure.value ?? ""}
    >
      {text}
    </span>
  );
}

const secs = (iso: string) => Math.floor(Date.parse(iso) / 1_000);

export function TradeDetail({
  trade,
  priceDecimals,
  tz,
}: {
  trade: PlacedTrade;
  priceDecimals: number | null;
  tz: TimeZoneId;
}) {
  const px = (v: number | null) => (v === null ? "—" : formatNumber(v, "", { decimals: priceDecimals }));
  const row = trade.row;
  if (!row) {
    const fills = trade.fills;
    return (
      <section className="cw-td" aria-label="Selected trade" data-testid="trade-detail" data-source="paper" data-key={trade.key}>
        <h3 className="cw-dw__h">
          PAPER {trade.direction ?? "?"} · {trade.tradeId}
        </h3>
        <dl className="cw-dw__grid">
          <dt>Entry</dt>
          <dd>
            {px(trade.entryPrice)} · {fills ? formatBarTime(secs(fills.entry.t), tz) : "—"}
          </dd>
          <dt>Exit</dt>
          <dd>
            {px(trade.exitPrice)} · {fills ? formatBarTime(secs(fills.exit.t), tz) : "—"}
          </dd>
          <dt>Exit reason</dt>
          <dd data-testid="td-exit-reason">{fills?.exit.exit_reason?.replaceAll("_", " ") ?? "not recorded"}</dd>
          <dt>Net P&amp;L</dt>
          <dd>{fills?.exit.net_pnl ? <Fig figure={fills.exit.net_pnl} signed /> : <span className="figure__missing">no data</span>}</dd>
        </dl>
        <p className="muted cw-td__note" data-testid="td-paper-note">
          The paper journal&apos;s chart feed carries fills, the exit reason and net P&amp;L; MAE, MFE, R and the
          cost split are on the journal row.{" "}
          <Link href={route.trade(trade.tradeId)} prefetch={false}>
            Open in the journal
          </Link>
        </p>
      </section>
    );
  }
  return (
    <section className="cw-td" aria-label="Selected trade" data-testid="trade-detail" data-source="backtest" data-key={trade.key}>
      <h3 className="cw-dw__h">
        BT {row.direction} · {row.trade_id} · {row.strategy_id}
      </h3>
      <dl className="cw-dw__grid">
        <dt>Entry</dt>
        <dd>
          {px(row.entry_price.value)} · {formatBarTime(secs(row.entry_time), tz)}
        </dd>
        <dt>Entry reason</dt>
        <dd data-testid="td-entry-reason" title={row.entry_reason_detail}>
          {row.entry_reason ?? <span className="figure__missing">not recorded</span>}
        </dd>
        <dt>Exit</dt>
        <dd>
          {px(row.exit_price.value)} · {formatBarTime(secs(row.exit_time), tz)}
        </dd>
        <dt>Exit reason</dt>
        <dd data-testid="td-exit-reason">{row.exit_reason ? row.exit_reason.replaceAll("_", " ") : "—"}</dd>
        <dt>Net P&amp;L</dt>
        <dd>
          <Fig figure={row.net_pnl} signed testId="td-net" />
        </dd>
        <dt>R-multiple</dt>
        <dd>
          <Fig figure={row.r_multiple} signed decimals={2} testId="td-r" />
        </dd>
        <dt>MAE</dt>
        <dd>
          <Fig figure={row.mae} signed testId="td-mae" /> · <Fig figure={row.mae_r} signed decimals={2} />
        </dd>
        <dt>MFE</dt>
        <dd>
          <Fig figure={row.mfe} signed testId="td-mfe" /> · <Fig figure={row.mfe_r} signed decimals={2} />
        </dd>
        <dt>Gross P&amp;L</dt>
        <dd>
          <Fig figure={row.gross_pnl} signed />
        </dd>
        <dt>Spread</dt>
        <dd>
          <Fig figure={row.spread_cost} testId="td-spread" />
        </dd>
        <dt>Commission</dt>
        <dd>
          <Fig figure={row.commission} />
        </dd>
        <dt>Slippage</dt>
        <dd>
          <Fig figure={row.slippage_cost} />
        </dd>
        <dt>Financing</dt>
        <dd>
          <Fig figure={row.financing_cost} />
        </dd>
        <dt>Costs total</dt>
        <dd>
          <Fig figure={row.costs} testId="td-costs" />
        </dd>
        <dt>Stop (initial / final)</dt>
        <dd>
          {px(row.stop_loss.value)} / {px(row.final_stop.value)}
          {row.stop_moved_by ? ` (${row.stop_moved_by})` : ""}
        </dd>
        <dt>Targets</dt>
        <dd>{row.take_profit_targets.length ? row.take_profit_targets.map((t) => px(t.value)).join(", ") : "none planned"}</dd>
        <dt>Bars held</dt>
        <dd>
          <Fig figure={row.bars_held} />
        </dd>
      </dl>
      <p className="muted cw-td__note">{row.entry_reason ? "" : row.entry_reason_detail}</p>
    </section>
  );
}
