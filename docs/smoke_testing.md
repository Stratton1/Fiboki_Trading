# Fiboki — Smoke Testing

## Overview

Lightweight Playwright smoke tests that confirm the deployed platform is healthy. They verify unauthenticated routing, login page rendering, and authenticated page shell loading across all stable pages.

## What is covered

| Category | Tests | Description |
|----------|-------|-------------|
| Unauthenticated routing | 7 | All protected pages redirect to `/login` without auth |
| Login page rendering | 2 | Branding, form elements, and HTML5 validation present |
| Authenticated page shells | 7 | Dashboard, charts, backtests, bots, trades, settings, system load without fatal JS errors |

**Total: 16 tests**

## What is intentionally NOT covered

- **Research page** — actively under development (Phase 8); will be added once stable
- **Real login flow** — requires production credentials; marker cookie is injected instead
- **Mutating operations** — no backtests run, bots created, or data modified
- **API response validation** — backend health is checked separately via `pytest`
- **Cross-browser** — Chromium only (sufficient for smoke verification)
- **IG/demo/live execution** — future phases

## How to run

### Prerequisites

```bash
cd frontend
npm install                    # includes @playwright/test
npx playwright install chromium  # one-time browser download
```

### Against local dev server

Start the frontend first, then run:

```bash
cd frontend
npm run dev &                  # start dev server on :3000
npm run smoke                  # runs against http://localhost:3000
```

### Against production

No local server needed:

```bash
cd frontend
npm run smoke:prod             # runs against https://fiboki.uk
```

Or with a custom URL:

```bash
BASE_URL=https://fiboki.uk npm run smoke
```

### Verbose output

```bash
npx playwright test --project=smoke --reporter=list
```

## What failures likely mean

| Failure pattern | Likely cause |
|-----------------|--------------|
| Unauthenticated routing tests fail | Middleware config changed or deployment is down |
| Login page rendering tests fail | Login page markup/branding changed |
| Authenticated page shells fail | Page component has a fatal JS error or middleware cookie name changed |
| All tests fail with network errors | Target URL is unreachable (deployment down or DNS issue) |
| Timeout errors | Deployment is slow or unresponsive |

## File locations

| File | Purpose |
|------|---------|
| `frontend/playwright.config.ts` | Playwright configuration |
| `frontend/e2e/smoke.spec.ts` | All smoke test cases |
| `frontend/package.json` | `smoke` and `smoke:prod` scripts |

## Notes

- The authenticated page shell tests inject a `fiboki_auth=1` marker cookie to bypass Next.js middleware. This does NOT create a real backend session — API calls from the pages will 401. The tests only verify that page components render their shell without crashing.
- Screenshots are captured on failure in `frontend/test-results/`.
- Adding research page smoke tests should be done once Phase 8 is complete.
