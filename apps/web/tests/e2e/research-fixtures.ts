import { readFileSync } from "node:fs";
import { join } from "node:path";
import type { Page, Route } from "@playwright/test";
import { API } from "./fixtures";
import { mockRobustness } from "./robustness-fixtures";

/**
 * Research analytics API fixtures. The JSON beside this file
 * (research-fixtures/*.json) was produced by the REAL backend
 * (research-fixtures/generate.py runs engine backtests through the worker's
 * job handlers and answers each route), so a spec drives the screens with
 * payloads the API can actually send.
 */
const DIR = join(__dirname, "research-fixtures");

export function researchFixture<T = Record<string, unknown>>(name: string): T {
  return JSON.parse(readFileSync(join(DIR, `${name}.json`), "utf8")) as T;
}

/** Route every research analytics read to its fixture. */
export async function mockResearch(page: Page) {
  const fx = (name: string) => (route: Route) => route.fulfill({ json: researchFixture(name) });
  const at = (path: (p: string) => boolean, name: string) =>
    page.route((url) => url.origin === API && path(url.pathname), fx(name));
  await at((p) => p === "/api/research/strategies", "strategies");
  await at((p) => p === "/api/research/validation", "validation");
  await at((p) => p === "/api/research/runs", "runs");
  await at((p) => /^\/api\/research\/runs\/[^/]+\/tearsheet$/.test(p), "tearsheet");
  await at((p) => /^\/api\/research\/runs\/[^/]+\/monte-carlo$/.test(p), "monte_carlo");
  await at((p) => p === "/api/research/compare", "compare");
  await at((p) => /^\/api\/research\/strategies\/[^/]+\/overview$/.test(p), "overview");
  await at((p) => /^\/api\/research\/strategies\/[^/]+\/walk-forward$/.test(p), "walk_forward");
  await at((p) => /^\/api\/research\/parameter-lab\/[^/]+\/surface$/.test(p), "surface");
  await at((p) => /^\/api\/research\/parameter-lab\/[^/]+$/.test(p), "parameter_lab");
  await at((p) => p === "/api/research/jobs", "jobs");
  await at((p) => p === "/api/research/correlations", "correlations");
  // wave 3R: the robustness panels on the tearsheet and the strategy page.
  await mockRobustness(page);
}
