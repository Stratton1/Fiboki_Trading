"use client";

import Link from "next/link";
import { useClock } from "@/lib/clock";
import { formatAge, formatTimestamp, parseUtc } from "@/lib/format";
import { singleProvenanceOf } from "@/lib/provenance";
import { useApi } from "@/lib/query";
import { route } from "@/lib/routes";
import type { BotState, FleetRowView, Page } from "@/lib/types";
import { AsyncBoundary } from "../AsyncBoundary";
import { FigureValue } from "../FigureValue";
import { ProvenanceChip } from "../ProvenanceChip";

/**
 * The fleet: one row per paper bot (session), from GET /api/trading/fleet.
 *
 * Everything a row says was decided by the API: the state (running, idle,
 * stale, halted, finished, failed) and its reason, the health, the last
 * gateway decision, the open positions and every P&L. Rows arrive worst state
 * first and are rendered in that order. The ages shown count from the API's
 * timestamps for display; nothing here judges them.
 *
 * Every row links its entities: the strategy's tearsheet, the chart of each
 * instrument, the bot's journal and its lifecycle entity.
 */

export const FLEET_PATH = "/api/trading/fleet";
const FLEET_REFRESH_MS = 15_000;

const STATE_GLYPH: Record<BotState, string> = {
  running: "●",
  idle: "◌",
  stale: "✕",
  halted: "■",
  finished: "✓",
  failed: "✕",
};

const STATE_TONE: Record<BotState, string> = {
  running: "ok",
  idle: "neutral",
  stale: "critical",
  halted: "warn",
  finished: "neutral",
  failed: "critical",
};

function ago(iso: string, now: number): string {
  const then = parseUtc(iso).getTime();
  return Number.isNaN(then) ? "unknown" : formatAge(Math.max(0, (now - then) / 1_000));
}

export function BotStateTag({ row }: { row: FleetRowView }) {
  return (
    <span
      className="bot-state"
      data-state={row.state}
      data-tone={STATE_TONE[row.state]}
      data-testid="bot-state"
      title={row.state_reason}
    >
      <span aria-hidden="true">{STATE_GLYPH[row.state]}</span>
      {row.state.toUpperCase()}
    </span>
  );
}

function LastSignal({ row, now, compact }: { row: FleetRowView; now: number; compact: boolean }) {
  const signal = row.last_signal;
  if (!signal) {
    return (
      <span className="muted" title={row.last_signal_absent ?? undefined} data-testid="bot-signal" data-state="absent">
        none recorded
      </span>
    );
  }
  return (
    <span
      className="bot-signal"
      data-testid="bot-signal"
      data-outcome={signal.outcome}
      title={`${formatTimestamp(signal.at)}: ${signal.kind} ${signal.outcome}: ${signal.reason}`}
    >
      <span className={`badge badge--${signal.outcome === "accepted" ? "ok" : "degraded"}`}>
        {signal.outcome === "accepted" ? "ACCEPTED" : "BLOCKED"}
      </span>{" "}
      <span className="num">{ago(signal.at, now)}</span>
      {compact ? null : <span className="bot-signal__reason"> {signal.reason}</span>}
    </span>
  );
}

