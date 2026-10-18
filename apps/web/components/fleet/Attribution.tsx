"use client";

import Link from "next/link";
import { singleProvenanceOf } from "@/lib/provenance";
import { useApi } from "@/lib/query";
import type { AttributionRow, AttributionView, Envelope } from "@/lib/types";
import { AsyncBoundary } from "../AsyncBoundary";
import { CaveatList } from "../CaveatList";
import { FigureValue } from "../FigureValue";
import { ProvenanceChip } from "../ProvenanceChip";

/**
 * P&L attribution: the executing mode's net P&L by strategy and by
 * instrument (GET /api/trading/attribution). Every sum, share and win rate is
 * the API's; rows arrive largest absolute net first.
 */

export const ATTRIBUTION_PATH = "/api/trading/attribution";

function Table({ title, rows, testId, chip }: { title: string; rows: AttributionRow[]; testId: string; chip: boolean }) {
  return (
    <section className="attr" data-testid={testId} aria-label={title}>
      <h3 className="attr__title">{title}</h3>
      <div className="table-wrap">
        <table className="dense">
          <thead>
            <tr>
              <th scope="col">{title.replace("By ", "")}</th>
              <th scope="col" className="num">
                Trades
              </th>
              <th scope="col" className="num">
                Win %
              </th>
              <th scope="col" className="num">
                Net P&amp;L
              </th>
              <th scope="col" className="num">
                Share
              </th>
              <th scope="col" className="num">
                Day
              </th>
              <th scope="col" className="num">
                Unrealised
              </th>
            </tr>
          </thead>
          <tbody>
            {rows.map((row) => (
              <tr key={row.key} data-testid="attr-row" data-key={row.key}>
                <th scope="row">
                  <Link prefetch={false} href={row.chart ?? row.link} className="mono" data-testid="attr-link">
                    {row.label}
                  </Link>
                </th>
                <td className="num">
                  <FigureValue figure={row.trades} showChip={chip} />
                </td>
                <td className="num">
                  <FigureValue figure={row.win_rate} showChip={chip} />
                </td>
                <td className="num">
                  <FigureValue figure={row.net_pnl} colourSign showChip={chip} />
                </td>
                <td className="num">
                  <FigureValue figure={row.share_of_abs_pct} showChip={false} />
                </td>
                <td className="num">
                  <FigureValue figure={row.day_pnl} colourSign showChip={chip} />
                </td>
                <td className="num">
                  <FigureValue figure={row.unrealised_pnl} colourSign showChip={chip} />
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </section>
  );
}

export function Attribution() {
  const state = useApi<Envelope<AttributionView>>(ATTRIBUTION_PATH, { refreshMs: 30_000 });
  return (
    <AsyncBoundary
      state={state}
      label="P&L attribution"
      onRetry={state.reload}
      isEmpty={(envelope) => envelope.data.by_strategy.length === 0}
      emptyTitle="Nothing to attribute"
      emptyBody="The executing mode has no closed trade and no open position, so there is no P&L to attribute."
    >
      {(envelope) => {
        const view = envelope.data;
        const shared = singleProvenanceOf([view.provenance, view.total_net_pnl.provenance]);
        return (
          <div className="attr-grid">
            <p className="attr__total">
              Total net <FigureValue figure={view.total_net_pnl} colourSign showChip={false} />{" "}
              {shared ? <ProvenanceChip provenance={shared} /> : null}
              <span className="muted"> {envelope.source.detail}</span>
            </p>
            <CaveatList caveats={envelope.caveats} />
            <Table title="By strategy" rows={view.by_strategy} testId="attr-strategy" chip={false} />
            <Table title="By instrument" rows={view.by_instrument} testId="attr-instrument" chip={false} />
          </div>
        );
      }}
    </AsyncBoundary>
  );
}
