"use client";

import { useApi } from "@/lib/api";
import { AsyncBoundary } from "@/components/AsyncBoundary";
import { KillSwitchPanel } from "@/components/KillSwitch";
import { LineChart } from "@/components/charts";
import {
  CaveatList,
  Card,
  PageHead,
  SourceBadge,
  StatusBadge,
  Tile,
} from "@/components/primitives";
import { FigureValue } from "@/components/FigureValue";
import { ProvenanceChip } from "@/components/ProvenanceChip";
import { formatAge, formatTimestamp } from "@/lib/format";
import type {
  Envelope,
  HealthReport,
  PortfolioView,
  RiskStateView,
} from "@/lib/types";

/**
 * COMMAND · Overview.
 *
 * The first thing on the page is platform health, not P&L. V1's dashboard led
 * with "Fleet PnL (live)" — which was a backtest number — above "Recent
 * Execution", which was the same backtest trades again, beside hardcoded
 * "Online" and "Connected" badges. An operator could not tell a dead backend
 * from a quiet one.
 */
export default function OverviewPage() {
  const health = useApi<HealthReport>("/api/health", { refreshMs: 20_000 });
  const portfolio = useApi<Envelope<PortfolioView>>("/api/trading/portfolio");
  const risk = useApi<Envelope<RiskStateView>>("/api/trading/risk");

  return (
    <>
      <PageHead
        title="Overview"
        intro="Platform health first, then the book. Every figure below carries the provenance of the run that produced it."
      />

      <Card title="Platform health">
        <AsyncBoundary state={health} label="platform health" onRetry={health.reload}>
          {(report) => (
            <div data-testid="health-panel">
              <div className="row" style={{ marginBottom: 10 }}>
                <StatusBadge status={report.status} />
                <span className="muted">
                  build {report.build_sha ?? "unknown"} · mode {report.execution_mode} ·
                  migration {report.migration_revision ?? "unknown"} · worker heartbeat{" "}
                  <span data-testid="health-heartbeat">
                    {formatAge(report.worker_heartbeat_age_seconds)}
                  </span>{" "}
                  · checked {formatTimestamp(report.checked_at)}
                </span>
              </div>
              {report.advisory ? (
                <p className="state state--error" data-testid="health-advisory">
                  {report.advisory}
                </p>
              ) : null}
              <div className="table-wrap">
                <table>
                  <thead>
                    <tr>
                      <th>Check</th>
                      <th>Status</th>
                      <th>Detail</th>
                    </tr>
                  </thead>
                  <tbody>
                    {report.checks.map((check) => (
                      <tr key={check.name}>
                        <td className="mono">{check.name}</td>
                        <td>
                          <StatusBadge status={check.status} />
                        </td>
                        <td className="wrap">{check.detail}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </div>
          )}
        </AsyncBoundary>
      </Card>

      <Card title="Account">
        <AsyncBoundary
          state={portfolio}
          label="the portfolio"
          onRetry={portfolio.reload}
        >
          {(envelope) => (
            <>
              <SourceBadge source={envelope.source} />
              <div className="tiles">
                <Tile label="Balance" figure={envelope.data.balance} />
                <Tile label="Equity" figure={envelope.data.equity} />
                <Tile
                  label="Realised P&L"
                  figure={envelope.data.realised_pnl}
                  colourSign
                />
                <Tile
                  label="Unrealised P&L"
                  figure={envelope.data.unrealised_pnl}
                  colourSign
                />
                <Tile label="Open positions" figure={envelope.data.open_positions} />
                <Tile
                  label="Max drawdown"
                  figure={envelope.data.max_drawdown_pct}
                />
              </div>
              <p className="muted">
                Trade record by provenance:{" "}
                {Object.entries(envelope.data.provenance_mix).map(([key, count]) => (
                  <span key={key} style={{ marginRight: 10 }}>
                    <ProvenanceChip provenance={key as never} /> {count}
                  </span>
                ))}
              </p>
              <LineChart series={envelope.data.equity_curve} />
              <CaveatList caveats={envelope.data.equity_curve.caveats} />
            </>
          )}
        </AsyncBoundary>
      </Card>

      <Card title="Risk">
        <AsyncBoundary state={risk} label="risk state" onRetry={risk.reload}>
          {(envelope) => (
            <>
              <div className="tiles">
                <Tile label="Daily P&L" figure={envelope.data.daily_loss_pct} colourSign />
                <Tile label="Daily loss limit" figure={envelope.data.max_daily_loss_pct} />
                <Tile label="Drawdown" figure={envelope.data.drawdown_pct} />
                <Tile
                  label="Margin utilisation"
                  figure={envelope.data.margin_utilisation_pct}
                  help="Unknown without a broker account snapshot."
                />
              </div>
              {envelope.data.breaches.length > 0 ? (
                <div className="state state--error">
                  <div className="state__title">Limit breaches</div>
                  <ul>
                    {envelope.data.breaches.map((b) => (
                      <li key={b}>{b}</li>
                    ))}
                  </ul>
                </div>
              ) : (
                <p className="muted">
                  No limit breach against limit set{" "}
                  <span className="mono">{envelope.data.limits_version}</span>.
                </p>
              )}
            </>
          )}
        </AsyncBoundary>
      </Card>

      <Card title="Kill switch">
        <KillSwitchPanel />
      </Card>
    </>
  );
}
