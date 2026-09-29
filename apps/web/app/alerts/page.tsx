"use client";

import { useApi } from "@/lib/query";
import { AsyncBoundary } from "@/components/AsyncBoundary";
import { Card, PageHead, StatusBadge } from "@/components/primitives";
import { formatTimestamp } from "@/lib/format";
import type { Envelope, HealthReport, RiskStateView } from "@/lib/types";

/**
 * COMMAND · Alerts.
 *
 * Derived from measured state — failing health checks and breached risk limits
 * — rather than from a separate alerting store that can itself go quiet. An
 * empty alert list here is only meaningful because the health panel above it is
 * green, and that relationship is stated on the page.
 */
export default function AlertsPage() {
  const health = useApi<HealthReport>("/api/health", { refreshMs: 15_000 });
  const risk = useApi<Envelope<RiskStateView>>("/api/trading/risk");

  return (
    <>
      <PageHead
        title="Alerts"
        intro="Everything currently wrong, derived from live probes. An empty list only means 'nothing wrong' when the checks below all pass."
      />
      <Card title="Failing checks">
        <AsyncBoundary state={health} label="platform health" onRetry={health.reload}>
          {(report) => {
            const failing = report.checks.filter((c) => c.status !== "ok");
            if (failing.length === 0) {
              return (
                <div className="state state--empty">
                  <div className="state__title">
                    <span>No failing checks</span>
                    <StatusBadge status={report.status} />
                  </div>
                  <div className="state__body">
                    Every health probe returned ok at {formatTimestamp(report.checked_at)}.
                  </div>
                </div>
              );
            }
            return (
              <div className="stack">
                {failing.map((check) => (
                  <div
                    key={check.name}
                    className={`caveat caveat--${check.status === "down" ? "critical" : "warning"}`}
                  >
                    <span className="caveat__code">
                      {check.name} · {check.critical ? "critical" : "advisory"}
                    </span>
                    {check.detail}
                  </div>
                ))}
              </div>
            );
          }}
        </AsyncBoundary>
      </Card>
      <Card title="Risk breaches">
        <AsyncBoundary state={risk} label="risk state" onRetry={risk.reload}>
          {(envelope) =>
            envelope.data.breaches.length === 0 ? (
              <div className="state state--empty">
                <div className="state__body">
                  No limit is breached against limit set{" "}
                  <span className="mono">{envelope.data.limits_version}</span>.
                </div>
              </div>
            ) : (
              <div className="stack">
                {envelope.data.breaches.map((breach) => (
                  <div key={breach} className="caveat caveat--critical">
                    {breach}
                  </div>
                ))}
              </div>
            )
          }
        </AsyncBoundary>
      </Card>
    </>
  );
}
