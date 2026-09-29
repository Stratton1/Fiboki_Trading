"use client";

import { useQueryClient } from "@tanstack/react-query";
import { Activity, Clock, HeartPulse, LogOut, Radio, UserRound } from "lucide-react";
import { useState } from "react";
import { ApiError } from "@/lib/api";
import { useOnScreenAsOf } from "@/lib/as-of";
import { signOut } from "@/lib/auth";
import { useClock } from "@/lib/clock";
import { isStale, workerStatus } from "@/lib/freshness";
import { formatAge, formatTimestamp, formatUtcTime } from "@/lib/format";
import { useLive, type ConnectionInfo, type HeartbeatInfo } from "@/lib/live-store";
import { workerAgeNow, workerStaleAfter, workerStateNow } from "@/lib/query";
import { HEARTBEAT_OVERDUE_MS } from "@/lib/stream/router";
import { StatusBadge } from "../primitives";
import type { Tone } from "../ui/StatusPill";
import { useToast } from "../ui/Toast";
import { DisplaySettings } from "./DisplaySettings";
import { useInspector } from "./Inspector";
import { useExecutionMode, useHealth, useOperator, type ModeKey } from "./platform";

/**
 * The status bar: the facts an operator needs to trust the screen, always
 * visible (report E §6.4). Mode, stream state and lag, the worker heartbeat
 * age (counting up between heartbeats, so a dead worker is visible without
 * waiting for the next report) toned by the PLATFORM's verdict on the worker
 * (report G W-03), API health, the OLDEST platform as-of among the views on
 * screen with probes excluded (report G W-04), the operator, and a UTC clock.
 *
 * Every entry says what it knows and nothing more: an API it cannot reach
 * reads "unreachable", a worker that never beat reads "never", and a stream
 * that has given up reads "disconnected".
 */

const MODE_TEXT: Record<ModeKey, string> = {
  loading: "MODE …",
  unknown: "MODE UNKNOWN",
  backtest: "BACKTEST",
  paper: "PAPER",
  shadow: "SHADOW",
  demo: "DEMO",
  live: "LIVE · REAL MONEY",
};

function streamText(
  connection: ConnectionInfo,
  heartbeat: HeartbeatInfo | null,
  now: number,
): { tone: Tone; text: string; title: string } {
  switch (connection.state) {
    case "idle":
      return { tone: "unknown", text: "stream off", title: "No live stream has been started." };
    case "connecting":
      return {
        tone: "unknown",
        text: "stream connecting…",
        title: "Opening the live stream; views refresh by polling until it is up.",
      };
    case "reconnecting":
      return {
        tone: "warn",
        text: `stream reconnecting (${connection.failures})`,
        title: `The stream dropped. ${connection.failures} consecutive failed attempt(s); retrying with backoff. Views poll REST meanwhile.`,
      };
    case "disconnected":
      return {
        tone: "critical",
        text: "stream disconnected",
        title: `${connection.failures} consecutive failures. Views poll REST every 10 s; use Reconnect in the banner to try now.`,
      };
    case "live": {
      if (heartbeat === null) {
        return { tone: "ok", text: "stream live", title: "Connected; waiting for the first heartbeat." };
      }
      const quietMs = Math.max(0, now - heartbeat.receivedAt);
      if (quietMs > HEARTBEAT_OVERDUE_MS) {
        return {
          tone: "warn",
          text: `stream quiet ${formatAge(quietMs / 1_000).replace(/ ago$/, "")}`,
          title: `No heartbeat for ${Math.round(quietMs / 1_000)} s; the server sends one every 5 s.`,
        };
      }
      return {
        tone: "ok",
        text: `stream live · lag ${heartbeat.lagMs < 1_000 ? `${Math.round(heartbeat.lagMs)} ms` : `${(heartbeat.lagMs / 1_000).toFixed(1)} s`}`,
        title: `Last heartbeat sent ${formatTimestamp(heartbeat.serverTime)}.`,
      };
    }
  }
}

