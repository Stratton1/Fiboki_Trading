"use client";

import { useApi } from "@/lib/api";
import type { Envelope, ExecutionModeBanner } from "@/lib/types";

/**
 * The sticky execution-mode banner.
 *
 * Position: sticky, top: 0 (see .mode-banner in globals.css) so it cannot
 * scroll out of view. Its content comes from GET /api/system/execution-mode —
 * the real mode, from the API, refreshed on an interval. It is rendered once,
 * in the root layout, above every page.
 *
 * When the call FAILS the banner does not disappear and does not guess. It
 * turns into an explicit "mode unknown" state, because a workstation that
 * cannot say where its orders go should say exactly that.
 */
export function ModeBanner() {
  const state = useApi<Envelope<ExecutionModeBanner>>(
    "/api/system/execution-mode",
    { refreshMs: 30_000 },
  );

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
  return (
    <div
      className={`mode-banner ${banner.severity}`}
      data-testid="mode-banner"
      data-mode={banner.mode}
      data-severity={banner.severity}
      data-real-money={banner.touches_real_money}
      role={banner.severity === "danger" ? "alert" : "status"}
    >
      <span className="mode-banner__mode" data-testid="mode-banner-mode">
        {banner.mode.toUpperCase()}
      </span>
      <span className="mode-banner__detail">
        <strong>{banner.headline}</strong> — {banner.detail}
      </span>
      {banner.kill_switch_active ? (
        <span className="mode-banner__ks" data-testid="mode-banner-killswitch">
          KILL SWITCH ARMED
          {banner.kill_switch_mode ? ` · ${banner.kill_switch_mode.toUpperCase()}` : ""}
        </span>
      ) : null}
    </div>
  );
}
