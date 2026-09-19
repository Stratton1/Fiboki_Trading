"use client";

import { useState } from "react";
import { ApiError, apiFetch, useApi } from "@/lib/api";
import { AsyncBoundary } from "@/components/AsyncBoundary";
import { ConfirmDialog } from "@/components/ConfirmDialog";
import { FigureValue } from "@/components/FigureValue";
import {
  CaveatList,
  PageHead,
  SourceBadge,
  TableWrap,
} from "@/components/primitives";
import type {
  CandidateRow,
  Envelope,
  ExecutionModeBanner,
  Page,
} from "@/lib/types";

/**
 * TRADING · Candidates.
 *
 * The ONE surface that answers "what should I promote?", and the ONE promotion
 * action. V1 had four competing surfaces for the question and six different
 * routes to approve a bot, each with different copy and different gating — so
 * whether a promotion was allowed depended on which button you found first.
 */
export default function CandidatesPage() {
  const state = useApi<Page<CandidateRow>>("/api/trading/candidates");
  const mode = useApi<Envelope<ExecutionModeBanner>>("/api/system/execution-mode");
  const [target, setTarget] = useState<CandidateRow | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const executionMode = mode.status === "success" ? mode.data.data.mode : "unknown";

  async function promote(choiceId: string, reason: string) {
    if (!target) return;
    setBusy(true);
    setError(null);
    try {
      await apiFetch(`/api/trading/candidates/${target.strategy_id}/promote`, {
        method: "POST",
        body: JSON.stringify({
          target_lifecycle: choiceId,
          reason,
          acknowledge_caveats: true,
        }),
      });
      setTarget(null);
      state.reload();
    } catch (err) {
      setError(
        err instanceof ApiError ? `${err.message} (${err.code})` : "Promotion failed.",
      );
    } finally {
      setBusy(false);
    }
  }

  return (
    <>
      <PageHead
        title="Candidates"
        intro="Ranked on out-of-sample, holdout and walk-forward evidence only. In-sample backtest trades are excluded from every figure on this page."
      />
      <AsyncBoundary
        state={state}
        label="candidates"
        onRetry={state.reload}
        isEmpty={(page) => page.items.length === 0}
        emptyTitle="No candidates"
        emptyBody="No strategy is currently registered as a promotion candidate."
      >
        {(page) => (
          <>
            <SourceBadge source={page.source} />
            <CaveatList caveats={page.caveats} />
            <TableWrap>
              <table>
                <thead>
                  <tr>
                    <th>Strategy</th>
                    <th>Family</th>
                    <th>Trades</th>
                    <th>Win rate</th>
                    <th>Expectancy</th>
                    <th>Net P&L</th>
                    <th>Sharpe</th>
                    <th>Max DD</th>
                    <th>Eligible</th>
                    <th>Action</th>
                  </tr>
                </thead>
                <tbody>
                  {page.items.map((row) => (
                    <tr key={row.strategy_id} data-testid="candidate-row">
                      <td>
                        <strong>{row.name}</strong>
                        <br />
                        <span className="mono muted">{row.strategy_id}</span>
                      </td>
                      <td>{row.family}</td>
                      <td>
                        <FigureValue figure={row.trades} />
                      </td>
                      <td>
                        <FigureValue figure={row.win_rate} />
                      </td>
                      <td>
                        <FigureValue figure={row.expectancy_r} colourSign />
                      </td>
                      <td>
                        <FigureValue figure={row.net_pnl} colourSign />
                      </td>
                      <td>
                        <FigureValue figure={row.sharpe} />
                      </td>
                      <td>
                        <FigureValue figure={row.max_drawdown_pct} />
                      </td>
                      <td>
                        <span
                          className={`badge badge--${row.eligible_for_ranking ? "ok" : "degraded"}`}
                          title={row.blocking_reasons.join("\n")}
                        >
                          {row.eligible_for_ranking ? "YES" : "NO"}
                        </span>
                      </td>
                      <td>
                        <button
                          type="button"
                          data-testid={`promote-${row.strategy_id}`}
                          disabled={!row.eligible_for_ranking}
                          title={
                            row.eligible_for_ranking
                              ? `Requires the ${row.next_action_requires_role} role.`
                              : row.blocking_reasons.join(" ")
                          }
                          onClick={() => {
                            setError(null);
                            setTarget(row);
                          }}
                        >
                          Promote
                        </button>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </TableWrap>

            {page.items.some((row) => row.blocking_reasons.length > 0) ? (
              <div className="card">
                <h2 className="card__title">Why these are not promotable</h2>
                <ul className="muted">
                  {page.items.flatMap((row) =>
                    row.blocking_reasons.map((reason) => (
                      <li key={`${row.strategy_id}:${reason}`}>
                        <span className="mono">{row.strategy_id}</span> — {reason}
                      </li>
                    )),
                  )}
                </ul>
              </div>
            ) : null}
          </>
        )}
      </AsyncBoundary>

      <ConfirmDialog
        open={target !== null}
        title={`Promote ${target?.name ?? ""}`}
        executionMode={executionMode}
        busy={busy}
        errorMessage={error}
        confirmLabel="Promote"
        onCancel={() => setTarget(null)}
        onConfirm={promote}
        choices={[
          {
            id: "paper",
            title: "TO PAPER",
            body: "Run it in the paper engine against recorded executable prices.",
            consequences: [
              "The strategy begins consuming risk budget in the paper book.",
              "Its figures will start carrying the PAPER provenance, not OOS.",
              `Realism caveats attached to its figures still apply: ${
                target?.net_pnl.caveats.map((c) => c.code).join(", ") || "none"
              }.`,
              "This is recorded against your name in the audit trail.",
              "It does NOT reach a broker. Demo and live are not options here.",
            ],
          },
          {
            id: "shadow",
            title: "TO SHADOW",
            body: "Mirror it against live pricing without submitting any order.",
            consequences: [
              "Signals are evaluated against the venue's real pricing.",
              "No order is submitted and no position is opened.",
              "Backtest-versus-live divergence starts being recorded.",
              "This is recorded against your name in the audit trail.",
            ],
          },
        ]}
      />
    </>
  );
}
