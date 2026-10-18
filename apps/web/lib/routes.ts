/**
 * The workstation's entity model: every entity an operator can open, and the
 * one route each opens at. Pages link entities ONLY through these helpers, so
 * a renamed route breaks the type check, not an operator's click.
 *
 *   instrument  /markets/<SYMBOL>[?tf=&run=&trade=]   chart workstation
 *   strategy    /research/strategies/<id>             tearsheet (research)
 *   run         /research/runs/<run_id>               backtest run (research)
 *   trade       /trading/execution?row=<trade_id>     journal row
 *   session     /trading/fleet?session=<session_id>   paper bot
 *   candidate   /lifecycle/<content_hash>             lifecycle entity
 *   incident    /system/incidents/<id>                incident timeline
 *
 * The research routes are built by the research work stream; the paths are
 * the contract between the two. The API builds the same links from the same
 * table (src/fiboki/api/links.py), and tests on both sides check theirs
 * against the pages that exist.
 */

export type EntityKind = "instrument" | "strategy" | "run" | "trade" | "session" | "candidate" | "incident";

const q = (value: string) => encodeURIComponent(value);

function withQuery(path: string, params: Record<string, string | null | undefined>): string {
  const entries = Object.entries(params).filter((entry): entry is [string, string] => Boolean(entry[1]));
  if (entries.length === 0) return path;
  return `${path}?${new URLSearchParams(entries).toString()}`;
}

export const route = {
  command: () => "/",
  fleet: () => "/trading/fleet",
  risk: () => "/trading/risk",
  exposure: () => "/trading/exposure",
  journal: (filter: { strategy?: string | null; instrument?: string | null } = {}) =>
    withQuery("/trading/execution", { strategy: filter.strategy, instrument: filter.instrument }),
  portfolio: () => "/trading/portfolio",
  candidates: () => "/trading/candidates",
  system: () => "/system",
  alerts: () => "/alerts",
  functions: () => "/system/functions",
  instrument: (symbol: string, opts: { tf?: string | null; run?: string | null; trade?: string | null } = {}) =>
    withQuery(`/markets/${q(symbol.toUpperCase())}`, { tf: opts.tf, run: opts.run, trade: opts.trade }),
  strategy: (id: string) => `/research/strategies/${q(id)}`,
  run: (runId: string) => `/research/runs/${q(runId)}`,
  trade: (tradeId: string) => withQuery("/trading/execution", { row: tradeId }),
  session: (sessionId: string) => withQuery("/trading/fleet", { session: sessionId }),
  candidate: (contentHash: string) => `/lifecycle/${q(contentHash)}`,
  incident: (id: string) => `/system/incidents/${q(id)}`,
} as const;

/** Page routes of the workstation ([param] marks a dynamic segment). */
export const ROUTE_PATTERNS = [
  "/",
  "/alerts",
  "/intelligence/agents",
  "/intelligence/research-memory",
  "/intelligence/runs",
  "/lifecycle/[hash]",
  "/login",
  "/market-pulse",
  "/markets",
  "/markets/[symbol]",
  "/markets/correlations",
  "/markets/data-quality",
  "/markets/regimes",
  "/research",
  "/research/datasets",
  "/research/experiments",
  "/research/hypotheses",
  "/research/parameter-lab",
  "/research/runs/[run_id]",
  "/research/strategies",
  "/research/strategies/[id]",
  "/research/validation",
  "/system",
  "/system/broker-health",
  "/system/data-health",
  "/system/functions",
  "/system/incidents/[id]",
  "/system/legend",
  "/system/logs",
  "/system/services",
  "/system/settings",
  "/system/workers",
  "/trading/candidates",
  "/trading/execution",
  "/trading/exposure",
  "/trading/fleet",
  "/trading/portfolio",
  "/trading/risk",
] as const;

/** True when an in-app link (path plus optional query) is one of the pages. */
export function isPageLink(link: string): boolean {
  const path = link.split(/[?#]/, 1)[0] ?? "";
  if (!path.startsWith("/") || path.startsWith("//")) return false;
  const parts = path.split("/").filter(Boolean);
  return ROUTE_PATTERNS.some((pattern) => {
    const want = pattern.split("/").filter(Boolean);
    return want.length === parts.length && want.every((w, i) => w.startsWith("[") || w === parts[i]);
  });
}
