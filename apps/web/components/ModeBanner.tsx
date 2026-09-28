"use client";

import { useApi } from "@/lib/api";
import { formatAge, formatTimestamp } from "@/lib/format";
import type { Envelope, ExecutionModeBanner } from "@/lib/types";
import { useFreshness } from "./AsyncBoundary";

/**
 * The sticky execution-mode banner.
 *
 * Position: sticky, top: 0 (see .mode-banner in globals.css) so it cannot
 * scroll out of view. Its content comes from GET /api/system/execution-mode —
 * the real mode, from the API, refreshed on an interval. It is rendered once,
 * in the root layout, above every page.
 *
 * When the FIRST call fails the banner does not disappear and does not guess.
 * It turns into an explicit "mode unknown" state, because a workstation that
 * cannot say where its orders go should say exactly that.
 *
 * Once a mode has been read, the banner never goes back to "MODE …". A failed
 * or overdue refresh keeps the last mode the platform reported, with a STALE
 * marker saying how old it is. It used to blank to grey on every 30-second
 * poll, which trained the eye to ignore the one element that must be read.
 */
export function ModeBanner() {
  const state = useApi<Envelope<ExecutionModeBanner>>(
    "/api/system/execution-mode",
    { refreshMs: 30_000 },
  );
  const freshness = useFreshness(state, state.refreshMs);

  if (state.status === "loading") {
    return (
      <div className="mode-banner unknown" data-testid="mode-banner" data-mode="loading">
        <span className="mode-banner__mode">MODE …</span>
        <span className="mode-banner__detail">
          Reading the execution mode from the platform.
        </span>
      </div>
    );
  }

  if (state.status === "error") {
    return (
      <div
        className="mode-banner danger"
        data-testid="mode-banner"
        data-mode="unknown"
        role="alert"
      >
        <span className="mode-banner__mode">MODE UNKNOWN</span>
        <span className="mode-banner__detail">
          The platform did not answer, so this workstation cannot say what mode
          it is in or where orders would go. Treat every figure on screen as
          stale. ({state.error.code})
        </span>
      </div>
    );
  }

  const banner = state.data.data;
  const stale = freshness?.stale === true;
  const refreshError = state.refreshError;
  return (
    <div
      className={`mode-banner ${banner.severity}`}
      data-testid="mode-banner"
      data-mode={banner.mode}
      data-severity={banner.severity}
      data-real-money={banner.touches_real_money}
      data-stale={stale}
      role={banner.severity === "danger" || stale ? "alert" : "status"}
    >
      <span className="mode-banner__mode" data-testid="mode-banner-mode">
        {banner.mode.toUpperCase()}
      </span>
      <span className="mode-banner__detail">
        <strong>{banner.headline}</strong> — {banner.detail}
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
        <span className="mode-banner__ks" data-testid="mode-banner-killswitch">
          KILL SWITCH ARMED
          {banner.kill_switch_mode ? ` · ${banner.kill_switch_mode.toUpperCase()}` : ""}
        </span>
      ) : null}
    </div>
  );
}
