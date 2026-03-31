// NEXT_PUBLIC_API_URL is inlined at build time by Next.js.
// The runtime guard below protects against stale Vercel CDN builds that may
// still have http:// baked in from an older compilation.
const _buildTimeUrl = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";
const API_URL =
  typeof window !== "undefined" &&
  window.location.protocol === "https:" &&
  _buildTimeUrl.startsWith("http://") &&
  !_buildTimeUrl.includes("localhost")
    ? _buildTimeUrl.replace("http://", "https://")
    : _buildTimeUrl;

interface WatchlistResponse {
  id: number;
  name: string;
  instrument_ids: string[];
}

class ApiError extends Error {
  constructor(public status: number, message: string) {
    super(message);
  }
}

async function apiFetch<T>(path: string, options: RequestInit = {}): Promise<T> {
  const headers: Record<string, string> = {
    "Content-Type": "application/json",
  };
  // Use Authorization header as fallback when cross-origin cookies aren't available
  if (typeof window !== "undefined") {
    const token = localStorage.getItem("fibokei_token");
    if (token) {
      headers["Authorization"] = `Bearer ${token}`;
    }
  }
  // Abort after timeout to prevent infinite loading screens.
  // POST/PUT/DELETE get 30s; GET gets 10s.
  const method = (options.method || "GET").toUpperCase();
  const timeoutMs = method === "GET" ? 10000 : 30000;
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), timeoutMs);
  let res: Response;
  try {
    res = await fetch(`${API_URL}/api/v1${path}`, {
      credentials: "include",
      signal: controller.signal,
      headers: {
        ...headers,
        ...options.headers,
      },
      ...options,
    });
  } catch (fetchErr) {
    clearTimeout(timeout);
    if (fetchErr instanceof DOMException && fetchErr.name === "AbortError") {
      throw new ApiError(0, `Request timed out after ${timeoutMs / 1000}s`);
    }
    throw fetchErr;
  } finally {
    clearTimeout(timeout);
  }

  if (res.status === 401) {
    if (typeof window !== "undefined" && !path.includes("/auth/")) {
      localStorage.removeItem("fibokei_token");
      document.cookie = "fiboki_auth=; path=/; max-age=0";
      window.location.href = "/login";
    }
    throw new ApiError(401, "Unauthorized");
  }

  if (!res.ok) {
    const body = await res.json().catch(() => ({ detail: "Request failed" }));
    let message = "Request failed";
    if (typeof body.detail === "string") {
      message = body.detail;
    } else if (Array.isArray(body.detail)) {
      message = body.detail.map((e: { msg?: string; loc?: string[] }) =>
        e.msg ? `${e.msg} (${(e.loc ?? []).join(".")})` : JSON.stringify(e)
      ).join("; ");
    }
    throw new ApiError(res.status, message);
  }

  return res.json();
}

