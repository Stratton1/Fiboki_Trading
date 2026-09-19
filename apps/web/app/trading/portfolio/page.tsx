"use client";

import { useApi } from "@/lib/api";
import { AsyncBoundary } from "@/components/AsyncBoundary";
import { LineChart } from "@/components/charts";
import { FigureValue } from "@/components/FigureValue";
import { ProvenanceChip } from "@/components/ProvenanceChip";
import {
  CaveatList,
  Card,
  PageHead,
  SourceBadge,
  TableWrap,
  Tile,
} from "@/components/primitives";
import { formatTimestamp } from "@/lib/format";
import type { Envelope, Page, PortfolioView, PositionRow } from "@/lib/types";

/** TRADING · Portfolio: the book, and only the book, for the executing mode. */
export default function PortfolioPage() {
  const portfolio = useApi<Envelope<PortfolioView>>("/api/trading/portfolio");
  const positions = useApi<Page<PositionRow>>("/api/trading/positions");

  return (
    <>
      <PageHead
        title="Portfolio"
        intro="Balance and equity count only trades executed in the current mode. Trades from other provenances are on the Execution page and are excluded here on purpose."
      />

      <AsyncBoundary state={portfolio} label="the portfolio" onRetry={portfolio.reload}>
        {(envelope) => (
          <>
            <SourceBadge source={envelope.source} />
            <div className="tiles">
              <Tile label="Balance" figure={envelope.data.balance} />
              <Tile label="Equity" figure={envelope.data.equity} />
              <Tile label="Realised P&L" figure={envelope.data.realised_pnl} colourSign />
              <Tile label="Unrealised P&L" figure={envelope.data.unrealised_pnl} colourSign />
              <Tile label="Max drawdown" figure={envelope.data.max_drawdown_pct} />
            </div>
            <Card>
              <LineChart series={envelope.data.equity_curve} />
              <CaveatList caveats={envelope.data.equity_curve.caveats} />
            </Card>
          </>
        )}
      </AsyncBoundary>

      <Card title="Open positions">
        <AsyncBoundary
          state={positions}
          label="open positions"
          onRetry={positions.reload}
          isEmpty={(page) => page.items.length === 0}
          emptyTitle="No open positions"
          emptyBody="The platform answered successfully with an empty book. Nothing is open right now."
        >
          {(page) => (
            <>
              <SourceBadge source={page.source} />
              <TableWrap>
                <table>
                  <thead>
                    <tr>
                      <th>Source</th>
                      <th>Instrument</th>
                      <th>Strategy</th>
                      <th>Side</th>
                      <th>Size</th>
                      <th>Entry</th>
                      <th>Mark</th>
                      <th>Stop</th>
                      <th>Unrealised</th>
                      <th>Opened</th>
                    </tr>
                  </thead>
                  <tbody>
                    {page.items.map((row) => (
                      <tr key={row.position_id}>
                        <td>
                          <ProvenanceChip provenance={row.provenance} />
                        </td>
                        <td>{row.instrument}</td>
                        <td>{row.strategy_id}</td>
                        <td>{row.direction.toUpperCase()}</td>
                        <td>
                          <FigureValue figure={row.size} showChip={false} />
                        </td>
                        <td>
                          <FigureValue figure={row.entry_price} showChip={false} />
                        </td>
                        <td>
                          <FigureValue figure={row.mark_price} showChip={false} />
                        </td>
                        <td>
                          <FigureValue figure={row.stop_loss} showChip={false} />
                        </td>
                        <td>
                          <FigureValue figure={row.unrealised_pnl} showChip={false} colourSign />
                        </td>
                        <td>{formatTimestamp(row.entry_time)}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </TableWrap>
            </>
          )}
        </AsyncBoundary>
      </Card>
    </>
  );
}
