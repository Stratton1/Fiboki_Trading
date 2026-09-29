"use client";

import { createContext, useContext, type ReactNode } from "react";
import { useApi, type ApiHandle } from "@/lib/api";
import type {
  Envelope,
  ExecutionMode,
  ExecutionModeBanner,
  HealthReport,
  PrincipalView,
} from "@/lib/types";
import { useFreshness, type Freshness } from "../AsyncBoundary";

/**
 * The shell's three platform reads, made ONCE and shared: the execution mode
 * (every 30 s), health (every 20 s) and the signed-in operator (once).
 *
 * Before this, the banner, the kill switch and the promote page each fetched
 * the execution mode on their own, and could disagree for a poll interval.
 * Wave 2 replaces this with TanStack Query and the stream; until then this is
 * the single source.
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
  /** False while the mode is loading or unknown: nothing mutating may be confirmed. */
  mutationsAllowed: boolean;
}

interface Platform {
  mode: ModeState;
  health: ApiHandle<HealthReport>;
  operator: PrincipalView | null;
}

const PlatformContext = createContext<Platform | null>(null);

export const MODE_REFRESH_MS = 30_000;
export const HEALTH_REFRESH_MS = 20_000;

export function PlatformProvider({ children }: { children: ReactNode }) {
  const modeHandle = useApi<Envelope<ExecutionModeBanner>>("/api/system/execution-mode", {
    refreshMs: MODE_REFRESH_MS,
  });
  const freshness = useFreshness(modeHandle, modeHandle.refreshMs);
  const health = useApi<HealthReport>("/api/health", { refreshMs: HEALTH_REFRESH_MS });
  const me = useApi<PrincipalView>("/api/auth/me");

  const banner = modeHandle.status === "success" ? modeHandle.data.data : null;
  const mode: ModeKey =
    modeHandle.status === "loading" ? "loading" : banner === null ? "unknown" : banner.mode;

  const value: Platform = {
    mode: {
      handle: modeHandle,
      freshness,
      mode,
      banner,
      stale: freshness?.stale === true,
      mutationsAllowed: banner !== null,
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
