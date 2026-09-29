"use client";

import { usePathname } from "next/navigation";
import { createContext, useContext, useEffect, type ReactNode } from "react";
import { LOGIN_PATH } from "@/lib/api";
import { ME_PATH } from "@/lib/auth";
import type { Freshness } from "@/lib/freshness";
import { writeLive } from "@/lib/live-store";
import { useApi, type ApiHandle } from "@/lib/query";
import type {
  Envelope,
  ExecutionMode,
  ExecutionModeBanner,
  HealthReport,
  PrincipalView,
} from "@/lib/types";

/**
 * The shell's three platform reads, made ONCE and shared: the execution mode,
 * health and the signed-in operator. All three are TanStack Query reads, so a
 * page that reads the same path shares the same cache entry, and the stream
 * updates them in place (mode, health) with no polling while it is live.
 */

export type ModeKey = "loading" | "unknown" | ExecutionMode;

export interface ModeState {
  handle: ApiHandle<Envelope<ExecutionModeBanner>>;
  freshness: Freshness | null;
  /**
   * `unknown` only when the platform has never answered (first load failed).
   * Once a mode has been read, a failed refresh keeps it, marked stale.
   */
  mode: ModeKey;
  banner: ExecutionModeBanner | null;
  stale: boolean;
  /**
   * False while the mode is loading or unknown, or the workstation is
   * disconnected from the platform: nothing mutating may be confirmed.
   */
  mutationsAllowed: boolean;
}

interface Platform {
  mode: ModeState;
  health: ApiHandle<HealthReport>;
  operator: PrincipalView | null;
}

const PlatformContext = createContext<Platform | null>(null);

export const MODE_PATH = "/api/system/execution-mode";

/** REST poll intervals while the stream is not feeding these reads. */
export const MODE_REFRESH_MS = 30_000;
export const HEALTH_REFRESH_MS = 20_000;

export function PlatformProvider({ children }: { children: ReactNode }) {
  const signingIn = usePathname() === LOGIN_PATH;
  const modeHandle = useApi<Envelope<ExecutionModeBanner>>(MODE_PATH, {
    refreshMs: MODE_REFRESH_MS,
  });
  const health = useApi<HealthReport>(signingIn ? null : "/api/health", {
    refreshMs: HEALTH_REFRESH_MS,
  });
  // No "who am I" on the sign-in page: its 401 would redirect to itself.
  const me = useApi<PrincipalView>(signingIn ? null : ME_PATH);

  // The worker heartbeat from REST, for freshness while the stream is down.
  // The platform's own verdict rides along: the `worker_heartbeat` check.
  const workerAge = health.status === "success" ? health.data.worker_heartbeat_age_seconds : undefined;
  const workerState =
    health.status === "success"
      ? (health.data.checks.find((check) => check.name === "worker_heartbeat")?.status ?? null)
      : null;
  const healthAsOf = health.status === "success" ? health.asOf : null;
  useEffect(() => {
    if (workerAge === undefined || healthAsOf === null) return;
    writeLive(() => ({
      restWorker: { ageS: workerAge, receivedAt: Date.parse(healthAsOf), state: workerState },
    }));
  }, [workerAge, workerState, healthAsOf]);

  const banner = modeHandle.status === "success" ? modeHandle.data.data : null;
  const mode: ModeKey =
    modeHandle.status === "loading" ? "loading" : banner === null ? "unknown" : banner.mode;
  const freshness = modeHandle.status === "success" ? modeHandle.freshness : null;

  const value: Platform = {
    mode: {
      handle: modeHandle,
      freshness,
      mode,
      banner,
      stale: freshness === "stale" || freshness === "disconnected",
      mutationsAllowed: banner !== null && freshness !== "disconnected",
    },
    health,
    // Absent unless the platform answered; the UI then omits the name rather
    // than inventing one.
    operator: me.status === "success" ? me.data : null,
  };

  return <PlatformContext.Provider value={value}>{children}</PlatformContext.Provider>;
}

function usePlatform(): Platform {
  const platform = useContext(PlatformContext);
  if (platform === null) throw new Error("PlatformProvider is missing from the shell.");
  return platform;
}

export function useExecutionMode(): ModeState {
  return usePlatform().mode;
}

export function useHealth(): ApiHandle<HealthReport> {
  return usePlatform().health;
}

export function useOperator(): PrincipalView | null {
  return usePlatform().operator;
}
