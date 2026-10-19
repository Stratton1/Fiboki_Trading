"use client";

import Link from "next/link";
import { useEffect, type ReactNode } from "react";
import { formatTimestamp, formatUtcTime } from "@/lib/format";
import { singleProvenanceOf } from "@/lib/provenance";
import { useApi } from "@/lib/query";
import type { BoardRow, BoardView, Envelope } from "@/lib/types";
import { AsyncBoundary } from "../AsyncBoundary";
import { FigureValue } from "../FigureValue";
import { PageHead, StatusBadge } from "../primitives";
import { ProvenanceChip } from "../ProvenanceChip";
import { AuditChainPanel } from "../ops/AuditChain";

/**
 * SYSTEM BOARD (F8): every service, worker, paper bot, broker, data store,
 * health check and the incident load on one page, from GET /api/system/board.
 *
 * Every row was read and aged by ONE API clock (`server_time`), and its
 * `stale` verdict is the platform's: before, the status bar and the Workers
 * page aged the same heartbeat by two browser clocks and two thresholds and
 * could disagree. Stale rows are dimmed and marked; the page-level banner in
 * the shell says when this tab's own copy of the board is old.
 *
 * The old pages (/system/services, /system/workers, /system/broker-health,
 * /system/data-health) open this board at their group.
 */

export const BOARD_PATH = "/api/system/board";
const BOARD_REFRESH_MS = 10_000;

export type BoardFocus = "services" | "workers" | "broker" | "data" | null;

const GROUPS: { id: BoardRow["group"]; title: string; focus: BoardFocus }[] = [
  { id: "worker", title: "Workers", focus: "workers" },
  { id: "bot", title: "Paper bots", focus: null },
  { id: "service", title: "Services", focus: "services" },
  { id: "data", title: "Data stores", focus: "data" },
  { id: "broker", title: "Broker", focus: "broker" },
  { id: "alerts", title: "Alerts and incidents", focus: null },
  { id: "check", title: "Health checks", focus: null },
];

function Row({ row }: { row: BoardRow }) {
  const internal = row.deep_link.startsWith("/") && !row.deep_link.startsWith("//");
  return (
    <tr data-testid="board-row" data-id={row.id} data-status={row.status} data-stale={row.stale || undefined}>
      <td>
        <StatusBadge status={row.status} />
      </td>
      <th scope="row" className="mono">
        {internal ? <Link href={row.deep_link}>{row.name}</Link> : row.name}
        {row.stale ? (
          <span className="badge badge--down board__stale" data-testid="board-row-stale" title="The platform judges this component's information out of date.">
            STALE
          </span>
        ) : null}
      </th>
      <td className="num" data-testid="board-age">
        <FigureValue figure={row.age} showChip={false} missingLabel="never" />
      </td>
      <td className="num muted">
        <FigureValue figure={row.stale_after} showChip={false} missingLabel="—" />
      </td>
      <td className="wrap board__detail">{row.detail}</td>
    </tr>
  );
}

function Group({ title, rows, id, focused }: { title: string; rows: BoardRow[]; id: string; focused: boolean }) {
  if (rows.length === 0) return null;
  return (
    <section
      className="card board__group"
      id={`board-${id}`}
      data-testid={`board-group-${id}`}
      data-focused={focused || undefined}
      aria-labelledby={`board-title-${id}`}
    >
      <h2 className="card__title" id={`board-title-${id}`}>
        {title} <span className="muted">{rows.length}</span>
      </h2>
      <div className="table-wrap">
        <table className="dense board__table">
          <thead>
            <tr>
              <th scope="col">Status</th>
              <th scope="col">Component</th>
              <th scope="col" className="num">
                Age
              </th>
              <th scope="col" className="num">
                Stale after
              </th>
              <th scope="col">Detail</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((row) => (
              <Row key={row.id} row={row} />
            ))}
          </tbody>
        </table>
      </div>
    </section>
  );
}

function Board({
  envelope,
  focus,
  scroll,
}: {
  envelope: Envelope<BoardView>;
  focus: BoardFocus;
  scroll: boolean;
}) {
  const board = envelope.data;
  const provenance = singleProvenanceOf(board.rows.map((r) => r.age.provenance));
  useEffect(() => {
    if (!focus || !scroll) return;
    const group = GROUPS.find((g) => g.focus === focus);
    if (group) document.getElementById(`board-${group.id}`)?.scrollIntoView({ block: "start" });
  }, [focus, scroll]);
  return (
    <>
      <div className="board__summary" data-testid="board-summary" data-status={board.status}>
        <StatusBadge status={board.status} />
        <span className="num" data-testid="board-clock" title={formatTimestamp(board.server_time)}>
          API clock {formatUtcTime(board.server_time)}
        </span>
        {(["down", "degraded", "absent", "ok"] as const).map((status) => {
          const figure = board.counts[status];
          return figure ? (
            <span key={status} className="board__count" data-status={status}>
              <FigureValue figure={figure} showChip={false} /> {status}
            </span>
          ) : null;
        })}
        <span className="board__count" data-status={board.stale.value ? "down" : "ok"} data-testid="board-stale-count">
          <FigureValue figure={board.stale} showChip={false} /> stale
        </span>
        {provenance ? <ProvenanceChip provenance={provenance} /> : null}
      </div>
      {board.advisory ? (
        <p className="state state--error board__advisory" data-testid="board-advisory">
          {board.advisory}
        </p>
      ) : null}
      <div className="board__grid">
        {GROUPS.map((group) => (
          <Group
            key={group.id}
            id={group.id}
            title={group.title}
            rows={board.rows.filter((r) => r.group === group.id)}
            focused={focus !== null && group.focus === focus}
          />
        ))}
        {/* wave ops: the paper runtime's hash-chained audit log, verified by the API */}
        <AuditChainPanel />
      </div>
    </>
  );
}

export function SystemBoard({ focus = null, before }: { focus?: BoardFocus; before?: ReactNode }) {
  const state = useApi<Envelope<BoardView>>(BOARD_PATH, { refreshMs: BOARD_REFRESH_MS });
  return (
    <div className="board" data-testid="system-board" data-focus={focus ?? undefined}>
      <PageHead
        title="System"
        intro="Every service, worker, paper bot, data store, broker and health check, and the incident load, read once and aged by one API clock. STALE is the platform's verdict, not this browser's. The old Services, Workers, Broker and Data Health pages open this board at their group."
      />
      {before}
      <AsyncBoundary state={state} label="the system board" onRetry={state.reload}>
        {/* A page that leads with its own panel (before) is not scrolled away from it. */}
        {(envelope) => <Board envelope={envelope} focus={focus} scroll={before === undefined} />}
      </AsyncBoundary>
    </div>
  );
}
