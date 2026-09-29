"use client";

import { useApi } from "@/lib/query";
import { AsyncBoundary } from "@/components/AsyncBoundary";
import { Card, PageHead, SourceBadge, TableWrap } from "@/components/primitives";
import type { Envelope, SettingsView } from "@/lib/types";

/**
 * SYSTEM · Settings.
 *
 * Read-only, on purpose. Execution mode is a deploy-time control: the API
 * returns 403 for any attempt to change it, and this page shows the five
 * independent controls that would have to hold before LIVE were even possible.
 */
export default function SettingsPage() {
  const state = useApi<Envelope<SettingsView>>("/api/system/settings");
  return (
    <>
      <PageHead
        title="Settings"
        intro="What this deployment is actually configured to do. Nothing on this page is editable — execution mode in particular is a deploy-time control with no API surface."
      />
      <AsyncBoundary state={state} label="settings" onRetry={state.reload}>
        {(envelope) => (
          <>
            <SourceBadge source={envelope.source} />
            <Card title="Execution">
              <p>
                Mode: <strong className="mono">{envelope.data.execution_mode}</strong>
              </p>
              <p>
                Live execution compiled in:{" "}
                <span
                  className={`badge badge--${envelope.data.live_execution_compiled_in ? "down" : "ok"}`}
                  data-testid="live-compiled-in"
                >
                  {envelope.data.live_execution_compiled_in ? "YES" : "NO"}
                </span>
              </p>
              <p className="muted">
                Reaching LIVE requires all five controls in the mode guard, the
                first of which is a source constant. No role, and no request to
                this API, can change it.
              </p>
            </Card>
            <Card title="Realism assumptions in force">
              <p className="muted">
                These drive the caveats attached to every performance figure. The
                caveat text is generated from these values, so changing one
                changes what the platform tells you.
              </p>
              <TableWrap>
                <table>
                  <thead>
                    <tr>
                      <th>Model</th>
                      <th>Setting</th>
                    </tr>
                  </thead>
                  <tbody>
                    {Object.entries(envelope.data.realism_models).map(([key, value]) => (
                      <tr key={key}>
                        <td className="mono">{key}</td>
                        <td className="mono">{value}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </TableWrap>
            </Card>
            <Card title={`Risk limits (${envelope.data.limits_version})`}>
              <TableWrap>
                <table>
                  <thead>
                    <tr>
                      <th>Limit</th>
                      <th>Value</th>
                    </tr>
                  </thead>
                  <tbody>
                    {Object.entries(envelope.data.limits).map(([key, value]) => (
                      <tr key={key}>
                        <td className="mono">{key}</td>
                        <td className="mono wrap">{String(value)}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </TableWrap>
            </Card>
            <Card title="Security">
              <ul className="muted">
                <li>Cookie secure: {String(envelope.data.cookie_secure)}</li>
                <li>Cookie SameSite: {envelope.data.cookie_samesite}</li>
                <li>Allowed origins: {envelope.data.allowed_origin_count}</li>
                <li>Session TTL: {envelope.data.session_ttl_seconds}s</li>
                <li>Build: {envelope.data.build_sha ?? "unknown"}</li>
              </ul>
            </Card>
          </>
        )}
      </AsyncBoundary>
    </>
  );
}
