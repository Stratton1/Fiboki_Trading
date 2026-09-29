import type { NextConfig } from "next";

const DEV = process.env.NODE_ENV !== "production";

/**
 * The API origin the browser calls (lib/api.ts). Baked in at build time.
 * An EMPTY value means same-origin: the page calls relative /api/* URLs and
 * the rewrite below proxies them (how scripts/dev-up.sh and the launchd
 * service run it). Same-origin needs no extra connect-src entry.
 */
function apiOrigin(): string | null {
  const raw = process.env.NEXT_PUBLIC_FIBOKI_API;
  if (raw === undefined) return "http://127.0.0.1:8000";
  if (raw.trim() === "") return null;
  try {
    return new URL(raw).origin;
  } catch {
    throw new Error(`NEXT_PUBLIC_FIBOKI_API is not a URL: ${raw}`);
  }
}

/**
 * Content-Security-Policy (report E §3.6).
 *
 *  - connect-src: this origin (the /api rewrite) and the API origin only.
 *  - script-src 'self' 'unsafe-inline': every page is statically prerendered,
 *    and Next inlines its RSC payload (`self.__next_f.push(...)`) and our
 *    pre-paint theme script as inline <script> tags. A nonce needs a
 *    per-request render (proxy.ts), which static pages do not have; that is
 *    the Wave 2 change, when session gating makes pages per-request anyway.
 *    Until then, 'unsafe-inline' is the honest statement. NO 'unsafe-eval'.
 *  - style-src 'self', no inline styles at all: Tailwind emits one static
 *    stylesheet, no component server-renders a style="" attribute, and Base UI
 *    positions popups through the CSSOM (which CSP does not restrict). Base
 *    UI's one inline <style> is disabled (CSPProvider) and shipped in
 *    globals.css. tests/e2e/csp.spec.ts fails on any violation on any route.
 *  - `next dev` alone adds 'unsafe-eval' (React's dev tooling) and inline
 *    styles (the dev overlay), plus websocket HMR.
 */
function contentSecurityPolicy(): string {
  const api = apiOrigin();
  const directives: Record<string, string[]> = {
    "default-src": ["'self'"],
    "script-src": ["'self'", "'unsafe-inline'", ...(DEV ? ["'unsafe-eval'"] : [])],
    "style-src": ["'self'", ...(DEV ? ["'unsafe-inline'"] : [])],
    "img-src": ["'self'", "data:"],
    "font-src": ["'self'"],
    "connect-src": ["'self'", ...(api ? [api] : []), ...(DEV ? ["ws:", "wss:"] : [])],
    "object-src": ["'none'"],
    "base-uri": ["'self'"],
    "form-action": ["'self'"],
    "frame-ancestors": ["'none'"],
    "manifest-src": ["'self'"],
    "worker-src": ["'self'"],
  };
  return Object.entries(directives)
    .map(([name, values]) => `${name} ${values.join(" ")}`)
    .join("; ");
}

/**
 * The workstation is a pure client of the Fiboki API. It holds no trading
 * logic, computes no indicator and decides no size — those live in the backend
 * packages, and the layering test in the Python suite enforces it there.
 */
const config: NextConfig = {
  reactStrictMode: true,
  poweredByHeader: false,
  // Next 16 runs lint separately; `npm run lint` and `npm run typecheck`
  // are the gates, and both are wired into the Playwright pre-flight.
  typescript: { ignoreBuildErrors: false },
  /**
   * Dev convenience: proxy the API through this origin so the workstation is
   * same-origin with it. Without this the browser must make a cross-origin
   * request from localhost:3000 to 127.0.0.1:8000, which some browsers block
   * as a private-network request regardless of CORS. Production sets
   * NEXT_PUBLIC_FIBOKI_API to the real API origin and this rewrite is unused.
   */
  async rewrites() {
    const target = process.env.FIBOKI_API_PROXY_TARGET;
    if (!target) return [];
    return [{ source: "/api/:path*", destination: `${target}/api/:path*` }];
  },
  /**
   * The plan's section URLs (plan §4), which the backend's attention queue
   * already links to (routers/command.py), mapped to where those sections
   * live today (components/shell/sections.ts). Temporary (307): Wave 4 moves
   * the screens to these URLs and these entries go. Entity links such as
   * /lifecycle/<hash> and /system/incidents/<id> have no screen yet and are
   * deliberately NOT redirected to a list that would lose the entity.
   */
  async redirects() {
    return [
      { source: "/risk", destination: "/trading/risk", permanent: false },
      { source: "/system", destination: "/system/services", permanent: false },
      { source: "/journal", destination: "/trading/execution", permanent: false },
    ];
  },
  async headers() {
    return [
      {
        source: "/:path*",
        headers: [
          { key: "Content-Security-Policy", value: contentSecurityPolicy() },
          { key: "X-Content-Type-Options", value: "nosniff" },
          { key: "X-Frame-Options", value: "DENY" },
          { key: "Referrer-Policy", value: "same-origin" },
        ],
      },
    ];
  },
};

export default config;
