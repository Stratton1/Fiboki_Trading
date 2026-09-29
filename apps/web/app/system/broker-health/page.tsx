"use client";

import { useApi } from "@/lib/query";
import { AsyncBoundary } from "@/components/AsyncBoundary";
import { Card, PageHead, SourceBadge, TableWrap } from "@/components/primitives";
import type { BrokerHealthView, Envelope } from "@/lib/types";

/** SYSTEM · Broker Health: the mode guard's verdict, control by control. */
export default function BrokerHealthPage() {
  const state = useApi<Envelope<BrokerHealthView>>("/api/system/broker-health");
  return (
    <>
      <PageHead
        title="Broker Health"
        intro="What the mode guard says about this deployment's venue. In paper mode a venue that is not reachable is the correct answer, not a fault."
      />
      <AsyncBoundary state={state} label="broker health" onRetry={state.reload}>
        {(envelope) => (
          <>
            <SourceBadge source={envelope.source} />
            <Card>
              <p>
                Venue host:{" "}
                <span className="mono">
                  {envelope.data.venue_url_host || "not configured"}
                </span>
              </p>
              <p>
                Guard verdict:{" "}
                <span
                  className={`badge badge--${envelope.data.guard_allowed ? "ok" : "down"}`}
                >
                  {envelope.data.guard_allowed ? "ALLOWED" : "REFUSED"}
                </span>
              </p>
              <p className="muted">{envelope.data.detail}</p>
            </Card>
            <Card title="Controls">
              <TableWrap>
                <table>
                  <thead>
                    <tr>
                      <th>Control</th>
                      <th>Satisfied</th>
                    </tr>
                  </thead>
                  <tbody>
                    {Object.entries(envelope.data.controls).map(([name, ok]) => (
                      <tr key={name}>
                        <td className="mono">{name}</td>
                        <td>
                          <span className={`badge badge--${ok ? "ok" : "down"}`}>
                            {ok ? "YES" : "NO"}
                          </span>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </TableWrap>
              {envelope.data.reasons.length > 0 ? (
                <ul className="muted">
                  {envelope.data.reasons.map((reason) => (
                    <li key={reason}>{reason}</li>
                  ))}
                </ul>
              ) : null}
            </Card>
          </>
        )}
      </AsyncBoundary>
    </>
  );
}
