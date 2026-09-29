"use client";

import { useEffect, useState } from "react";
import { ApiError, apiFetch } from "@/lib/api";
import { capability } from "@/lib/auth";
import { useApi } from "@/lib/query";
import { AsyncBoundary } from "@/components/AsyncBoundary";
import { useExecutionMode, useOperator } from "@/components/shell/platform";
import { Button } from "@/components/ui/Button";
import {
  ConfirmDialog,
  type Acknowledgement,
  type ConfirmChoice,
} from "@/components/ui/ConfirmDialog";
import { FigureValue } from "@/components/FigureValue";
import { CaveatPopover } from "@/components/CaveatPopover";
import { DataGrid, preloadGrid, type GridColumn } from "@/components/grid";
import { CaveatList, PageHead, SourceBadge } from "@/components/primitives";
import type {
  CandidateRow,
  Envelope,
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
 *
 * Wave 3: the list is a DataGrid. Why a candidate is not eligible is behind a
 * focusable "why" popover, not a `title` (report G W-15), and Promote is
 * `aria-disabled` rather than `disabled`, so it stays focusable and names the
 * reason it cannot be pressed.
 */
const CANDIDATES_PATH = "/api/trading/candidates";

export default function CandidatesPage() {
  const state = useApi<Page<CandidateRow>>(CANDIDATES_PATH);
  useEffect(() => {
    void preloadGrid();
  }, []);
  const { mode: executionMode, mutationsAllowed } = useExecutionMode();
  const allowed = capability(useOperator(), "can_promote");
  const [target, setTarget] = useState<CandidateRow | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const preflight = useApi<Envelope<PromotePreflightView>>(
    target
      ? `/api/trading/candidates/${encodeURIComponent(target.strategy_id)}/promote/preflight`
      : null,
  );

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

  const blockedReason = (row: CandidateRow): string | null =>
    !mutationsAllowed
      ? "The execution mode is unknown or the workstation is disconnected; nothing can be promoted until it can be read."
      : !allowed.allowed
        ? (allowed.reason ?? "Your role cannot promote.")
        : !row.eligible_for_ranking
          ? `Not eligible: ${row.blocking_reasons.join(" ")}`
          : null;

  const columns: GridColumn<CandidateRow>[] = [
    {
      id: "strategy",
      header: "Strategy",
      value: (row) => `${row.name} ${row.strategy_id}`,
      cell: (row) => (
        <span className="grid__stack">
          <strong>{row.name}</strong>
          <span className="mono muted">{row.strategy_id}</span>
        </span>
      ),
      width: 220,
      pin: true,
    },
    { id: "family", header: "Family", value: (row) => row.family, width: 96 },
    { id: "trades", header: "Trades", figure: (row) => row.trades, chip: true },
    { id: "win_rate", header: "Win rate", figure: (row) => row.win_rate, chip: true },
    {
      id: "expectancy",
      header: "Expectancy",
      figure: (row) => row.expectancy_r,
      signed: true,
      chip: true,
    },
    { id: "net", header: "Net P&L", figure: (row) => row.net_pnl, signed: true, chip: true, width: 170 },
    { id: "sharpe", header: "Sharpe", figure: (row) => row.sharpe, chip: true },
    { id: "max_dd", header: "Max DD", figure: (row) => row.max_drawdown_pct, chip: true },
    {
      id: "eligible",
      header: "Eligible",
      value: (row) => (row.eligible_for_ranking ? "yes" : "no"),
      cell: (row) => (
        <span className="row gap-1">
          <span
            className={`badge badge--${row.eligible_for_ranking ? "ok" : "degraded"}`}
            data-testid="candidate-eligible"
          >
            {row.eligible_for_ranking ? "YES" : "NO"}
          </span>
          <CaveatPopover
            reasons={row.blocking_reasons}
            title={`Why ${row.name} is not eligible`}
            label="why"
            accessibleName={`Why ${row.name} is not eligible for ranking`}
            testId={`candidate-why-${row.strategy_id}`}
          />
        </span>
      ),
      width: 120,
    },
    {
      id: "action",
      header: "Action",
      sortable: false,
      filterable: false,
      csv: false,
      value: () => null,
      cell: (row) => {
        const reason = blockedReason(row);
        return (
          <Button
            size="sm"
            data-testid={`promote-${row.strategy_id}`}
            aria-disabled={reason !== null}
            aria-description={reason ?? `Requires the ${row.next_action_requires_role} role.`}
            onClick={() => {
              if (reason !== null) return;
              setError(null);
              setTarget(row);
            }}
          >
            Promote
          </Button>
        );
      },
      width: 104,
    },
  ];

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
            {!allowed.allowed ? (
              <p className="muted" data-testid="promote-role-blocked" role="note">
                Promotion is disabled. {allowed.reason}
              </p>
            ) : null}
            {!mutationsAllowed ? (
              <p className="muted" data-testid="promote-mode-blocked" role="note">
                Promotion is disabled: the execution mode is unknown or the workstation is
                disconnected, so nothing can be promoted until the platform answers.
              </p>
            ) : null}
            <DataGrid<CandidateRow>
              id="candidates"
              label="candidates"
              rows={page.items}
              columns={columns}
              rowKey={(row) => row.strategy_id}
              source={{ source: page.source, path: CANDIDATES_PATH }}
              rowTestId="candidate-row"
            />

            {page.items.some((row) => row.blocking_reasons.length > 0) ? (
              <div className="card">
                <h2 className="card__title">Why these are not promotable</h2>
                <ul className="muted">
                  {page.items.flatMap((row) =>
                    row.blocking_reasons.map((reason) => (
                      <li key={`${row.strategy_id}:${reason}`}>
                        <span className="mono">{row.strategy_id}</span>: {reason}
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