export function StatusBar() {
  const { mode, stale } = useExecutionMode();
  const health = useHealth();
  const operator = useOperator();
  const onScreen = useOnScreenAsOf();
  const asOf = onScreen.oldest?.iso ?? null;
  const now = useClock();
  const inspector = useInspector();
  const connection = useLive((s) => s.connection);
  const heartbeat = useLive((s) => s.heartbeat);
  const client = useQueryClient();
  const toast = useToast();
  const [signingOut, setSigningOut] = useState(false);

  let apiTone: Tone = "unknown";
  let apiText = "API …";
  if (health.status === "error") {
    apiTone = "critical";
    apiText = "API unreachable";
  } else if (health.status === "success") {
    const status = health.data.status;
    apiTone = status === "ok" ? "ok" : status === "degraded" ? "warn" : "critical";
    apiText = `API ${status}`;
    if (isStale(health.freshness)) {
      apiTone = "warn";
      apiText = `API ${status} · stale`;
    }
  }

  const workerAge = useLive((s) => workerAgeNow(s, now));
  const staleAfter = useLive(workerStaleAfter);
  const workerState = useLive(workerStateNow);
  const streamLive = connection.state === "live" && heartbeat !== null;
  const worker = workerStatus({
    ageS: workerAge,
    state: workerState,
    staleAfterS: staleAfter,
    // Over REST, the verdict is only as current as the health report.
    reportStale: !streamLive && health.status === "success" && isStale(health.freshness),
  });
  const workerTone: Tone = worker.tone;
  const workerText = `worker hb ${worker.text}`;

  const stream = streamText(connection, heartbeat, now);

  const modeTone: Tone =
    mode === "unknown" ? "critical" : mode === "loading" ? "unknown" : stale ? "warn" : "ok";

  // The body is a live component, not a snapshot: opened before health has
  // loaded, it fills in when it does, and it follows every later update.
  const openHealth = () => inspector.open({ title: "Platform health", body: <HealthDetail /> });
  const openAsOf = () => inspector.open({ title: "Data as of", body: <AsOfDetail /> });

  const onSignOut = async () => {
    setSigningOut(true);
    try {
      await signOut(client);
    } catch (error) {
      setSigningOut(false);
      toast(
        `Sign-out failed: ${error instanceof ApiError ? `${error.message} (${error.code})` : "the request failed"}. You are still signed in.`,
      );
    }
  };

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
        data-state={connection.state}
        data-failures={connection.failures}
        data-role={connection.role}
        data-tone={stream.tone}
        title={stream.title}
      >
        <Radio size={12} aria-hidden="true" />
        {stream.text}
      </span>
      <span
        className="status-bar__item"
        data-testid="status-worker"
        data-tone={workerTone}
        data-worker-state={workerState ?? undefined}
        data-age={workerAge === undefined || workerAge === null ? undefined : Math.floor(workerAge)}
        title={worker.detail}
      >
        <HeartPulse size={12} aria-hidden="true" />
        {workerText}
        <span className="sr-only">. {worker.detail}</span>
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
      <button
        type="button"
        className="status-bar__item"
        data-testid="status-as-of"
        data-as-of={asOf ?? undefined}
        data-path={onScreen.oldest?.path}
        onClick={openAsOf}
        aria-label={
          asOf
            ? `Oldest data on screen is as of ${formatUtcTime(asOf)}. Show every view's as-of.`
            : "No view on screen has a platform as-of. Show details."
        }
      >
        {asOf ? `data as of ${formatUtcTime(asOf)}` : "no data as-of on screen"}
      </button>
      {operator ? (
        <span className="status-bar__item" data-testid="status-operator" data-role={operator.role}>
          <UserRound size={12} aria-hidden="true" />
          {operator.display_name} ({operator.role})
          <button
            type="button"
            className="status-bar__signout"
            data-testid="sign-out"
            onClick={onSignOut}
            disabled={signingOut}
            aria-label={`Sign out ${operator.display_name}`}
          >
            <LogOut size={12} aria-hidden="true" />
          </button>
        </span>
      ) : null}
      <span className="status-bar__spacer" />
      <DisplaySettings />
      <span className="status-bar__item" data-testid="status-clock">
        <Clock size={12} aria-hidden="true" />
        {now > 0 ? formatUtcTime(Math.floor(now / 1_000) * 1_000) : "--:--:-- UTC"}
      </span>
    </footer>
  );
}

/** Every view on screen and its platform as-of, oldest first (report G W-04). */
function AsOfDetail() {
  const { views, oldest, newest } = useOnScreenAsOf();
  const sorted = [...views].sort((a, b) => {
    if (a.ms === null) return 1;
    if (b.ms === null) return -1;
    return a.ms - b.ms;
  });
  return (
    <div className="stack" data-testid="inspector-as-of">
      <p className="muted">
        The status bar shows the OLDEST platform as-of among the views on this screen. Probes
        (health, execution mode, the signed-in operator) are excluded: a health check that ran a
        moment ago says nothing about how old the data is.
      </p>
      {sorted.length === 0 ? (
        <p className="muted">No view on this screen has received data yet.</p>
      ) : (
        <ul className="stack list-none ps-0">
          {sorted.map((view, index) => (
            <li key={`${view.path}:${index}`} className="row" data-oldest={view === oldest}>
              <span className="mono">{view.path}</span>
              <span className="muted">
                {view.iso ? `as of ${formatTimestamp(view.iso)}` : "no as-of supplied by the platform"}
              </span>
            </li>
          ))}
        </ul>
      )}
      {newest ? (
        <p className="muted">Newest as-of received on the stream: {formatTimestamp(newest)}.</p>
      ) : null}
    </div>
  );
}

/** The inspector's view of platform health, kept current while it is open. */
function HealthDetail() {
  const health = useHealth();
  if (health.status === "success") {
    return (
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
    );
  }
  if (health.status === "error") {
    return (
      <p className="state state--error" data-testid="inspector-health">
        The health endpoint did not answer: {health.error.message} ({health.error.code}).
      </p>
    );
  }
  return (
    <p className="muted" data-testid="inspector-health">
      Reading platform health.
    </p>
  );
}
