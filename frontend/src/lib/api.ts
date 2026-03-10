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
  // Abort after 10s to prevent infinite loading screens
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), 10000);
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
    throw new ApiError(res.status, body.detail || "Request failed");
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
  marketData: (instrument: string, timeframe: string) =>
    apiFetch<import("@/types/contracts/chart").MarketDataResponse>(
      `/market-data/${instrument}/${timeframe}`
    ),

  // Charts
  annotations: (backtestId: number) =>
    apiFetch<import("@/types/contracts/trades").ChartAnnotationsResponse>(
      `/charts/annotations/${backtestId}`
    ),

  // Backtests
  runBacktest: (body: Record<string, unknown>) =>
    apiFetch("/backtests/run", { method: "POST", body: JSON.stringify(body) }),
  listBacktests: (params?: string) =>
    apiFetch<import("@/types/contracts/analytics").BacktestSummary[]>(
      `/backtests${params ? `?${params}` : ""}`
    ),
  getBacktest: (id: number) =>
    apiFetch<import("@/types/contracts/analytics").BacktestDetail>(`/backtests/${id}`),
  getEquityCurve: (id: number) =>
    apiFetch<{ equity_curve: number[] }>(`/backtests/${id}/equity-curve`),

  // Research
  runResearch: (body: Record<string, unknown>) =>
    apiFetch<import("@/types/contracts/research").ResearchRunSummary>(
      "/research/run", { method: "POST", body: JSON.stringify(body) }
    ),
  rankings: (params?: string) =>
    apiFetch<import("@/types/contracts/research").ResearchResult[]>(
      `/research/rankings${params ? `?${params}` : ""}`
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

  // Paper
  createBot: (body: Record<string, unknown>) =>
    apiFetch("/paper/bots", { method: "POST", body: JSON.stringify(body) }),
  listBots: () => apiFetch("/paper/bots"),
  getBot: (id: string) => apiFetch(`/paper/bots/${id}`),
  stopBot: (id: string) => apiFetch(`/paper/bots/${id}/stop`, { method: "POST" }),
  pauseBot: (id: string) => apiFetch(`/paper/bots/${id}/pause`, { method: "POST" }),
  account: () => apiFetch("/paper/account"),

  // Trades
  listTrades: (params?: string) =>
    apiFetch<import("@/types/contracts/trades").TradeListResponse>(
      `/trades/${params ? `?${params}` : ""}`
    ),
  getTrade: (id: number) =>
    apiFetch<import("@/types/contracts/trades").Trade>(`/trades/${id}`),

  // Instruments & strategies
  instruments: () =>
    apiFetch<
      Array<{ symbol: string; name: string; asset_class: string; has_canonical_data: boolean }>
    >("/instruments/"),
  strategies: () => apiFetch<Array<Record<string, unknown>>>("/strategies/"),

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
};

export { API_URL, ApiError };
