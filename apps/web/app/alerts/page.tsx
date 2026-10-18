"use client";

import Link from "next/link";
import { AsyncBoundary } from "@/components/AsyncBoundary";
import { FigureValue } from "@/components/FigureValue";
import { CaveatList, Card, PageHead, SourceBadge } from "@/components/primitives";
import { SeverityBadge } from "@/components/command/SeverityBadge";
import { useClock } from "@/lib/clock";
import { formatAge, formatTimestamp, parseUtc } from "@/lib/format";
import { useApi } from "@/lib/query";
import type { AlertsView, Envelope } from "@/lib/types";

/**
 * SYSTEM · Alerts: the alert manager (programme feature 30).
 *
 * Every alert rule the platform has (the obs taxonomy), its default
 * severity, the condition that raises it, its state (FIRING when it has an
 * open incident, QUIET when it has fired before, NEVER when it has not), when
 * it last fired and how often, and the alert history from the alert log. All
 * from GET /api/system/alerts. Rules are code, reviewed in commits: this page
 * reads them and edits none. Acknowledging happens on the incident.
 */

const STATE_WORD = { firing: "FIRING", quiet: "QUIET", never_fired: "NEVER" } as const;

function ago(iso: string | null, now: number): string {
  if (!iso) return "never";
  const t = parseUtc(iso).getTime();
  return Number.isNaN(t) ? "unknown" : formatAge(Math.max(0, (now - t) / 1_000));
}

export default function AlertsPage() {
  const state = useApi<Envelope<AlertsView>>("/api/system/alerts", { refreshMs: 15_000 });
  const now = useClock();
  return (
    <>
      <PageHead
        title="Alerts"
        intro="Every alert rule the platform has, whether it is firing now, when it last fired, and the alert history. Rules are code and change only in a reviewed commit; an incident is acknowledged on its own page."
      />
      <AsyncBoundary state={state} label="the alert manager" onRetry={state.reload}>
        {(envelope) => {
          const view = envelope.data;
          const firing = view.rules.filter((r) => r.state === "firing").length;
          return (
            <>
              <div className="alerts__summary" data-testid="alerts-summary">
                <span className={`badge badge--${firing > 0 ? "down" : "ok"}`} data-testid="alerts-firing">
                  {firing} FIRING
                </span>
                <span className="muted">
                  channels: <span className="mono">{view.channels.join(", ")}</span>
                  {view.log_configured ? "" : " · alert log not configured: history is empty and only kill-switch incidents are seen"}
                </span>
                {Object.entries(view.thresholds).map(([name, figure]) => (
                  <span key={name} className="muted">
                    {name.replace(/_/g, " ")} <FigureValue figure={figure} showChip={false} />
                  </span>
                ))}
              </div>
              <CaveatList caveats={envelope.caveats.filter((c) => c.severity !== "info")} />
              <div className="alerts__grid">
                <Card title="Rules">
                  <div className="table-wrap">
                    <table className="dense" data-testid="alert-rules">
                      <thead>
                        <tr>
                          <th scope="col">State</th>
                          <th scope="col">Rule</th>
                          <th scope="col">Severity</th>
                          <th scope="col" className="num">
                            Fired
                          </th>
                          <th scope="col" className="num">
                            Last fired
                          </th>
                          <th scope="col">Condition</th>
                        </tr>
                      </thead>
                      <tbody>
                        {view.rules.map((rule) => (
                          <tr key={rule.event} data-testid="alert-rule" data-event={rule.event} data-state={rule.state}>
                            <td>
                              <span
                                className={`badge badge--${rule.state === "firing" ? "down" : rule.state === "quiet" ? "neutral" : "unknown"}`}
                              >
                                {STATE_WORD[rule.state]}
                              </span>
                            </td>
                            <th scope="row" className="mono">
                              {rule.open_incident ? (
                                <Link href={rule.open_incident} data-testid="alert-rule-incident">
                                  {rule.event}
                                </Link>
                              ) : (
                                rule.event
                              )}
                              {rule.category === "trade" ? <span className="muted"> · trade</span> : null}
                            </th>
                            <td>
                              <SeverityBadge severity={rule.severity} />
                            </td>
                            <td className="num">
                              <FigureValue figure={rule.fired} showChip={false} />
                            </td>
                            <td className="num" title={rule.last_fired_at ? formatTimestamp(rule.last_fired_at) : undefined}>
                              {ago(rule.last_fired_at, now)}
                            </td>
                            <td className="wrap muted">{rule.condition}</td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                </Card>
                <Card title="History">
                  {view.history.length === 0 ? (
                    <p className="muted" data-testid="alert-history-empty">
                      {view.log_configured
                        ? "The alert log holds no alert: none has been dispatched."
                        : "No alert log is configured, so no dispatched alert can be read. This is not a quiet system."}
                    </p>
                  ) : (
                    <div className="table-wrap">
                      <table className="dense" data-testid="alert-history">
                        <thead>
                          <tr>
                            <th scope="col">At (UTC)</th>
                            <th scope="col">Rule</th>
                            <th scope="col">Severity</th>
                            <th scope="col">Message</th>
                          </tr>
                        </thead>
                        <tbody>
                          {view.history.map((row, index) => (
                            <tr key={`${row.at}-${row.event}-${index}`} data-testid="alert-history-row">
                              <td className="num">{formatTimestamp(row.at)}</td>
                              <th scope="row" className="mono">
                                {row.link ? <Link href={row.link}>{row.event}</Link> : row.event}
                              </th>
                              <td>
                                <SeverityBadge severity={row.severity} />
                              </td>
                              <td className="wrap">{row.message}</td>
                            </tr>
                          ))}
                        </tbody>
                      </table>
                    </div>
                  )}
                  <p className="muted">
                    <FigureValue figure={view.history_total} showChip={false} /> alerts in the log.
                  </p>
                </Card>
              </div>
              <SourceBadge source={envelope.source} />
            </>
          );
        }}
      </AsyncBoundary>
    </>
  );
}
