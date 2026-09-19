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

  test("no charting library is bundled", async () => {
    // V1 shipped ~4.5MB of Plotly, including mapbox-gl, to draw line charts.
    const pkg = JSON.parse(readFileSync(join(ROOT, "package.json"), "utf8"));
    const deps = Object.keys({ ...pkg.dependencies, ...pkg.devDependencies });
    for (const banned of ["plotly.js", "react-plotly.js", "mapbox-gl", "chart.js", "d3", "echarts"]) {
      expect(deps, `${banned} must not be a dependency`).not.toContain(banned);
    }
    expect(Object.keys(pkg.dependencies)).toEqual(["next", "react", "react-dom"]);
  });
});
