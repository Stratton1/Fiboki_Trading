"use client";

import Link from "next/link";
import { lossRows } from "@/lib/limits-core";
import { singleProvenanceOf } from "@/lib/provenance";
import { useApi } from "@/lib/query";
import { route } from "@/lib/routes";
import type { Envelope, LimitBoardView } from "@/lib/types";
import { AsyncBoundary } from "../AsyncBoundary";
import { ProvenanceChip } from "../ProvenanceChip";
import { LIMITS_PATH } from "../risk/LimitBoard";
import { LimitRowItem, LimitStateTag } from "../risk/LimitRow";

/**
 * COMMAND · how close the book is to its loss limits: the daily-loss and
 * drawdown rows of GET /api/trading/limits, drawn by the same LimitRowItem as
 * the Risk board, plus the board's closest limit when it is another one.
 * Every number, band and breach is the API's.
 */
export function LossLimits() {
  const limits = useApi<Envelope<LimitBoardView>>(LIMITS_PATH, { refreshMs: 10_000 });
  return (
    <AsyncBoundary state={limits} label="the loss limits" onRetry={limits.reload}>
      {(envelope) => {
        const board = envelope.data;
        const rows = lossRows(board);
        const closest = board.rows.find((row) => row.key === board.closest_key) ?? null;
        const shared = singleProvenanceOf(rows.flatMap((row) => (row.value ? [row.value.provenance] : [])));
        return (
          <div data-testid="command-limits">
            <ul className="limit-family__rows">
              {rows.map((row) => (
                <LimitRowItem key={row.key} row={row} bands={board.bands} signed={row.family === "loss"} compact />
              ))}
            </ul>
            {shared ? (
              <p className="muted command__closest" data-testid="command-limits-provenance">
                every figure <ProvenanceChip provenance={shared} />
              </p>
            ) : null}
            {closest && !rows.some((row) => row.key === closest.key) ? (
              <p className="command__closest" data-testid="command-closest">
                Closest of all: <strong>{closest.label}</strong>
                {closest.kind ? <span className="muted"> {closest.kind}</span> : null}{" "}
                <LimitStateTag state={closest.band} />{" "}
                <Link href={route.risk()}>board</Link>
              </p>
            ) : null}
          </div>
        );
      }}
    </AsyncBoundary>
  );
}
