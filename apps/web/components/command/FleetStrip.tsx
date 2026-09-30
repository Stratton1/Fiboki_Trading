"use client";

import type { ReactNode } from "react";
import { useClock } from "@/lib/clock";
import { isStale, workerStatus } from "@/lib/freshness";
import { formatAge, formatTimestamp } from "@/lib/format";
import { useLive } from "@/lib/live-store";
import { workerAgeNow, workerStaleAfter, workerStateNow } from "@/lib/query";
import type { HealthCheck, HealthReport } from "@/lib/types";
import { AsyncBoundary } from "../AsyncBoundary";
import { StatusBadge, TableWrap } from "../primitives";
import { useHealth } from "../shell/platform";
import { ViewStateTag } from "../ui/ViewStateTag";

/**
 * COMMAND · the fleet strip: is every process that feeds this screen alive?
 *
 * Four cells, each from where the platform actually reports it:
 *  - worker heartbeat: the platform's verdict (stream heartbeat when live,
 *    else the `worker_heartbeat` check of GET /api/health), toned exactly as
 *    the status bar tones it (lib/freshness.ts `workerStatus`), and the age
 *    the last health report gave;
 *  - paper session: the `paper_journal` check of /api/health. The platform
 *    omits that check when no paper journal exists at all, and the strip says
 *    so rather than showing a healthy session;
 *  - news poll and model provider: neither /api/health nor the stream reports
 *    them, so both are NOT REPORTED, with that reason.
 *
 * Under the strip: the platform's overall status, build, mode and migration,
 * the advisory when there is one, and every health check on request.
 */

type Tone = "ok" | "warn" | "critical" | "unknown";

function checkTone(check: HealthCheck): Tone {
  return check.status === "ok" ? "ok" : check.status === "degraded" ? "warn" : "critical";
}

const TONE_GLYPH: Record<Tone, string> = { ok: "✓", warn: "◐", critical: "✕", unknown: "?" };

function Cell({
  name,
  tone,
  value,
  detail,
  testId,
  absent = false,
}: {
  name: string;
  tone: Tone;
  value: ReactNode;
  detail: ReactNode;
  testId: string;
  absent?: boolean;
}) {
  return (
    <li className="fleet__cell" data-testid={testId} data-tone={absent ? "absent" : tone}>
      <span className="fleet__name">{name}</span>
      <span className="fleet__value">
        {absent ? null : (
          <span className="fleet__glyph" aria-hidden="true">
            {TONE_GLYPH[tone]}
          </span>
        )}
        {value}
      </span>
      <span className="fleet__detail">{detail}</span>
    </li>
  );
}

function Strip({ report, healthStale }: { report: HealthReport; healthStale: boolean }) {
  const now = useClock();
  const connection = useLive((s) => s.connection.state);
  const heartbeat = useLive((s) => s.heartbeat);
  const ageS = useLive((s) => workerAgeNow(s, now));
  const staleAfter = useLive(workerStaleAfter);
  const state = useLive(workerStateNow);
  const streamLive = connection === "live" && heartbeat !== null;
  const worker = workerStatus({
    ageS,
    state,
    staleAfterS: staleAfter,
    reportStale: !streamLive && healthStale,
  });
  const paper = report.checks.find((check) => check.name === "paper_journal");
  return (
    <ul className="fleet__strip" data-testid="fleet-strip">
      <Cell
        testId="fleet-worker"
        name="Worker heartbeat"
        tone={worker.tone}
        value={worker.text}
        detail={
          <>
            {worker.detail} Last health report ({formatTimestamp(report.checked_at)}) gave{" "}
            <span data-testid="health-heartbeat">{formatAge(report.worker_heartbeat_age_seconds)}</span>
            {streamLive ? "; the age above counts from the stream heartbeat." : "."}
          </>
        }
      />
      {paper ? (
        <Cell
          testId="fleet-paper"
          name="Paper session"
          tone={checkTone(paper)}
          value={paper.status.toUpperCase()}
          detail={paper.detail}
        />
      ) : (
        <Cell
          testId="fleet-paper"
          name="Paper session"
          tone="unknown"
          value="NONE"
          detail="The health report has no paper_journal check: the platform omits it when no paper journal exists, so no paper session is running."
        />
      )}
      <Cell
        testId="fleet-news"
        name="News poll"
        tone="unknown"
        absent
        value={<ViewStateTag state="absent">NOT REPORTED</ViewStateTag>}
        detail="Neither /api/health nor the stream reports a news poll, so its state is unknown here."
      />
      <Cell
        testId="fleet-model"
        name="Model provider"
        tone="unknown"
        absent
        value={<ViewStateTag state="absent">NOT REPORTED</ViewStateTag>}
        detail="Neither /api/health nor the stream reports the agents' model provider, so its state is unknown here."
      />
    </ul>
  );
}

export function FleetStrip() {
  const health = useHealth();
  return (
    <AsyncBoundary state={health} label="platform health" onRetry={health.reload}>
      {(report) => (
        <div data-testid="health-panel" className="fleet">
          <Strip report={report} healthStale={health.status === "success" && isStale(health.freshness)} />
          <p className="fleet__meta muted">
            <StatusBadge status={report.status} /> build {report.build_sha ?? "unknown"} · mode {report.execution_mode}{" "}
            · migration {report.migration_revision ?? "unknown"} · checked {formatTimestamp(report.checked_at)}
          </p>
          {report.advisory ? (
            <p className="state state--error" data-testid="health-advisory">
              {report.advisory}
            </p>
          ) : null}
          <details className="chart__data" data-testid="health-checks">
            <summary>All health checks ({report.checks.length})</summary>
            <TableWrap>
              <table>
                <thead>
                  <tr>
                    <th scope="col">Check</th>
                    <th scope="col">Status</th>
                    <th scope="col">Detail</th>
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
            </TableWrap>
          </details>
        </div>
      )}
    </AsyncBoundary>
  );
}