export const api = {
  // Auth
  login: (username: string, password: string) => {
    const formData = new URLSearchParams();
    formData.append("username", username);
    formData.append("password", password);
    return fetch(`${API_URL}/api/v1/auth/login`, {
      method: "POST",
      credentials: "include",
      headers: { "Content-Type": "application/x-www-form-urlencoded" },
      body: formData,
    });
  },
  logout: () => apiFetch("/auth/logout", { method: "POST" }),
  me: () => apiFetch<{ user_id: number; username: string; role: string }>("/auth/me"),

  // Market data
  marketData: (instrument: string, timeframe: string, mode: "historical" | "live" = "historical") =>
    apiFetch<import("@/types/contracts/chart").MarketDataResponse>(
      `/market-data/${instrument}/${timeframe}?mode=${mode}`
    ),
  sessions: () =>
    apiFetch<{
      sessions: Array<{ name: string; start_utc: string; end_utc: string; color: string }>;
    }>("/market-data/sessions"),
  liveStatus: () =>
    apiFetch<import("@/types/contracts/chart").LiveStatusResponse>(
      "/market-data/live/status"
    ),

  // Charts
  annotations: (backtestId: number) =>
    apiFetch<import("@/types/contracts/trades").ChartAnnotationsResponse>(
      `/charts/annotations/${backtestId}`
    ),

  // Backtests
  runBacktest: (body: Record<string, unknown>, async_mode = false) =>
    apiFetch(`/backtests/run${async_mode ? "?async=true" : ""}`, { method: "POST", body: JSON.stringify(body) }),
  listBacktests: (params?: string) =>
    apiFetch<import("@/types/contracts/analytics").BacktestSummary[]>(
      `/backtests${params ? `?${params}` : ""}`
    ),
  getBacktest: (id: number) =>
    apiFetch<import("@/types/contracts/analytics").BacktestDetail>(`/backtests/${id}`),
  getEquityCurve: (id: number) =>
    apiFetch<{ equity_curve: number[] }>(`/backtests/${id}/equity-curve`),
  deleteBacktest: (id: number) =>
    apiFetch<{ deleted: number }>(`/backtests/${id}`, { method: "DELETE" }),
  bulkDeleteBacktests: (ids: number[]) =>
    apiFetch<{ deleted_count: number; requested: number }>(`/backtests/bulk`, {
      method: "DELETE",
      body: JSON.stringify({ ids }),
    }),
  getBacktestTrades: (id: number, page = 1, size = 50, sort?: string) =>
    apiFetch<import("@/types/contracts/trades").TradeListResponse>(
      `/backtests/${id}/trades?page=${page}&size=${size}${sort ? `&sort=${sort}` : ""}`
    ),

  // Research
  runResearch: (body: Record<string, unknown>) =>
    apiFetch<{ job_id: string; job_type: string; label: string; state: string }>(
      "/research/run", { method: "POST", body: JSON.stringify(body) }
    ),
  rankings: (params?: string) =>
    apiFetch<import("@/types/contracts/research").ResearchResult[]>(
      `/research/rankings${params ? (params.startsWith("?") ? params : `?${params}`) : ""}`
    ),
  advancedResearch: (body: Record<string, unknown>) =>
    apiFetch<import("@/types/contracts/research").AdvancedResearchResponse>(
      "/research/advanced", { method: "POST", body: JSON.stringify(body) }
    ),
  validateResearch: (body: Record<string, unknown>) =>
    apiFetch<import("@/types/contracts/research").ValidationBatchResponse>(
      "/research/validate", { method: "POST", body: JSON.stringify(body) }
    ),
  compareResearch: (combos: string[]) =>
    apiFetch<import("@/types/contracts/research").ResearchResult[]>(
      "/research/compare", { method: "POST", body: JSON.stringify({ combos }) }
    ),
  listPresets: () =>
    apiFetch<import("@/types/contracts/research").ResearchPreset[]>("/research/presets"),
  createPreset: (body: { name: string; description?: string; config: Record<string, unknown> }) =>
    apiFetch<import("@/types/contracts/research").ResearchPreset>("/research/presets", {
      method: "POST", body: JSON.stringify(body),
    }),
  updatePreset: (id: number, body: Record<string, unknown>) =>
    apiFetch<import("@/types/contracts/research").ResearchPreset>(`/research/presets/${id}`, {
      method: "PUT", body: JSON.stringify(body),
    }),
  deletePreset: (id: number) =>
    apiFetch<{ deleted: number }>(`/research/presets/${id}`, { method: "DELETE" }),
  runScenario: (body: {
    combos: Array<{ strategy_id: string; instrument: string; timeframe: string; risk_pct?: number }>;
    capital?: number;
  }) =>
    apiFetch<{ job_id: string; job_type: string; label: string; state: string }>(
      "/research/scenario", { method: "POST", body: JSON.stringify(body) }
    ),
  deleteResearchRun: (runId: string) =>
    apiFetch<{ deleted: string; results_removed: number }>(`/research/runs/${runId}`, { method: "DELETE" }),

  // Saved Shortlist
  listShortlist: () =>
    apiFetch<import("@/types/contracts/research").ShortlistEntry[]>("/research/shortlist"),
  saveToShortlist: (body: {
    strategy_id: string; instrument: string; timeframe: string; score: number;
    source_run_id?: string; metrics_snapshot?: Record<string, unknown>; note?: string;
  }) =>
    apiFetch<import("@/types/contracts/research").ShortlistEntry>("/research/shortlist", {
      method: "POST", body: JSON.stringify(body),
    }),
  updateShortlistEntry: (id: number, body: { note?: string; status?: string }) =>
    apiFetch<import("@/types/contracts/research").ShortlistEntry>(`/research/shortlist/${id}`, {
      method: "PATCH", body: JSON.stringify(body),
    }),
  deleteShortlistEntry: (id: number) =>
    apiFetch<{ deleted: number }>(`/research/shortlist/${id}`, { method: "DELETE" }),

  // Research Runs + Result Deletion
  listResearchRuns: () =>
    apiFetch<import("@/types/contracts/research").ResearchRunListItem[]>("/research/runs"),
  deleteSingleResult: (id: number) =>
    apiFetch<{ deleted: number }>(`/research/results/${id}`, { method: "DELETE" }),
  deleteResultsBulk: (runId?: string) =>
    apiFetch<{ deleted_count: number; run_id: string | null }>(
      `/research/results${runId ? `?run_id=${runId}` : ""}`, { method: "DELETE" }
    ),
  deleteNonSavedResults: (runId?: string) =>
    apiFetch<{ deleted_count: number; run_id: string | null }>(
      `/research/results/non-saved${runId ? `?run_id=${runId}` : ""}`, { method: "DELETE" }
    ),
  autoScout: (body: {
    timeframes?: string[];
    initial_capital?: number;
    min_trades?: number;
    asset_classes?: string[];
  }) =>
    apiFetch<{ job_id: string; job_type: string; label: string; state: string }>(
      "/research/auto-scout", { method: "POST", body: JSON.stringify(body) }
    ),
  smartDeploy: (body: {
    top_n?: number;
    run_id?: string;
    min_score?: number;
    risk_pct?: number;
  }) =>
    apiFetch<{
      deployed: number;
      skipped: number;
      bots: Array<{
        bot_id: string;
        strategy_id: string;
        instrument: string;
        timeframe: string;
        composite_score: number;
      }>;
    }>("/research/smart-deploy", { method: "POST", body: JSON.stringify(body) }),

  // Paper
  createBot: (body: Record<string, unknown>) =>
    apiFetch("/paper/bots", { method: "POST", body: JSON.stringify(body) }),
  listBots: () => apiFetch("/paper/bots"),
  getBot: (id: string) => apiFetch(`/paper/bots/${id}`),
  stopBot: (id: string) => apiFetch(`/paper/bots/${id}/stop`, { method: "POST" }),
  pauseBot: (id: string) => apiFetch(`/paper/bots/${id}/pause`, { method: "POST" }),
  resumeBot: (id: string) => apiFetch(`/paper/bots/${id}/resume`, { method: "POST" }),
  deleteBot: (id: string) => apiFetch<{ deleted: string }>(`/paper/bots/${id}`, { method: "DELETE" }),
  deleteAllBots: () => apiFetch<{ deleted_count: number }>(`/paper/bots`, { method: "DELETE" }),
  account: () => apiFetch("/paper/account"),
  fleet: () => apiFetch<{
    total_bots: number;
    running: number;
    paused: number;
    stopped: number;
    stale: number;
    aggregate_pnl: number;
    aggregate_trades: number;
    open_positions: number;
    bots: Array<{
      bot_id: string;
      strategy_id: string;
      instrument: string;
      timeframe: string;
      state: string;
      bars_seen: number;
      total_trades: number;
      total_pnl: number;
      has_position: boolean;
      source_type: string | null;
      last_evaluated_bar: string | null;
      is_stale: boolean;
    }>;
    strategy_groups: Record<string, { count: number; running: number; pnl: number; trades: number }>;
  }>("/paper/fleet"),
  botTrades: (botId: string) => apiFetch<{
    bot_id: string;
    total: number;
    trades: Array<Record<string, unknown>>;
    equity_curve: number[];
  }>(`/paper/bots/${botId}/trades`),
  exposure: () => apiFetch<{
    instrument_exposure: Record<string, { long: number; short: number; net: number; bot_count: number }>;
    asset_class_exposure: Record<string, { long: number; short: number; instruments: number }>;
    direction_balance: { long: number; short: number };
    active_positions: number;
    concentration_warnings: Array<{ instrument: string; bot_count: number }>;
    risk_utilization: {
      open_trades: number;
      max_open_trades: number;
      open_trades_pct: number;
      daily_dd_pct: number;
      daily_soft_stop_pct: number;
      daily_hard_stop_pct: number;
      weekly_dd_pct: number;
      weekly_soft_stop_pct: number;
      weekly_hard_stop_pct: number;
    };
    total_bots: number;
    total_trades: number;
  }>("/paper/exposure"),
  fleetRisk: () => apiFetch<{
    fleet_limits: {
      max_bots_per_instrument: number;
      max_total_positions: number;
      max_exposure_per_instrument: number;
      correlation_threshold: number;
      cull_sigma: number;
      cull_min_trades: number;
    };
    fleet_status: {
      total_bots: number;
      active_bots: number;
      open_positions: number;
      positions_limit_pct: number;
    };
    instrument_alerts: Array<{ instrument: string; bot_count: number; limit: number }>;
    correlation_alerts: Array<{ bot_a: string; bot_b: string; overlap: number }>;
    underperformers: Array<{
      bot_id: string;
      avg_pnl: number;
      fleet_median: number;
      fleet_std: number;
      sigma_below: number;
    }>;
  }>("/paper/fleet/risk"),

  // Trades
  listTrades: (params?: string) =>
    apiFetch<import("@/types/contracts/trades").TradeListResponse>(
      `/trades/${params ? `?${params}` : ""}`
    ),
  getTrade: (id: number) =>
    apiFetch<import("@/types/contracts/trades").Trade>(`/trades/${id}`),

  // Trade Journal
  getJournal: (tradeId: number) =>
    apiFetch<import("@/types/contracts/trades").JournalEntry | null>(`/trades/${tradeId}/journal`),
  createJournal: (tradeId: number, body: { note?: string; tags?: string[] }) =>
    apiFetch<import("@/types/contracts/trades").JournalEntry>(`/trades/${tradeId}/journal`, {
      method: "POST", body: JSON.stringify(body),
    }),
  updateJournal: (tradeId: number, body: { note?: string; tags?: string[] }) =>
    apiFetch<import("@/types/contracts/trades").JournalEntry>(`/trades/${tradeId}/journal`, {
      method: "PATCH", body: JSON.stringify(body),
    }),
  deleteJournal: (tradeId: number) =>
    apiFetch<{ deleted: number }>(`/trades/${tradeId}/journal`, { method: "DELETE" }),
  listJournal: (params?: string) =>
    apiFetch<import("@/types/contracts/trades").JournalListResponse>(
      `/journal${params ? `?${params}` : ""}`
    ),

  // Strategy Variants
  listVariants: (strategyId?: string) =>
    apiFetch<{
      items: Array<{
        id: number;
        strategy_id: string;
        name: string;
        params: Record<string, number>;
        is_active: boolean;
        backtest_run_id: number | null;
        trade_overlap: number | null;
        created_at: string | null;
      }>;
      total: number;
    }>(`/variations${strategyId ? `?strategy_id=${strategyId}` : ""}`),
  createVariant: (body: { strategy_id: string; name: string; params: Record<string, number> }) =>
    apiFetch<{ id: number; strategy_id: string; name: string; params: Record<string, number> }>(
      "/variations", { method: "POST", body: JSON.stringify(body) }
    ),
  deleteVariant: (id: number) =>
    apiFetch<{ deleted: number }>(`/variations/${id}`, { method: "DELETE" }),
  variantParams: (strategyId: string) =>
    apiFetch<{
      strategy_id: string;
      params: Record<string, number[]>;
      constructor_params: Record<string, string>;
    }>(`/variations/params/${strategyId}`),
  generateVariants: (body: { strategy_id: string; max_variants?: number }) =>
    apiFetch<{ strategy_id: string; variants: Array<Record<string, number>>; count: number }>(
      "/variations/generate", { method: "POST", body: JSON.stringify(body) }
    ),

  // Instruments & strategies
  instruments: () =>
    apiFetch<
      Array<{ symbol: string; name: string; asset_class: string; has_canonical_data: boolean }>
    >("/instruments"),
  strategies: () => apiFetch<Array<Record<string, unknown>>>("/strategies"),

  // System
  systemHealth: () => apiFetch<{ status: string; version: string }>("/system/health"),
  systemStatus: () =>
    apiFetch<{
      api_version: string;
      database: string;
      paper_engine: string;
      strategies_loaded: number;
      execution_mode: string;
      kill_switch_active: boolean;
    }>("/system/status"),
  riskConfig: () =>
    apiFetch<{
      max_risk_per_trade_pct: number;
      max_portfolio_risk_pct: number;
      max_open_trades: number;
      max_per_instrument: number;
      daily_soft_stop_pct: number;
      daily_hard_stop_pct: number;
      weekly_soft_stop_pct: number;
      weekly_hard_stop_pct: number;
      fleet_max_bots_per_instrument: number;
      fleet_max_total_positions: number;
      fleet_max_exposure_per_instrument: number;
      fleet_correlation_threshold: number;
      fleet_cull_sigma: number;
      fleet_cull_min_trades: number;
    }>("/system/risk-config"),
  igHealth: () =>
    apiFetch<{
      configured: boolean;
      reachable: boolean;
      account_id: string | null;
      account_name: string | null;
      balance: number | null;
      error: string | null;
    }>("/execution/ig-health"),

  // Execution
  executionMode: () =>
    apiFetch<{
      mode: string;
      live_execution_enabled: boolean;
      ig_paper_mode: boolean;
      kill_switch_active: boolean;
    }>("/execution/mode"),
  killSwitchStatus: () =>
    apiFetch<{
      is_active: boolean;
      reason: string | null;
      activated_by: string | null;
      activated_at: string | null;
    }>("/execution/kill-switch"),
  activateKillSwitch: (reason: string) =>
    apiFetch("/execution/kill-switch/activate", {
      method: "POST",
      body: JSON.stringify({ reason }),
    }),
  deactivateKillSwitch: () =>
    apiFetch("/execution/kill-switch/deactivate", { method: "POST" }),
  executionAudit: (params?: string) =>
    apiFetch<
      Array<{
        id: number;
        timestamp: string;
        execution_mode: string;
        action: string;
        instrument: string;
        direction: string | null;
        size: number | null;
        deal_id: string | null;
        status: string;
        error_message: string | null;
        bot_id: string | null;
      }>
    >(`/execution/audit${params ? `?${params}` : ""}`),
  slippage: (instrument?: string) =>
    apiFetch<{
      total_fills: number;
      avg_slippage_pips: number;
      instruments: Array<{
        instrument: string;
        fills: number;
        avg_slippage_pips: number;
        max_slippage_pips: number;
        min_slippage_pips: number;
        avg_latency_ms: number;
      }>;
    }>(`/execution/slippage${instrument ? `?instrument=${instrument}` : ""}`),

  // Data availability
  manifest: () =>
    apiFetch<import("@/types/contracts/chart").DataManifest>("/data/manifest"),
  refreshManifest: () =>
    apiFetch<{ status: string; datasets: number }>("/data/manifest/refresh", { method: "POST" }),
  dataCheck: (symbol: string, timeframe: string) =>
    apiFetch<import("@/types/contracts/chart").DataAvailability>(
      `/data/check/${symbol}/${timeframe}`
    ),

  // Jobs
  listJobs: (params?: string) =>
    apiFetch<{
      items: Array<{
        job_id: string;
        job_type: string;
        label: string;
        state: string;
        progress: number;
        created_at: string;
        started_at: string | null;
        completed_at: string | null;
        result: Record<string, unknown> | null;
        error: string | null;
      }>;
      active_count: number;
    }>(`/jobs${params ? `?${params}` : ""}`),
  getJob: (jobId: string) =>
    apiFetch<{
      job_id: string;
      job_type: string;
      label: string;
      state: string;
      progress: number;
      created_at: string;
      started_at: string | null;
      completed_at: string | null;
      result: Record<string, unknown> | null;
      error: string | null;
    }>(`/jobs/${jobId}`),
  cancelJob: (jobId: string) =>
    apiFetch<{ job_id: string; state: string }>(`/jobs/${jobId}/cancel`, { method: "POST" }),
  deleteJob: (jobId: string) =>
    apiFetch<{ deleted: string }>(`/jobs/${jobId}`, { method: "DELETE" }),
  clearFinishedJobs: () =>
    apiFetch<{ deleted_count: number }>("/jobs", { method: "DELETE" }),

  // Drawings
  listDrawings: (instrument: string, timeframe: string) =>
    apiFetch<import("@/types/contracts/drawings").ChartDrawing[]>(
      `/drawings?instrument=${instrument}&timeframe=${timeframe}`
    ),
  createDrawing: (body: import("@/types/contracts/drawings").DrawingCreate) =>
    apiFetch<import("@/types/contracts/drawings").ChartDrawing>("/drawings", {
      method: "POST",
      body: JSON.stringify(body),
    }),
  updateDrawing: (id: number, body: import("@/types/contracts/drawings").DrawingUpdate) =>
    apiFetch<import("@/types/contracts/drawings").ChartDrawing>(`/drawings/${id}`, {
      method: "PUT",
      body: JSON.stringify(body),
    }),
  deleteDrawing: (id: number) =>
    apiFetch<void>(`/drawings/${id}`, { method: "DELETE" }),
  clearDrawings: (instrument: string, timeframe: string) =>
    apiFetch<{ deleted: number }>(
      `/drawings?instrument=${instrument}&timeframe=${timeframe}`,
      { method: "DELETE" }
    ),

  // Drawing Templates
  listDrawingTemplates: () =>
    apiFetch<import("@/types/contracts/drawings").DrawingTemplate[]>(
      "/charts/drawing-templates"
    ),
  createDrawingTemplate: (body: import("@/types/contracts/drawings").DrawingTemplateCreate) =>
    apiFetch<import("@/types/contracts/drawings").DrawingTemplate>(
      "/charts/drawing-templates",
      { method: "POST", body: JSON.stringify(body) }
    ),
  deleteDrawingTemplate: (id: number) =>
    apiFetch<{ deleted: number }>(`/charts/drawing-templates/${id}`, {
      method: "DELETE",
    }),

  // Bookmarks
  listBookmarks: (entityType?: string) =>
    apiFetch<Array<{ id: number; entity_type: string; entity_id: number; note: string | null; created_at: string | null }>>(
      `/bookmarks${entityType ? `?entity_type=${entityType}` : ""}`
    ),
  createBookmark: (body: { entity_type: string; entity_id: number; note?: string }) =>
    apiFetch<{ id: number; entity_type: string; entity_id: number; note: string | null; created_at: string | null }>(
      "/bookmarks", { method: "POST", body: JSON.stringify(body) }
    ),
  deleteBookmark: (id: number) =>
    apiFetch<{ deleted: number }>(`/bookmarks/${id}`, { method: "DELETE" }),
  deleteBookmarkByEntity: (entityType: string, entityId: number) =>
    apiFetch<{ deleted: number }>(
      `/bookmarks?entity_type=${entityType}&entity_id=${entityId}`,
      { method: "DELETE" }
    ),

  // Alerts
  listAlerts: (params?: string) =>
    apiFetch<{
      items: Array<{
        id: number;
        alert_type: string;
        severity: string;
        title: string;
        message: string;
        metadata_json: Record<string, unknown> | null;
        is_read: boolean;
        created_at: string;
      }>;
      unread_count: number;
      total: number;
    }>(`/alerts${params ? `?${params}` : ""}`),
  unreadAlertCount: () =>
    apiFetch<{ unread_count: number }>("/alerts/unread-count"),
  markAlertRead: (id: number) =>
    apiFetch<{ id: number; is_read: boolean }>(`/alerts/${id}/read`, { method: "POST" }),
  markAllAlertsRead: () =>
    apiFetch<{ marked_read: number }>("/alerts/read-all", { method: "POST" }),

  // Watchlists
  listWatchlists: () => apiFetch<WatchlistResponse[]>("/watchlists"),
  createWatchlist: (body: { name: string; instrument_ids: string[] }) =>
    apiFetch<WatchlistResponse>("/watchlists", { method: "POST", body: JSON.stringify(body) }),
  updateWatchlist: (id: number, body: { name?: string; instrument_ids?: string[] }) =>
    apiFetch<WatchlistResponse>(`/watchlists/${id}`, { method: "PUT", body: JSON.stringify(body) }),
  deleteWatchlist: (id: number) =>
    apiFetch<{ deleted: number }>(`/watchlists/${id}`, { method: "DELETE" }),
};

export { API_URL, ApiError };