function Row({
  row,
  now,
  compact,
  selected,
  chip,
}: {
  row: FleetRowView;
  now: number;
  compact: boolean;
  selected: boolean;
  chip: boolean;
}) {
  return (
    <tr
      data-testid="fleet-row"
      data-session={row.session_id}
      data-state={row.state}
      data-health={row.health}
      aria-selected={selected || undefined}
      className={selected ? "is-selected" : undefined}
      id={`bot-${row.session_id}`}
    >
      <td>
        <BotStateTag row={row} />
      </td>
      <th scope="row" className="fleet-bot" title={`${row.label}: ${row.kind} · ${row.asset_class}`}>
        <Link href={route.session(row.session_id)} className="fleet-bot__name" data-testid="fleet-bot-link">
          {row.label}
        </Link>
      </th>
      {compact ? null : (
        <td>
          <Link href={route.strategy(row.strategy_id)} data-testid="fleet-strategy-link" className="mono">
            {row.strategy_id}
          </Link>
        </td>
      )}
      <td className="fleet-instruments">
        {(compact ? row.instruments.slice(0, 1) : row.instruments).map((symbol) => (
          <Link
            key={symbol}
            href={route.instrument(symbol, { tf: row.timeframe })}
            className="mono"
            data-testid="fleet-chart-link"
          >
            {symbol}
          </Link>
        ))}
        {compact && row.instruments.length > 1 ? <span className="muted"> +{row.instruments.length - 1}</span> : null}
        {row.timeframe ? <span className="muted"> {row.timeframe}</span> : null}
      </td>
      <td>
        <LastSignal row={row} now={now} compact={compact} />
      </td>
      {compact ? null : (
        <td className="num">
          <FigureValue figure={row.open_positions} showChip={chip} />
        </td>
      )}
      <td className="num">
        <FigureValue figure={row.unrealised_pnl} colourSign showChip={chip} />
      </td>
      <td className="num">
        <FigureValue figure={row.day_pnl} colourSign showChip={chip} />
      </td>
      {compact ? null : (
        <>
          <td className="num" title={`${row.closed_trades.value ?? "unknown"} closed trade(s)`}>
            <FigureValue figure={row.realised_pnl} colourSign showChip={chip} />
          </td>
          <td>
            <span className={`badge badge--${row.health === "ok" ? "ok" : row.health === "warn" ? "degraded" : "down"}`} title={row.health_reason} data-testid="bot-health">
              {row.health.toUpperCase()}
            </span>
          </td>
          <td className="num muted" title={formatTimestamp(row.written_at)}>
            {ago(row.written_at, now)}
          </td>
          <td className="fleet-links">
            <Link href={route.journal({ strategy: row.strategy_id })} data-testid="fleet-journal-link">
              journal
            </Link>
            {row.content_hash ? (
              <Link href={route.candidate(row.content_hash)} data-testid="fleet-lifecycle-link">
                lifecycle
              </Link>
            ) : null}
          </td>
        </>
      )}
    </tr>
  );
}

export function FleetTable({
  compact = false,
  limit,
  selected,
}: {
  compact?: boolean;
  /** Rows to show (the API's worst-first order); the rest are counted. */
  limit?: number;
  selected?: string | null;
}) {
  const state = useApi<Page<FleetRowView>>(FLEET_PATH, { refreshMs: FLEET_REFRESH_MS });
  const now = useClock();
  return (
    <AsyncBoundary
      state={state}
      label="the fleet"
      onRetry={state.reload}
      isEmpty={(page) => page.items.length === 0}
      emptyTitle="No bots"
      emptyBody="The platform reports no paper session, so no bot has run. This is not an idle fleet."
    >
      {(page) => {
        const shared = singleProvenanceOf(page.items.map((row) => row.provenance));
        const rows = limit === undefined ? page.items : page.items.slice(0, limit);
        const hidden = page.items.length - rows.length;
        return (
          <div className="fleet-table" data-testid="fleet-table" data-compact={compact || undefined}>
            <div className="table-wrap">
              <table className="dense">
                <thead>
                  <tr>
                    <th scope="col">State</th>
                    <th scope="col">Bot</th>
                    {compact ? null : <th scope="col">Strategy</th>}
                    <th scope="col">Instrument</th>
                    <th scope="col">Last decision</th>
                    {compact ? null : (
                      <th scope="col" className="num">
                        Open
                      </th>
                    )}
                    <th scope="col" className="num">
                      Unrealised
                    </th>
                    <th scope="col" className="num">
                      Day P&amp;L
                    </th>
                    {compact ? null : (
                      <>
                        <th scope="col" className="num">
                          Realised
                        </th>
                        <th scope="col">Health</th>
                        <th scope="col" className="num">
                          Wrote
                        </th>
                        <th scope="col">Links</th>
                      </>
                    )}
                  </tr>
                </thead>
                <tbody>
                  {rows.map((row) => (
                    <Row
                      key={row.session_id}
                      row={row}
                      now={now}
                      compact={compact}
                      selected={selected === row.session_id}
                      chip={shared === null}
                    />
                  ))}
                </tbody>
              </table>
            </div>
            <p className="fleet-table__foot muted">
              {shared ? (
                <span data-testid="fleet-provenance">
                  every figure <ProvenanceChip provenance={shared} />
                </span>
              ) : null}
              {hidden > 0 ? (
                <Link href={route.fleet()} data-testid="fleet-more">
                  {hidden} more bot{hidden === 1 ? "" : "s"}
                </Link>
              ) : null}
            </p>
          </div>
        );
      }}
    </AsyncBoundary>
  );
}
