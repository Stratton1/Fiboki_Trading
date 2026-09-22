/**
 * screenshot-all-pages.ts
 *
 * Captures full-page screenshots of every Fiboki frontend route.
 * Auth: logs in via the /login page first, then re-uses the session cookie.
 *
 * Usage:
 *   npx ts-node scripts/screenshot-all-pages.ts
 *   # or with env overrides:
 *   BASE_URL=http://localhost:3000 EMAIL=joe@... PASSWORD=... npx ts-node scripts/screenshot-all-pages.ts
 *
 * Output: screenshots/ directory (created next to this script)
 *
 * Requirements:
 *   npm install -D playwright ts-node
 *   npx playwright install chromium
 */

import { chromium } from "playwright";
import * as fs from "fs";
import * as path from "path";
import { fileURLToPath } from "url";

const __filename = fileURLToPath(import.meta.url);
const __dirname  = path.dirname(__filename);

// ── Config ────────────────────────────────────────────────────────────────────

const BASE_URL  = process.env.BASE_URL  ?? "https://fiboki.uk";
const USERNAME  = process.env.USERNAME  ?? "joe";
const PASSWORD  = process.env.PASSWORD  ?? "";        // set via env — never hardcode
const OUT_DIR  = path.resolve(__dirname, "screenshots");
const VIEWPORT = { width: 1440, height: 900 };
const WAIT_MS  = 1800;   // ms to wait after navigation before screenshotting

// ── Page list ─────────────────────────────────────────────────────────────────
// slug → filename-friendly label
const PAGES: { route: string; label: string }[] = [
  { route: "/",              label: "01_dashboard"      },
  { route: "/charts",        label: "02_charts"         },
  { route: "/research",      label: "03_research"       },
  { route: "/backtests",     label: "04_backtests"      },
  { route: "/scenarios",     label: "05_scenarios"      },
  { route: "/jobs",          label: "06_jobs"           },
  { route: "/bots",          label: "07_bots"           },
  { route: "/analytics",     label: "08_analytics"      },
  { route: "/exposure",      label: "09_exposure"       },
  { route: "/trades",        label: "10_trades"         },
  { route: "/alerts",        label: "11_alerts"         },
  { route: "/settings",      label: "12_settings"       },
  { route: "/system",        label: "13_system"         },
];

// ── Helpers ───────────────────────────────────────────────────────────────────

function ensureOutDir() {
  if (!fs.existsSync(OUT_DIR)) fs.mkdirSync(OUT_DIR, { recursive: true });
}

function timestamp(): string {
  return new Date().toISOString().slice(0, 10); // YYYY-MM-DD
}

// ── Main ──────────────────────────────────────────────────────────────────────

(async () => {
  ensureOutDir();

  const browser = await chromium.launch({ headless: true });
  const context = await browser.newContext({ viewport: VIEWPORT });
  const page    = await context.newPage();

  // ── Login ──────────────────────────────────────────────────────────────────
  console.log(`\n🔐  Logging in as ${USERNAME} @ ${BASE_URL}`);
  await page.goto(`${BASE_URL}/login`, { waitUntil: "domcontentloaded" });

  // Wait for the login form to hydrate (Next.js SSR → client)
  await page.waitForSelector("#username", { timeout: 20_000 });

  // Fill credentials — login form uses id="username" (text) + id="password"
  await page.fill("#username", USERNAME);
  await page.fill("#password", PASSWORD);
  await page.click('button[type="submit"]');

  // Wait until we land on an authenticated page
  await page.waitForURL((url) => !url.pathname.startsWith("/login"), { timeout: 20_000 });
  console.log("✅  Logged in\n");

  const date = timestamp();
  const errors: string[] = [];

  // ── Screenshot each route ──────────────────────────────────────────────────
  for (const { route, label } of PAGES) {
    const url = `${BASE_URL}${route}`;
    const filename = `${date}_${label}.png`;
    const outPath  = path.join(OUT_DIR, filename);

    process.stdout.write(`  ${label.padEnd(22)} → `);
    try {
      await page.goto(url, { waitUntil: "domcontentloaded", timeout: 20_000 });
      // Extra wait for SWR data / charts to settle
      await page.waitForTimeout(WAIT_MS);
      await page.screenshot({ path: outPath, fullPage: true });
      console.log(`✓  ${filename}`);
    } catch (err) {
      const msg = err instanceof Error ? err.message : String(err);
      console.log(`✗  FAILED — ${msg}`);
      errors.push(`${label}: ${msg}`);
    }
  }

  // ── Summary ───────────────────────────────────────────────────────────────
  console.log(`\n📸  Screenshots saved to: ${OUT_DIR}`);
  if (errors.length) {
    console.log(`\n⚠️  ${errors.length} page(s) failed:`);
    errors.forEach((e) => console.log(`   • ${e}`));
  } else {
    console.log("✅  All pages captured successfully.");
  }

  await browser.close();
})();
