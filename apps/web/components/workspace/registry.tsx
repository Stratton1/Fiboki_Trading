"use client";

import { lazy, type ComponentType, type LazyExoticComponent } from "react";
import type { PanelKind } from "@/lib/workspace";
import type { PanelProps } from "./types";

/**
 * The workstation side of the typed panel registry. The kinds are the API's
 * union (GET /api/workspace/panels, generated into lib/generated/openapi.ts),
 * and this record is `Record<PanelKind, …>`: a kind added to the API without a
 * panel here fails the type check, never an operator's screen. Each body is a
 * separate chunk, loaded when a tile of that kind first appears, so the
 * workspace route's first load carries the tiling and none of the panels.
 */
export interface PanelEntry {
  Body: LazyExoticComponent<ComponentType<PanelProps>>;
  /** Short code shown in the tile header (the command-line code where one exists). */
  code: string;
}

export const PANELS: Record<PanelKind, PanelEntry> = {
  chart: { code: "GP", Body: lazy(() => import("./panels/ChartPanel")) },
  watchlist: { code: "WL", Body: lazy(() => import("./panels/WatchlistPanel")) },
  news: { code: "NWS", Body: lazy(() => import("./panels/NewsPanel")) },
  calendar: { code: "ECO", Body: lazy(() => import("./panels/CalendarPanel")) },
  fleet: { code: "FLT", Body: lazy(() => import("./panels/FleetPanel")) },
  risk: { code: "RSK", Body: lazy(() => import("./panels/RiskPanel")) },
  exposure: { code: "EXPO", Body: lazy(() => import("./panels/ExposurePanel")) },
  alerts: { code: "NTF", Body: lazy(() => import("./panels/AlertsPanel")) },
  journal: { code: "JRN", Body: lazy(() => import("./panels/JournalPanel")) },
  research_runs: { code: "RUNS", Body: lazy(() => import("./panels/ResearchRunsPanel")) },
  market_pulse: { code: "PULSE", Body: lazy(() => import("./panels/PulsePanel")) },
  system_board: { code: "SYS", Body: lazy(() => import("./panels/SystemBoardPanel")) },
  notes: { code: "NOTE", Body: lazy(() => import("./panels/NotesPanel")) },
};
