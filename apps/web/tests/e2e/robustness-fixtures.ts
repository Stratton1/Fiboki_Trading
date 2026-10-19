import { readFileSync } from "node:fs";
import { join } from "node:path";
import type { Page, Route } from "@playwright/test";
import { API } from "./fixtures";

/**
 * Robustness diagnostics API fixtures (wave 3R). The JSON beside this file
 * (robustness-fixtures/*.json) was produced by the REAL backend
 * (robustness-fixtures/generate.py queues every re-run diagnostic through the
 * API, runs it with the research worker's handler and reads every route back).
 */
const DIR = join(__dirname, "robustness-fixtures");

export function robustnessFixture<T = Record<string, unknown>>(name: string): T {
  return JSON.parse(readFileSync(join(DIR, `${name}.json`), "utf8")) as T;
}

const LEDGER = new Set(["bands", "breakdowns", "consistency", "fills"]);

/**
 * Route every robustness read to its fixture. `state` picks the re-run fixture
 * set: `done` (computed), `pending` (queued, not run) or `not_run`.
 */
export async function mockRobustness(page: Page, state: "done" | "pending" | "not_run" = "done") {
  const fx = (name: string) => (route: Route) => route.fulfill({ json: robustnessFixture(name) });
  await page.route(
    (url) => url.origin === API && /^\/api\/research\/runs\/[^/]+\/robustness$/.test(url.pathname),
    fx(state === "done" ? "index" : "index_not_run"),
  );
  await page.route(
    (url) => url.origin === API && /^\/api\/research\/runs\/[^/]+\/robustness\/[a-z]+$/.test(url.pathname),
    (route: Route) => {
      const what = new URL(route.request().url()).pathname.split("/").pop()!;
      if (route.request().method() === "POST") {
        const pending = robustnessFixture<{ data: { status: unknown } }>("rerun_pending");
        return route.fulfill({ status: 202, json: { ...pending, data: pending.data.status } });
      }
      if (LEDGER.has(what)) return route.fulfill({ json: robustnessFixture(what) });
      if (state === "done") return route.fulfill({ json: robustnessFixture(`rerun_${what}`) });
      return route.fulfill({ json: robustnessFixture(state === "pending" ? "rerun_pending" : "rerun_not_run") });
    },
  );
  await page.route(
    (url) => url.origin === API && /^\/api\/research\/strategies\/[^/]+\/optimisation-profile$/.test(url.pathname),
    fx("profile"),
  );
  await page.route((url) => url.origin === API && url.pathname === "/api/research/rankings", fx("rankings"));
}
