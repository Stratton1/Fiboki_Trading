"use client";

import { parseAsStringLiteral, useQueryState } from "nuqs";
import { PulseMonitor } from "@/components/monitors/PulseMonitor";
import { PageHead } from "@/components/primitives";
import { UrlState } from "@/components/UrlState";
import { QUOTE_WINDOWS } from "@/lib/monitors";

/**
 * COMMAND · Market Pulse (PULSE): movers, currency strength, volatility and
 * the session clocks over every recorded instrument (programme P1 13), from
 * GET /api/markets/pulse. `?window=1h|4h|24h`.
 */
export default function MarketPulsePage() {
  return (
    <>
      <PageHead
        title="Market Pulse"
        intro="What the recorded universe did over the window: the biggest movers, a currency strength map, realised volatility and the trading sessions, all computed by the platform from the quotes the recorder captured (OANDA practice top of book, polled). An instrument with no recorded quote in the window is not in the pulse; the stamp says how many are. Definitions are at the foot."
      />
      <UrlState fallback={<p className="muted" role="status">Loading Market Pulse…</p>}>
        <PulseScreen />
      </UrlState>
    </>
  );
}

function PulseScreen() {
  const [window, setWindow] = useQueryState(
    "window",
    parseAsStringLiteral(QUOTE_WINDOWS).withDefault("24h").withOptions({ history: "replace" }),
  );
  return <PulseMonitor window={window} onWindow={(w) => void setWindow(w)} />;
}
