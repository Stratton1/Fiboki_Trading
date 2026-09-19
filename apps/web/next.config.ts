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
