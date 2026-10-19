"use client";

import Link from "next/link";
import { formatTimestamp } from "@/lib/format";
import {
  AUDIT_CHAINS_PATH,
  CHAIN_TONE,
  sessionChainPath,
  type ChainIntegrityView,
  type ChainTailView,
} from "@/lib/ops";
import { useApi } from "@/lib/query";
import { route } from "@/lib/routes";
import type { Envelope, Page } from "@/lib/types";
import { AsyncBoundary } from "../AsyncBoundary";
import { FigureValue } from "../FigureValue";
import { SourceBadge } from "../primitives";

/**
 * The paper audit chain (Wave 3O): every order, fill, close, control and risk
 * decision the paper runtime made, hash-chained. The API walks each chain from
 * genesis (GET /api/trading/audit-chain) and says whether it verifies and, if
 * not, which row broke and why. This only renders that verdict.
 */

function ChainBadge({ status }: { status: string }) {
  return (
    <span className={`badge badge--${CHAIN_TONE[status] ?? "unknown"}`} data-testid="chain-status" data-status={status}>
      {status === "ok" ? "INTACT" : status.toUpperCase()}
    </span>
  );
}

function short(hash: string | null | undefined): string {
  return hash ? `${hash.slice(0, 12)}…` : "—";
}

/** System board panel: integrity of every bot's chain. */
export function AuditChainPanel() {
  const state = useApi<Page<ChainIntegrityView>>(AUDIT_CHAINS_PATH, { refreshMs: 30_000 });
  return (
    <section className="card board__group" data-testid="audit-chain-panel" aria-labelledby="audit-chain-title">
      <h2 className="card__title" id="audit-chain-title">
        Paper audit chain
      </h2>
      <AsyncBoundary
        state={state}
        label="the paper audit chains"
        onRetry={state.reload}
        isEmpty={(p) => p.items.length === 0}
        emptyTitle="No audit chain"
        emptyBody="No forward paper bot, so no chain to verify. This is not a verified chain."
      >
        {(page) => (
          <>
            <SourceBadge source={page.source} />
            <div className="table-wrap">
              <table className="dense">
                <thead>
                  <tr>
                    <th scope="col">Chain</th>
                    <th scope="col">Bot</th>
                    <th scope="col" className="num">
                      Rows
                    </th>
                    <th scope="col">Head</th>
                    <th scope="col">Last row (UTC)</th>
                    <th scope="col">Verdict</th>
                  </tr>
                </thead>
                <tbody>
                  {page.items.map((c) => (
                    <tr key={c.session_id} data-testid="audit-chain-row" data-status={c.status}>
                      <td>
                        <ChainBadge status={c.status} />
                      </td>
                      <th scope="row" className="mono">
                        <Link href={route.sessionDetail(c.session_id)}>{c.session_id}</Link>
                      </th>
                      <td className="num">
                        <FigureValue figure={c.rows} showChip={false} />
                      </td>
                      <td className="ops-chain" title={c.head_hash ?? undefined}>
                        {short(c.head_hash)}
                      </td>
                      <td className="num">{c.last_at ? formatTimestamp(c.last_at) : "—"}</td>
                      <td className="wrap" data-testid="audit-chain-reason">
                        {c.first_broken_seq !== null ? `row ${c.first_broken_seq}: ` : ""}
                        {c.reason}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </>
        )}
      </AsyncBoundary>
    </section>
  );
}

/** One bot's chain: verdict and the newest rows. */
export function SessionAuditChain({ sessionId }: { sessionId: string }) {
  const state = useApi<Envelope<ChainTailView>>(sessionChainPath(sessionId), { refreshMs: 30_000 });
  return (
    <AsyncBoundary state={state} label="the bot's audit chain" onRetry={state.reload}>
      {(env) => {
        const { integrity, rows } = env.data;
        return (
          <div data-testid="session-audit-chain" data-status={integrity.status}>
            <SourceBadge source={env.source} />
            <div className="ops-row">
              <ChainBadge status={integrity.status} />
              <span className="ops-meaning">
                <FigureValue figure={integrity.rows} showChip={false} /> row(s) · head{" "}
                <span className="ops-chain" title={integrity.head_hash ?? undefined}>
                  {short(integrity.head_hash)}
                </span>{" "}
                · verified {formatTimestamp(integrity.verified_at)}
              </span>
            </div>
            {integrity.status === "broken" ? (
              <p className="state state--error" data-testid="session-audit-broken">
                Row {integrity.first_broken_seq}: {integrity.reason}. Nothing after this row can be trusted as
                written.
              </p>
            ) : null}
            {rows.length === 0 ? (
              <p className="muted">No rows yet.</p>
            ) : (
              <div className="table-wrap">
                <table className="dense">
                  <thead>
                    <tr>
                      <th scope="col" className="num">
                        Seq
                      </th>
                      <th scope="col">At (UTC)</th>
                      <th scope="col">Kind</th>
                      <th scope="col">What</th>
                      <th scope="col">Hash</th>
                    </tr>
                  </thead>
                  <tbody>
                    {rows.map((r) => (
                      <tr key={r.seq} data-testid="audit-chain-entry" data-kind={r.kind}>
                        <td className="num">{r.seq}</td>
                        <td className="num">{formatTimestamp(r.at)}</td>
                        <td className="mono">{r.kind}</td>
                        <td className="wrap">{r.summary}</td>
                        <td className="ops-chain" title={`prev ${r.prev_hash}`}>
                          {short(r.hash)}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </div>
        );
      }}
    </AsyncBoundary>
  );
}
