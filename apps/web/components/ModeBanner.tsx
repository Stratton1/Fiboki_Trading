"use client";

import Link from "next/link";
import { useState } from "react";
import { formatAge, formatTimestamp } from "@/lib/format";
import { useExecutionMode, useOperator } from "./shell/platform";

/**
 * The sticky execution-mode banner (v2).
 *
 * Position: sticky, top: 0 (.mode-banner in globals.css) so it cannot scroll
 * out of view. Its content comes from GET /api/system/execution-mode, read
 * once for the whole shell by PlatformProvider and refreshed on an interval.
 * It is rendered once, in the shell, above every page.
 *
 * When the FIRST call fails the banner does not disappear and does not guess.
 * It turns into an explicit "mode unknown" state, because a workstation that
 * cannot say where its orders go should say exactly that; every mutating
 * control is disabled while it does.
 *
 * Once a mode has been read, the banner never goes back to "MODE …". A failed
 * or overdue refresh keeps the last mode the platform reported, with a STALE
 * marker saying how old it is. It used to blank to grey on every 30-second
 * poll, which trained the eye to ignore the one element that must be read.
 *
 * v2: each mode has its own treatment (report E §4.4). LIVE is inverted
 * magenta with REAL MONEY in capitals, the signed-in operator's name when the
 * platform supplies one, and a kill-switch control that is always visible. The
 * kill-switch state is shown in every mode, armed or not.
 */
export function ModeBanner() {
  const { handle: state, freshness, mode } = useExecutionMode();
  const operator = useOperator();

  // A single 250 ms pulse when the mode CHANGES into live, never on load and
  // never on a poll tick. (Suppressed under reduced motion in the stylesheet.)
  const [seen, setSeen] = useState(mode);
  const [pulse, setPulse] = useState(false);
  if (seen !== mode) {
    setSeen(mode);
    setPulse(mode === "live" && seen !== "loading" && seen !== "unknown");
  }

  if (state.status === "loading") {
    return (
      <div className="mode-banner" data-testid="mode-banner" data-mode="loading" role="status">
        <span className="mode-banner__mode">MODE …</span>
        <span className="mode-banner__detail">Reading the execution mode from the platform.</span>
      </div>
    );
  }

  if (state.status === "error") {
    return (
      <div className="mode-banner" data-testid="mode-banner" data-mode="unknown" role="alert">
        <span className="mode-banner__mode" data-testid="mode-banner-mode">
          MODE UNKNOWN
        </span>
        <span className="mode-banner__detail">
          The platform did not answer, so this workstation cannot say what mode it is in or where
          orders would go. Treat every figure on screen as stale; every control that changes
          anything is disabled. ({state.error.code})
        </span>
      </div>
    );
  }

  const banner = state.data.data;
  const stale = freshness?.stale === true;
  const refreshError = state.refreshError;
  const live = banner.mode === "live";
  return (
    <div
      className="mode-banner"
      data-testid="mode-banner"
      data-mode={banner.mode}
      data-severity={banner.severity}
      data-real-money={banner.touches_real_money}
      data-stale={stale}
      data-pulse={pulse}
      role={banner.severity === "danger" || stale ? "alert" : "status"}
    >
      <span className="mode-banner__mode" data-testid="mode-banner-mode">
        {banner.mode.toUpperCase()}
      </span>
      {live || banner.touches_real_money ? (
        <span className="mode-banner__money" data-testid="mode-banner-real-money">
          REAL MONEY
        </span>
      ) : null}
      {live && operator ? (
        <span className="mode-banner__operator" data-testid="mode-banner-operator">
          {operator.display_name}
        </span>
      ) : null}
      <span className="mode-banner__detail">
        <strong>{banner.headline}</strong>
        <span aria-hidden="true"> · </span>
        {banner.detail}
      </span>
      {stale && freshness ? (
        <span
          className="mode-banner__stale"
          data-testid="mode-banner-stale"
          title={
            `Last read from the platform ${formatTimestamp(state.asOf)}. ` +
            (refreshError
              ? `The latest refresh failed (${refreshError.code}); the mode may have changed since.`
              : "No refresh has completed since; the mode may have changed.")
          }
        >
          STALE · last good {formatAge(freshness.ageSeconds)} · retrying
        </span>
      ) : null}
      {banner.kill_switch_active ? (
        <span className="mode-banner__ks" data-testid="mode-banner-killswitch" data-active="true">
          KILL SWITCH ARMED
          {banner.kill_switch_mode ? ` · ${banner.kill_switch_mode.toUpperCase()}` : ""}
        </span>
      ) : (
        <span
          className="mode-banner__ks"
          data-testid="mode-banner-killswitch-off"
          data-active="false"
        >
          Kill switch disarmed
        </span>
      )}
      {live ? (
        <Link
          href="/trading/risk"
          className="mode-banner__action"
          data-testid="mode-banner-killswitch-action"
        >
          Kill switch
        </Link>
      ) : null}
    </div>
  );
}
