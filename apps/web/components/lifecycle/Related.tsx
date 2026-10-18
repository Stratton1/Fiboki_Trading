"use client";

import Link from "next/link";
import { formatTimestamp } from "@/lib/format";
import { singleProvenanceOf } from "@/lib/provenance";
import { useApi } from "@/lib/query";
import { route } from "@/lib/routes";
import type { FleetRowView, Page, TradeRow } from "@/lib/types";
import { AsyncBoundary } from "../AsyncBoundary";
import { BotStateTag, FLEET_PATH } from "../fleet/FleetTable";
import { FigureValue } from "../FigureValue";
import { ProvenanceChip } from "../ProvenanceChip";

/**
 * LIFECYCLE · everything related to one strategy (programme P2 18): its
 * tearsheet and backtest runs (the research screens, /research/strategies/<id>),
 * the paper bots running it, its latest trades (each linking to the chart at
 * that trade) and the charts of what it trades. The equity curve and the
 * runs live on the tearsheet; nothing here sums or recomputes P&L.
 */
export function LifecycleRelated({ strategyId }: { strategyId: string }) {
  const trades = useApi<Page<TradeRow>>(
    `/api/trading/trades?strategy_id=${encodeURIComponent(strategyId)}&limit=12`,
  );
  const fleet = useApi<Page<FleetRowView>>(FLEET_PATH, { refreshMs: 15_000 });
  return (
    <section className="card" aria-labelledby="lifecycle-related" data-testid="lifecycle-related">
      <h2 id="lifecycle-related" className="card__title">
        Related
      </h2>
      <p className="entity-links" data-testid="lifecycle-related-links">
        <Link href={route.strategy(strategyId)} data-testid="lifecycle-tearsheet-link">
          Tearsheet, equity and runs
        </Link>
        <Link href={route.journal({ strategy: strategyId })} data-testid="lifecycle-journal-link">
          Every trade in the journal
        </Link>
        <Link href={route.fleet()}>Fleet</Link>
      </p>
      <div className="related-grid">
        <div>
          <h3 className="attr__title">Paper bots</h3>
          <AsyncBoundary state={fleet} label="the fleet" onRetry={fleet.reload}>
            {(page) => {
              const bots = page.items.filter((row) => row.strategy_id === strategyId);
              if (bots.length === 0) {
                return (
                  <p className="muted" data-testid="lifecycle-no-bots">
                    No paper session runs {strategyId}.
                  </p>
                );
              }
              return (
                <ul className="related-list">
                  {bots.map((bot) => (
                    <li key={bot.session_id} data-testid="lifecycle-bot">
                      <BotStateTag row={bot} /> <Link href={route.session(bot.session_id)}>{bot.label}</Link>{" "}
                      {bot.instruments.map((s) => (
                        <Link key={s} href={route.instrument(s, { tf: bot.timeframe })} className="mono">
                          {s}
                        </Link>
                      ))}
                    </li>
                  ))}
                </ul>
              );
            }}
          </AsyncBoundary>
        </div>
        <div>
          <h3 className="attr__title">Latest trades</h3>
          <AsyncBoundary
            state={trades}
            label="the strategy's trades"
            onRetry={trades.reload}
            isEmpty={(page) => page.items.length === 0}
            emptyTitle="No trades"
            emptyBody="The trade record holds no trade for this strategy."
          >
            {(page) => {
              const shared = singleProvenanceOf(page.items.map((t) => t.provenance));
              return (
                <div className="table-wrap">
                  <table className="dense" data-testid="lifecycle-trades">
                    <thead>
                      <tr>
                        <th scope="col">Closed</th>
                        <th scope="col">Instrument</th>
                        <th scope="col">Side</th>
                        <th scope="col" className="num">
                          Net {shared ? <ProvenanceChip provenance={shared} /> : null}
                        </th>
                      </tr>
                    </thead>
                    <tbody>
                      {page.items.map((t) => (
                        <tr key={t.trade_id} data-testid="lifecycle-trade">
                          <td>
                            <Link href={route.trade(t.trade_id)}>{formatTimestamp(t.exit_time)}</Link>
                          </td>
                          <td>
                            <Link href={route.instrument(t.instrument, { trade: t.trade_id })} className="mono">
                              {t.instrument}
                            </Link>
                          </td>
                          <td>{t.direction.toUpperCase()}</td>
                          <td className="num">
                            <FigureValue figure={t.net_pnl} colourSign showChip={shared === null} />
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              );
            }}
          </AsyncBoundary>
        </div>
      </div>
    </section>
  );
}
