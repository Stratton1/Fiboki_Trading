const API_URL = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";

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
    apiFetch("/research/run", { method: "POST", body: JSON.stringify(body) }),
  rankings: (params?: string) =>
    apiFetch<import("@/types/contracts/research").ResearchResult[]>(
      `/research/rankings${params ? `?${params}` : ""}`
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
  instruments: () => apiFetch<Array<Record<string, unknown>>>("/instruments/"),
  strategies: () => apiFetch<Array<Record<string, unknown>>>("/strategies/"),

  // System
  systemHealth: () => apiFetch<{ status: string; version: string }>("/system/health"),
  systemStatus: () => apiFetch<Record<string, unknown>>("/system/status"),
};

export { ApiError };
