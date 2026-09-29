import { readFileSync, readdirSync, statSync } from "node:fs";
import { join } from "node:path";
import { expect, test } from "@playwright/test";

/**
 * Source-level rules, greppped.
 *
 * The ESLint rule in eslint.config.mjs is the primary gate. This is the second
 * one, because a lint rule can be disabled inline and a CI step can be skipped,
 * and `data?.x ?? 0` is the single line of code that made a V1 outage look like
 * a healthy idle fleet.
 */

const ROOT = join(__dirname, "..", "..");
const SKIP = new Set(["node_modules", ".next", "out", "test-results", "playwright-report"]);

/**
 * Strip comments before matching.
 *
 * These rules ban PATTERNS IN CODE. The modules under test document the V1
 * failures they close, and those comments quote the banned patterns verbatim —
 * `data?.x ?? 0`, "Paper trading only", a native `confirm()`. Matching comment
 * text would make the codebase unable to explain itself, so the scan sees only
 * executable source.
 */
function stripComments(text: string): string {
  return text
    .replace(/\/\*[\s\S]*?\*\//g, " ")
    .replace(/(^|[^:])\/\/.*$/gm, "$1");
}

function sources(dir: string, acc: string[] = []): string[] {
  for (const entry of readdirSync(dir)) {
    if (SKIP.has(entry)) continue;
    const full = join(dir, entry);
    if (statSync(full).isDirectory()) sources(full, acc);
    else if (/\.(ts|tsx)$/.test(entry) && !full.includes(join("tests", "e2e"))) acc.push(full);
  }
  return acc;
}

test.describe("source rules", () => {
  test("no file coalesces a missing value to zero", async () => {
    const offenders: string[] = [];
    for (const file of sources(ROOT)) {
      const text = stripComments(readFileSync(file, "utf8"));
      text.split("\n").forEach((line, index) => {
        if (/\?\?\s*0\b/.test(line) || /\|\|\s*0\b/.test(line)) {
          offenders.push(`${file.replace(ROOT, "")}:${index + 1}: ${line.trim()}`);
        }
      });
    }
    expect(
      offenders,
      "A missing number must render as an explicit 'no data' state, never as 0.",
    ).toEqual([]);
  });

  test("no page hardcodes a realism caveat", async () => {
    // Caveats are computed server-side and returned with the data they qualify.
    // V1 shipped "Estimated realistic return: 190–230%" as static page copy.
    const banned = [
      /Estimated realistic return/i,
      /realistic return:\s*\d/i,
      /\d+\s*[–-]\s*\d+\s*%\s*(expected|realistic)/i,
    ];
    const offenders: string[] = [];
    for (const file of sources(ROOT)) {
      const text = stripComments(readFileSync(file, "utf8"));
      for (const pattern of banned) {
        if (pattern.test(text)) offenders.push(`${file.replace(ROOT, "")} matched ${pattern}`);
      }
    }
    expect(offenders).toEqual([]);
  });

  test("no page hardcodes an execution-mode claim", async () => {
    // V1's bot-creation copy said "Paper trading only — no live execution" in
    // every mode. Mode copy comes from /api/system/execution-mode or nowhere.
    const offenders: string[] = [];
    for (const file of sources(ROOT)) {
      if (file.includes(join("lib", "types.ts"))) continue;
      const text = stripComments(readFileSync(file, "utf8"));
      if (/Paper trading only/i.test(text)) {
        offenders.push(`${file.replace(ROOT, "")} hardcodes a mode claim`);
      }
    }
    expect(offenders).toEqual([]);
  });

  test("no page uses a native confirm() or alert()", async () => {
    const offenders: string[] = [];
    for (const file of sources(ROOT)) {
      const text = stripComments(readFileSync(file, "utf8"));
      if (/(?<![.\w])(window\.)?(confirm|alert)\s*\(/.test(text)) {
        offenders.push(file.replace(ROOT, ""));
      }
    }
    expect(
      offenders,
      "Every destructive action goes through the one shared ConfirmDialog.",
    ).toEqual([]);
  });

  test("no code invents a provenance with a fallback", async () => {
    // `page.items[0]?.provenance ?? "backtest"` labelled a MIXED trade list with
    // its first row's provenance, or with "backtest" when empty. Aggregates
    // derive their label from the data (lib/provenance.ts deriveProvenance).
    const names =
      "backtest|walkforward|out_of_sample|holdout|paper|shadow|broker_demo|broker_live";
    const fallback = new RegExp(`(\\?\\?|\\|\\|)\\s*["'\`](${names})["'\`]`);
    const offenders: string[] = [];
    for (const file of sources(ROOT)) {
      const text = stripComments(readFileSync(file, "utf8"));
      text.split("\n").forEach((line, index) => {
        if (fallback.test(line)) {
          offenders.push(`${file.replace(ROOT, "")}:${index + 1}: ${line.trim()}`);
        }
      });
    }
    expect(
      offenders,
      "A provenance must come from the data. When there is none, render 'unlabelled source'.",
    ).toEqual([]);
  });

  test("no page hard-codes a provenance literal", async () => {
    // market-pulse and correlations passed provenance="backtest" to their
    // charts although neither payload is a backtest result.
    const literal = /(?<![-\w])provenance\s*=\s*\{?\s*["'`][a-z_]+["'`]/;
    const offenders: string[] = [];
    for (const file of sources(join(ROOT, "app"))) {
      const text = stripComments(readFileSync(file, "utf8"));
      text.split("\n").forEach((line, index) => {
        if (literal.test(line)) {
          offenders.push(`${file.replace(ROOT, "")}:${index + 1}: ${line.trim()}`);
        }
      });
    }
    expect(offenders).toEqual([]);
  });

  test("no React key is random", async () => {
    // intelligence/runs used Math.random() as a row key, remounting every row
    // on every render and defeating reconciliation.
    const offenders: string[] = [];
    for (const file of sources(ROOT)) {
      const text = stripComments(readFileSync(file, "utf8"));
      if (/Math\.random\s*\(/.test(text)) offenders.push(file.replace(ROOT, ""));
    }
    expect(offenders).toEqual([]);
  });

  test("the rules above would catch the patterns they ban", async () => {
    // Guard against a regex that silently matches nothing.
    const names = "backtest|paper";
    const fallback = new RegExp(`(\\?\\?|\\|\\|)\\s*["'\`](${names})["'\`]`);
    expect(fallback.test('provenance={page.items[0]?.provenance ?? "backtest"}')).toBe(true);
    expect(fallback.test("x?.exposure_pct.provenance ?? 'paper'")).toBe(true);
    const literal = /(?<![-\w])provenance\s*=\s*\{?\s*["'`][a-z_]+["'`]/;
    expect(literal.test('                provenance="backtest"')).toBe(true);
    expect(literal.test('data-provenance="mixed"')).toBe(false);
  });

  test("no charting library is bundled", async () => {
    // V1 shipped ~4.5MB of Plotly, including mapbox-gl, to draw line charts.
    // Checked in package.json AND the lockfile, so a transitive copy fails too.
    const banned = ["plotly.js", "react-plotly.js", "mapbox-gl", "chart.js", "d3", "echarts"];
    const pkg = JSON.parse(readFileSync(join(ROOT, "package.json"), "utf8"));
    const deps = Object.keys({ ...pkg.dependencies, ...pkg.devDependencies });
    for (const name of banned) {
      expect(deps, `${name} must not be a dependency`).not.toContain(name);
    }
    const lock = JSON.parse(readFileSync(join(ROOT, "package-lock.json"), "utf8"));
    const installed = Object.keys(lock.packages ?? {}).map((key) =>
      key.replace(/^.*node_modules\//, ""),
    );
    for (const name of banned) {
      expect(installed, `${name} must not be installed, even transitively`).not.toContain(name);
    }
  });

  test("runtime dependencies are an explicit allow-list", async () => {
    // Replaces "exactly three dependencies" (report E §3.7, plan D-F7). Each
    // entry is a reviewed decision; adding a runtime dependency means adding
    // it here, in the same change, and staying inside the byte budgets below.
    const ALLOWED: Record<string, string> = {
      next: "framework (D-F1)",
      react: "framework (D-F1)",
      "react-dom": "framework (D-F1)",
      "@base-ui/react": "accessible primitives under components/ui (D-F2)",
      "lucide-react": "icons, tree-shaken",
      "@fontsource-variable/inter": "self-hosted UI font, OFL (D-F9)",
      "@fontsource-variable/jetbrains-mono": "self-hosted mono font, OFL (D-F9)",
      "react-resizable-panels": "SplitPane (D-F8)",
    };
    const pkg = JSON.parse(readFileSync(join(ROOT, "package.json"), "utf8"));
    const runtime = Object.keys(pkg.dependencies).sort();
    expect(runtime, "every runtime dependency must be on the allow-list").toEqual(
      Object.keys(ALLOWED).sort(),
    );
  });

  test("every route's first-load JavaScript is within its gzip budget", async () => {
    // Shell and Overview <= 180 KiB; every other route <= 230 KiB for now.
    // Measured from the production build this suite serves (the prerendered
    // HTML's module scripts, gzipped one by one); `npm run size` enforces the
    // same budgets through size-limit.
    const { measureRoutes } = await import("../../scripts/first-load.mjs");
    const rows: { route: string; jsGzip: number; budgetKb: number }[] = measureRoutes();

    // No route escapes measurement: every app page must appear.
    const pages: string[] = [];
    const walk = (dir: string, prefix: string) => {
      for (const entry of readdirSync(dir)) {
        const full = join(dir, entry);
        if (statSync(full).isDirectory()) walk(full, `${prefix}/${entry}`);
        else if (entry === "page.tsx") pages.push(prefix === "" ? "/" : prefix);
      }
    };
    walk(join(ROOT, "app"), "");
    expect(rows.map((r) => r.route).sort()).toEqual(pages.sort());

    const over = rows
      .filter((r) => r.jsGzip / 1024 > r.budgetKb)
      .map((r) => `${r.route}: ${(r.jsGzip / 1024).toFixed(1)} KiB > ${r.budgetKb} KiB`);
    expect(over, "first-load JS over budget").toEqual([]);
    const shell = rows.find((r) => r.route === "/");
    expect(shell?.budgetKb).toBe(180);
  });
});
