import type { NextConfig } from "next";

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
  async headers() {
    return [
      {
        source: "/:path*",
        headers: [
          { key: "X-Content-Type-Options", value: "nosniff" },
          { key: "X-Frame-Options", value: "DENY" },
          { key: "Referrer-Policy", value: "same-origin" },
        ],
      },
    ];
  },
};

export default config;
