"use client";

import { List } from "lucide-react";
import { useState } from "react";
import { formatTimestamp } from "@/lib/format";
import { useApi } from "@/lib/query";
import type { Page, PositionRow } from "@/lib/types";
import { AsyncBoundary } from "../AsyncBoundary";
import { FigureValue } from "../FigureValue";
import { ProvenanceChip } from "../ProvenanceChip";
import { CaveatList, SourceBadge, TableWrap } from "../primitives";
import { Button } from "../ui/Button";
import { Sheet } from "../ui/Sheet";

export const POSITIONS_PATH = "/api/trading/positions";

/**
 * The open book, in the shared Sheet, from GET /api/trading/positions (the
 * `positions` stream topic keeps it current while the drawer is open). Read
 * only: closing a position is not a Risk & Exposure action. The read starts
 * on first open, so an operator who never opens the drawer costs nothing.
 */
export function PositionsDrawer() {
  const [open, setOpen] = useState(false);
  const [used, setUsed] = useState(false);
  if (open && !used) setUsed(true);
  const positions = useApi<Page<PositionRow>>(used ? POSITIONS_PATH : null);
  const count = positions.status === "success" ? positions.data.items.length : null;
  return (
    <>
      <Button size="sm" onClick={() => setOpen(true)} data-testid="positions-open" aria-haspopup="dialog">
        <List size={13} aria-hidden="true" />
        Open positions{count === null ? "" : ` (${count})`}
      </Button>
      <Sheet open={open} onOpenChange={setOpen} title="Open positions" testId="positions-sheet">
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
              <CaveatList caveats={page.caveats} />
              <TableWrap>
                <table data-testid="positions-table">
                  <caption className="sr-only">Open positions</caption>
                  <thead>
                    <tr>
                      <th scope="col">Source</th>
                      <th scope="col">Instrument</th>
                      <th scope="col">Side</th>
                      <th scope="col" className="num">
                        Size
                      </th>
                      <th scope="col" className="num">
                        Entry
                      </th>
                      <th scope="col" className="num">
                        Mark
                      </th>
                      <th scope="col" className="num">
                        Stop
                      </th>
                      <th scope="col" className="num">
                        To stop
                      </th>
                      <th scope="col" className="num">
                        Unrealised
                      </th>
                      <th scope="col">Strategy</th>
                      <th scope="col">Opened (UTC)</th>
                    </tr>
                  </thead>
                  <tbody>
                    {page.items.map((row) => (
                      <tr key={row.position_id} data-testid="position-row" data-position-id={row.position_id}>
                        <td>
                          <ProvenanceChip provenance={row.provenance} />
                        </td>
                        <td>{row.instrument}</td>
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
                          <FigureValue figure={row.distance_to_stop_pct} showChip={false} />
                        </td>
                        <td>
                          <FigureValue figure={row.unrealised_pnl} showChip={false} colourSign />
                        </td>
                        <td>{row.strategy_id}</td>
                        <td>{formatTimestamp(row.entry_time)}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </TableWrap>
            </>
          )}
        </AsyncBoundary>
      </Sheet>
    </>
  );
}
