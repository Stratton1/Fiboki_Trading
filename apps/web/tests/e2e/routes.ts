import { readdirSync, statSync } from "node:fs";
import { join } from "node:path";

/**
 * A representative value for each dynamic segment, so a sweep can visit a
 * dynamic page (`/markets/[symbol]` → `/markets/EURUSD`). A new dynamic
 * segment without an entry here fails loudly rather than being skipped.
 */
export const SAMPLE_PARAMS: Readonly<Record<string, string>> = {
  symbol: "EURUSD",
  // /system/incidents/[id]: the incident the shared fixtures return.
  id: "inc-1",
  // /lifecycle/[hash]: the content hash the fixtures' strategy-review item links to.
  hash: "abc123def456",
};

/** `/markets/[symbol]` → `/markets/EURUSD`. */
export function concreteRoute(pattern: string): string {
  return pattern.replace(/\[([^\]]+)\]/g, (_, name: string) => {
    const value = SAMPLE_PARAMS[name];
    if (value === undefined) {
      throw new Error(`tests/e2e/routes.ts: no sample value for the dynamic segment [${name}]`);
    }
    return value;
  });
}

/** Every page route in app/, as its pattern (`/markets/[symbol]`), found on disk. */
export function routePatterns(): string[] {
  const root = join(__dirname, "..", "..", "app");
  const out: string[] = [];
  const walk = (dir: string, prefix: string) => {
    for (const entry of readdirSync(dir)) {
      const full = join(dir, entry);
      if (statSync(full).isDirectory()) walk(full, `${prefix}/${entry}`);
      else if (entry === "page.tsx") out.push(prefix === "" ? "/" : prefix);
    }
  };
  walk(root, "");
  return out.sort();
}

/**
 * Every page route in app/, found on disk so a new page cannot escape the
 * sweeps, as a URL a browser can visit (dynamic segments filled with their
 * sample values).
 */
export function allRoutes(): string[] {
  return routePatterns().map(concreteRoute);
}
