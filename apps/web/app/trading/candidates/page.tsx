"use client";

import { useState } from "react";
import { ApiError, apiFetch, useApi } from "@/lib/api";
import { AsyncBoundary } from "@/components/AsyncBoundary";
import {
  ConfirmDialog,
  type Acknowledgement,
  type ConfirmChoice,
} from "@/components/ConfirmDialog";
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
  PromotePreflightView,
} from "@/lib/types";

/**
 * TRADING · Candidates.
 *
 * The ONE surface that answers "what should I promote?", and the ONE promotion
 * action. V1 had four competing surfaces for the question and six different
 * routes to approve a bot, each with different copy and different gating — so
 * whether a promotion was allowed depended on which button you found first.
 *
 * The dialog's content is server-computed: GET .../promote/preflight returns
 * the consequences of each target and the realism caveats on the candidate's
 * figures. Each caveat is a required checkbox, and `acknowledge_caveats` is
 * sent true only when every one was ticked, with the ticked codes alongside.
 * The page used to send `acknowledge_caveats: true` unconditionally, signing
 * the acknowledgement on the operator's behalf.
 */
export default function CandidatesPage() {
  const state = useApi<Page<CandidateRow>>("/api/trading/candidates");
  const mode = useApi<Envelope<ExecutionModeBanner>>("/api/system/execution-mode");
  const [target, setTarget] = useState<CandidateRow | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const preflight = useApi<Envelope<PromotePreflightView>>(
    target
      ? `/api/trading/candidates/${encodeURIComponent(target.strategy_id)}/promote/preflight`
      : null,
  );

  const executionMode = mode.status === "success" ? mode.data.data.mode : "unknown";

  const view = preflight.status === "success" ? preflight.data.data : null;
  const promotable = view !== null && view.eligible;
  const choices: ConfirmChoice[] = promotable
    ? Object.entries(view.consequences).map(([id, consequences]) => ({
        id,
        title: `TO ${id.toUpperCase()}`,
        body: `Promote to the ${id} lifecycle. The consequences below are computed by the platform.`,
        consequences,
      }))
    : [];
  const acknowledgements: Acknowledgement[] = promotable
    ? view.caveats.map((caveat) => ({ code: caveat.code, text: caveat.message }))
    : [];
  const notice =
    preflight.status === "loading"
      ? "Loading the consequences and caveats for this promotion from the platform."
      : view !== null && !view.eligible
        ? `This candidate cannot be promoted: ${view.blocking_reasons.join(" ")}`
        : null;
  const preflightError =
    preflight.status === "error"
      ? `Could not load what this promotion would do: ${preflight.error.message} (${preflight.error.code}). Nothing can be confirmed without it.`
      : null;

  async function promote(choiceId: string, reason: string, acknowledged: string[]) {
    if (!target || view === null) return;
    const required = view.caveats.map((c) => c.code);
    const allAcknowledged = required.every((code) => acknowledged.includes(code));
    setBusy(true);
    setError(null);
    try {
      await apiFetch(`/api/trading/candidates/${encodeURIComponent(target.strategy_id)}/promote`, {
        method: "POST",
        body: JSON.stringify({
          target_lifecycle: choiceId,
          reason,
          acknowledge_caveats: allAcknowledged,
          acknowledged_caveats: acknowledged,
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
        errorMessage={error ?? preflightError}
        notice={notice}
        confirmLabel="Promote"
        onCancel={() => setTarget(null)}
        onConfirm={promote}
        choices={choices}
        acknowledgements={acknowledgements}
      />
    </>
  );
}
