#!/usr/bin/env npx tsx
/**
 * Fiboki API Smoke Check
 *
 * Hits key production API endpoints and reports pass/fail.
 * Does NOT require authentication for health/system checks.
 * Requires FIBOKI_E2E_USERNAME + FIBOKI_E2E_PASSWORD for auth checks.
 *
 * Run:
 *   npx tsx frontend/e2e/api-smoke.ts
 *   API_URL=https://api.fiboki.uk npx tsx frontend/e2e/api-smoke.ts
 */

const API_URL = process.env.API_URL || "https://api.fiboki.uk";
const USERNAME = process.env.FIBOKI_E2E_USERNAME;
const PASSWORD = process.env.FIBOKI_E2E_PASSWORD;

interface CheckResult {
  name: string;
  status: "pass" | "fail" | "skip";
  detail: string;
  ms: number;
}

const results: CheckResult[] = [];

async function check(name: string, fn: () => Promise<string>): Promise<void> {
  const start = Date.now();
  try {
    const detail = await fn();
    results.push({ name, status: "pass", detail, ms: Date.now() - start });
  } catch (err) {
    const detail = err instanceof Error ? err.message : String(err);
    results.push({ name, status: "fail", detail, ms: Date.now() - start });
  }
}

function skip(name: string, reason: string) {
  results.push({ name, status: "skip", detail: reason, ms: 0 });
}

async function fetchJson(path: string, options?: RequestInit) {
  const res = await fetch(`${API_URL}/api/v1${path}`, {
    ...options,
    signal: AbortSignal.timeout(10_000),
  });
  if (!res.ok) throw new Error(`${res.status} ${res.statusText}`);
  return res.json();
}

// ── Checks ────────────────────────────────────────────────────────

async function run() {
  console.log(`\nFiboki API Smoke Check — ${API_URL}\n`);

  // Health
  await check("Health endpoint", async () => {
    const data = await fetchJson("/system/health");
    if (data.status !== "ok") throw new Error(`status=${data.status}`);
    return `ok — v${data.version}`;
  });

  // System status
  await check("System status", async () => {
    const data = await fetchJson("/system/status");
    return `db=${data.database}, engine=${data.paper_engine}, strategies=${data.strategies_loaded}`;
  });

  // Instruments
  await check("Instruments", async () => {
    const data = await fetchJson("/instruments/");
    if (!Array.isArray(data)) throw new Error("Expected array");
    const canonical = data.filter((i: any) => i.has_canonical_data).length;
    return `${data.length} instruments (${canonical} canonical)`;
  });

  // Strategies
  await check("Strategies", async () => {
    const data = await fetchJson("/strategies/");
    if (!Array.isArray(data)) throw new Error("Expected array");
    return `${data.length} strategies`;
  });

  // Authenticated checks
  if (USERNAME && PASSWORD) {
    // Login
    let token: string | null = null;
    await check("Login", async () => {
      const body = new URLSearchParams();
      body.append("username", USERNAME);
      body.append("password", PASSWORD);
      const res = await fetch(`${API_URL}/api/v1/auth/login`, {
        method: "POST",
        headers: { "Content-Type": "application/x-www-form-urlencoded" },
        body,
        signal: AbortSignal.timeout(10_000),
      });
      if (!res.ok) throw new Error(`${res.status} ${res.statusText}`);
      const data = await res.json();
      if (!data.access_token) throw new Error("No access_token in response");
      token = data.access_token;
      return `token received`;
    });

    if (token) {
      const authFetch = (path: string) =>
        fetch(`${API_URL}/api/v1${path}`, {
          headers: { Authorization: `Bearer ${token}` },
          signal: AbortSignal.timeout(10_000),
        }).then(async (res) => {
          if (!res.ok) throw new Error(`${res.status} ${res.statusText}`);
          return res.json();
        });

      // /auth/me
      await check("Auth: /me", async () => {
        const data = await authFetch("/auth/me");
        return `${data.username} (${data.role})`;
      });

      // Paper account
      await check("Paper account", async () => {
        const data = await authFetch("/paper/account");
        return `balance=$${data.balance}, equity=$${data.equity}`;
      });

      // Market data check (EURUSD/H1)
      await check("Market data: EURUSD/H1", async () => {
        const data = await authFetch("/market-data/EURUSD/H1");
        const candles = data.candles?.length ?? 0;
        if (candles === 0) throw new Error("No candles returned — starter data not available");
        return `${candles} candles`;
      });

      // Backtests list
      await check("Backtests list", async () => {
        const data = await authFetch("/backtests");
        return `${Array.isArray(data) ? data.length : 0} backtests`;
      });

      // Research rankings
      await check("Research rankings", async () => {
        const data = await authFetch("/research/rankings");
        return `${Array.isArray(data) ? data.length : 0} rankings`;
      });
    }
  } else {
    skip("Login", "FIBOKI_E2E_USERNAME / FIBOKI_E2E_PASSWORD not set");
    skip("Auth: /me", "No credentials");
    skip("Paper account", "No credentials");
    skip("Market data: EURUSD/H1", "No credentials");
    skip("Backtests list", "No credentials");
    skip("Research rankings", "No credentials");
  }

  // ── Report ──────────────────────────────────────────────────────

  console.log("─".repeat(72));
  let passed = 0, failed = 0, skipped = 0;
  for (const r of results) {
    const icon = r.status === "pass" ? "✓" : r.status === "fail" ? "✗" : "○";
    const colour = r.status === "pass" ? "\x1b[32m" : r.status === "fail" ? "\x1b[31m" : "\x1b[33m";
    const ms = r.ms > 0 ? ` (${r.ms}ms)` : "";
    console.log(`${colour}${icon}\x1b[0m  ${r.name}${ms}`);
    console.log(`   ${r.detail}`);
    if (r.status === "pass") passed++;
    else if (r.status === "fail") failed++;
    else skipped++;
  }
  console.log("─".repeat(72));
  console.log(`\nTotal: ${passed} passed, ${failed} failed, ${skipped} skipped\n`);

  if (failed > 0) process.exit(1);
}

run().catch((err) => {
  console.error("Fatal error:", err);
  process.exit(1);
});
