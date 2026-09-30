/**
 * First-load JavaScript per route, measured from the production build.
 *
 * `next build` (Turbopack, Next 16) no longer prints a "First Load JS" column,
 * so this reads what the browser actually downloads: every route is statically
 * prerendered to `.next/server/app/<route>.html`, and the module `<script src>`
 * tags in that HTML are the route's first-load JavaScript. `noModule`
 * polyfills are excluded (modern browsers skip them). Each chunk is gzipped on
 * its own, as it is served, and the sizes are summed.
 *
 * Used by `.size-limit.cjs` (the CI budget gate), by
 * `tests/e2e/source-rules.spec.ts` and by `npm run size:report`.
 */
import { existsSync, readFileSync, readdirSync, statSync } from "node:fs";
import { dirname, join, relative, sep } from "node:path";
import { fileURLToPath } from "node:url";
import { gzipSync } from "node:zlib";

const HERE = dirname(fileURLToPath(import.meta.url));
export const WEB_ROOT = join(HERE, "..");
const NEXT = join(WEB_ROOT, ".next");
const APP_HTML = join(NEXT, "server", "app");

/** Budgets in KiB (gzip), first-load JS. Report E §3.7 and plan D-F7. */
export const SHELL_BUDGET_KB = 180;
export const DEFAULT_BUDGET_KB = 230;
/** Routes held to the shell budget: the shell itself and the Overview. */
export const SHELL_ROUTES = new Set(["/"]);

export function budgetFor(route) {
  return SHELL_ROUTES.has(route) ? SHELL_BUDGET_KB : DEFAULT_BUDGET_KB;
}

function htmlFiles(dir, acc = []) {
  for (const entry of readdirSync(dir)) {
    const full = join(dir, entry);
    if (statSync(full).isDirectory()) htmlFiles(full, acc);
    else if (entry.endsWith(".html")) acc.push(full);
  }
  return acc;
}

function routeOf(file) {
  const rel = relative(APP_HTML, file)
    .split(sep)
    .join("/")
    .replace(/\.html$/, "");
  if (rel === "index") return "/";
  return `/${rel}`;
}

/** Module script chunks referenced by a prerendered page, in document order. */
export function scriptsIn(html) {
  const out = [];
  const tag = /<script\b([^>]*)>/g;
  let match;
  while ((match = tag.exec(html)) !== null) {
    const attrs = match[1] ?? "";
    if (/\bnoModule\b/i.test(attrs)) continue;
    const src = /\bsrc="([^"]+)"/.exec(attrs);
    if (src?.[1]?.startsWith("/_next/")) out.push(src[1]);
  }
  return out;
}

export function stylesheetsIn(html) {
  const out = [];
  const tag = /<link\b([^>]*)>/g;
  let match;
  while ((match = tag.exec(html)) !== null) {
    const attrs = match[1] ?? "";
    if (!/rel="stylesheet"/.test(attrs)) continue;
    const href = /\bhref="([^"]+)"/.exec(attrs);
    if (href?.[1]?.startsWith("/_next/")) out.push(href[1]);
  }
  return out;
}

export function assetPath(url) {
  return join(NEXT, url.replace(/^\/_next\//, ""));
}

const gzCache = new Map();
export function gzipBytes(file) {
  const hit = gzCache.get(file);
  if (hit !== undefined) return hit;
  const size = gzipSync(readFileSync(file), { level: 9 }).length;
  gzCache.set(file, size);
  return size;
}

/**
 * The route PATTERN each prerendered path belongs to, from Next's own
 * prerender manifest: `/markets/EURUSD` → `/markets/[symbol]`. A dynamic
 * route is measured through the page(s) its generateStaticParams prerendered
 * (their first-load JavaScript is the route's; only the data differs).
 */
function srcRoutes() {
  const file = join(NEXT, "prerender-manifest.json");
  if (!existsSync(file)) return { get: () => undefined };
  const manifest = JSON.parse(readFileSync(file, "utf8"));
  const exact = new Map();
  for (const [path, entry] of Object.entries(manifest.routes ?? {})) {
    if (entry && typeof entry.srcRoute === "string") exact.set(path, entry.srcRoute);
  }
  // A dynamic route's pages rendered ON DEMAND by a running server (a test
  // visiting /markets/GBPUSD) land in the same cache directory without a
  // manifest entry; they belong to their pattern just the same.
  const dynamic = Object.entries(manifest.dynamicRoutes ?? {}).map(([pattern, entry]) => ({
    pattern,
    regex: new RegExp(entry.routeRegex),
  }));
  return {
    get(path) {
      const hit = exact.get(path);
      if (hit) return hit;
      const match = dynamic.find((d) => d.regex.test(path));
      return match ? match.pattern : undefined;
    },
  };
}

/**
 * One row per route pattern: its first-load JS files and their gzip total.
 * Framework-internal routes (`/_not-found`, `/_global-error`) are omitted.
 * `sample` is the prerendered path measured when it differs from the pattern;
 * when several paths of one pattern were prerendered the largest is kept.
 */
export function measureRoutes() {
  if (!existsSync(APP_HTML)) {
    throw new Error(`No production build at ${APP_HTML}. Run \`npm run build\` first.`);
  }
  const patterns = srcRoutes();
  const rows = htmlFiles(APP_HTML)
    .map((file) => {
      const path = routeOf(file);
      const route = patterns.get(path) ?? path;
      const html = readFileSync(file, "utf8");
      const js = scriptsIn(html).map(assetPath);
      const css = stylesheetsIn(html).map(assetPath);
      const jsGzip = js.reduce((sum, f) => sum + gzipBytes(f), 0);
      const cssGzip = css.reduce((sum, f) => sum + gzipBytes(f), 0);
      const sample = route === path ? null : path;
      return { route, sample, js, css, jsGzip, cssGzip, budgetKb: budgetFor(route) };
    })
    .filter((row) => !row.route.startsWith("/_"));
  const byRoute = new Map();
  for (const row of rows) {
    const seen = byRoute.get(row.route);
    if (seen === undefined || row.jsGzip > seen.jsGzip) byRoute.set(row.route, row);
  }
  return [...byRoute.values()].sort((a, b) => a.route.localeCompare(b.route));
}

const kb = (bytes) => (bytes / 1024).toFixed(1);

export function formatTable(rows) {
  const lines = [
    "| Route | First-load JS (gzip KiB) | Budget | CSS (gzip KiB) | Within |",
    "|---|---:|---:|---:|---|",
  ];
  for (const r of rows) {
    const within = r.jsGzip / 1024 <= r.budgetKb ? "yes" : "NO";
    const route = r.sample ? `\`${r.route}\` (as \`${r.sample}\`)` : `\`${r.route}\``;
    lines.push(`| ${route} | ${kb(r.jsGzip)} | ${r.budgetKb} | ${kb(r.cssGzip)} | ${within} |`);
  }
  return lines.join("\n");
}

if (process.argv[1] && fileURLToPath(import.meta.url) === process.argv[1]) {
  const rows = measureRoutes();
  console.log(formatTable(rows));
  const over = rows.filter((r) => r.jsGzip / 1024 > r.budgetKb);
  if (over.length > 0) {
    console.error(`\n${over.length} route(s) over budget.`);
    process.exitCode = 1;
  }
}
