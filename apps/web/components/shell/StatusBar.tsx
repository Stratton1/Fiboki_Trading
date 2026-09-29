"use client";

import { Activity, Clock, HeartPulse, Radio, UserRound } from "lucide-react";
import { useSyncExternalStore } from "react";
import { useLatestAsOf } from "@/lib/as-of";
import { formatAge, formatTimestamp, formatUtcTime } from "@/lib/format";
import { StatusBadge } from "../primitives";
import { useFreshness } from "../AsyncBoundary";
import type { Tone } from "../ui/StatusPill";
import { DisplaySettings } from "./DisplaySettings";
import { useInspector } from "./Inspector";
import { useExecutionMode, useHealth, useOperator, type ModeKey } from "./platform";

/**
 * The status bar: the facts an operator needs to trust the screen, always
 * visible. Mode (mirrored so it shows even when the banner is out of a
 * split's view), stream, worker heartbeat, API health, the newest as-of the
 * platform has sent, the operator, and a UTC clock.
 *
 * Every entry says what it knows and nothing more: an API it cannot reach
 * reads "unreachable", a worker that never beat reads "never", and the stream
 * slot reads "no stream" because there is none yet (data is polled).
 */

function subscribeClock(listener: () => void) {
  const timer = setInterval(listener, 1000);
  return () => clearInterval(timer);
}

function useUtcClock(): string | null {
  return useSyncExternalStore(
    subscribeClock,
    () => formatUtcTime(Math.floor(Date.now() / 1000) * 1000),
    () => null,
  );
}

const MODE_TEXT: Record<ModeKey, string> = {
  loading: "MODE …",
  unknown: "MODE UNKNOWN",
  backtest: "BACKTEST",
  paper: "PAPER",
  shadow: "SHADOW",
  demo: "DEMO",
  live: "LIVE · REAL MONEY",
};

export function StatusBar() {
  const { mode, stale } = useExecutionMode();
  const health = useHealth();
  const healthFreshness = useFreshness(health, health.refreshMs);
  const operator = useOperator();
  const asOf = useLatestAsOf();
  const clock = useUtcClock();
  const inspector = useInspector();

  let apiTone: Tone = "unknown";
  let apiText = "API …";
  if (health.status === "error") {
    apiTone = "critical";
    apiText = "API unreachable";
  } else if (health.status === "success") {
    const status = health.data.status;
    apiTone = status === "ok" ? "ok" : status === "degraded" ? "warn" : "critical";
    apiText = `API ${status}`;
    if (healthFreshness?.stale) {
      apiTone = "warn";
      apiText = `API ${status} · stale`;
    }
  }

  let workerTone: Tone = "unknown";
  let workerText = "worker hb unknown";
  if (health.status === "success") {
    const age = health.data.worker_heartbeat_age_seconds;
    if (age === null) {
      workerTone = "warn";
      workerText = "worker hb never";
    } else {
      workerTone = healthFreshness?.stale ? "warn" : "ok";
      workerText = `worker hb ${formatAge(age)}`;
    }
  }

  const modeTone: Tone =
    mode === "unknown" ? "critical" : mode === "loading" ? "unknown" : stale ? "warn" : "ok";

  const openHealth = () =>
    inspector.open({
      title: "Platform health",
      body:
        health.status === "success" ? (
          <div className="stack" data-testid="inspector-health">
            <div className="row">
              <StatusBadge status={health.data.status} />
              <span className="muted">checked {formatTimestamp(health.data.checked_at)}</span>
            </div>
            {health.data.advisory ? <p className="muted">{health.data.advisory}</p> : null}
            <ul className="stack list-none ps-0">
              {health.data.checks.map((check) => (
                <li key={check.name} className="row">
                  <StatusBadge status={check.status} />
                  <span className="mono">{check.name}</span>
                  <span className="muted wrap">{check.detail}</span>
                </li>
              ))}
            </ul>
          </div>
        ) : health.status === "error" ? (
          <p className="state state--error" data-testid="inspector-health">
            The health endpoint did not answer: {health.error.message} ({health.error.code}).
          </p>
        ) : (
          <p className="muted" data-testid="inspector-health">
            Reading platform health.
          </p>
        ),
    });

  return (
    <footer className="status-bar" data-testid="status-bar" aria-label="Platform status">
      <span
        className="status-bar__item"
        data-testid="status-mode"
        data-mode={mode}
        data-tone={modeTone}
      >
        <span className="font-semibold">{MODE_TEXT[mode]}</span>
        {stale ? <span> · stale</span> : null}
      </span>
      <span
        className="status-bar__item"
        data-testid="status-stream"
        data-tone="unknown"
        title="No live stream is connected; views refresh by polling."
      >
        <Radio size={12} aria-hidden="true" />
        no stream
      </span>
      <span className="status-bar__item" data-testid="status-worker" data-tone={workerTone}>
        <HeartPulse size={12} aria-hidden="true" />
        {workerText}
      </span>
      <button
        type="button"
        className="status-bar__item"
        data-testid="status-api"
        data-tone={apiTone}
        onClick={openHealth}
        aria-label={`${apiText}. Open platform health.`}
      >
        <Activity size={12} aria-hidden="true" />
        {apiText}
      </button>
      <span className="status-bar__item" data-testid="status-as-of" data-as-of={asOf ?? undefined}>
        {asOf ? `data as of ${formatUtcTime(asOf)}` : "no data as-of yet"}
      </span>
      {operator ? (
        <span className="status-bar__item" data-testid="status-operator">
          <UserRound size={12} aria-hidden="true" />
          {operator.display_name} ({operator.role})
        </span>
      ) : null}
      <span className="status-bar__spacer" />
      <DisplaySettings />
      <span className="status-bar__item" data-testid="status-clock">
        <Clock size={12} aria-hidden="true" />
        {clock ?? "--:--:-- UTC"}
      </span>
    </footer>
  );
}
