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
                      <tr key={key} data-testid="realism-row" data-key={key}>
                        <td className="mono">{key}</td>
                        <td className="wrap">
                          <span className="mono">{value}</span>
                          <span className="muted" data-testid="realism-meaning">
                            {" "}
                            {REALISM_MEANING[`${key}:${value}`] ?? "(this code is not described in the workstation)"}
                          </span>
                        </td>
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
                      <th>Unit</th>
                    </tr>
                  </thead>
                  <tbody>
                    {Object.entries(envelope.data.limits).map(([key, value]) => {
                      const unit = limitUnit(key, value);
                      return (
                        <tr key={key} data-testid="limit-setting" data-key={key}>
                          <td className="mono">{key}</td>
                          <td className="mono wrap" data-testid="limit-setting-value">
                            {limitValue(value, unit)}
                          </td>
                          <td className="wrap" data-testid="limit-setting-unit">
                            {unit}
                          </td>
                        </tr>
                      );
                    })}
                  </tbody>
                </table>
              </TableWrap>
            </Card>
            <Card title="Security">
              <ul className="muted">
                <li>Cookie secure: {String(envelope.data.cookie_secure)}</li>
                <li>Cookie SameSite: {envelope.data.cookie_samesite}</li>
                <li>Allowed origins: {envelope.data.allowed_origin_count}</li>
                <li>Session TTL: {envelope.data.session_ttl_seconds} s</li>
                <li>Build: {envelope.data.build_sha ?? "unknown"}</li>
              </ul>
            </Card>
          </>
        )}
      </AsyncBoundary>
    </>
  );
}

/**
 * The unit of a limit, from its name. The limit set's own convention
 * (risk/limits.py: "Percentages are percentages (5.0 means 5%), fractions are
 * fractions") is carried in each field's suffix; a key this table does not
 * recognise says so rather than guessing.
 */
function limitUnit(key: string, value: unknown): string {
  if (value === null || value === undefined) return "not set (this limit set predates the rule)";
  if (typeof value === "boolean") return "on / off";
  if (key === "version" || key === "notes") return "text";
  if (key === "correlation_threshold") return "correlation coefficient, 0 to 1";
  if (key === "min_broker_health") return "health score, 0 to 1";
  if (key === "max_spread_multiple") return "× the instrument's typical spread";
  if (key.endsWith("_pct")) return "% of equity";
  if (key.endsWith("_seconds")) return "seconds";
  if (key.endsWith("_minutes")) return "minutes";
  return "unit not stated by the API";
}

function limitValue(value: unknown, unit: string): string {
  if (value === null || value === undefined) return "—";
  if (typeof value === "boolean") return value ? "on" : "off";
  if (unit === "% of equity" && typeof value === "number") return `${value}%`;
  if (unit === "seconds" && typeof value === "number") return `${value} s`;
  if (unit === "minutes" && typeof value === "number") return `${value} min`;
  if (unit.startsWith("×") && typeof value === "number") return `×${value}`;
  return String(value);
}

/**
 * What each realism code in force means, for the codes api/settings.py
 * defines today. The caveats on every figure are still the server's; this is
 * only a gloss on the code.
 */
const REALISM_MEANING: Record<string, string> = {
  "slippage:zero": "every fill is assumed at the requested price",
  "spread:static_typical": "the instrument's typical spread, held constant",
  "spread:static": "a constant spread per instrument",
  "financing:none": "no overnight financing is charged",
  "fx_conversion:static_rate": "P&L is converted at a fixed rate",
};
