"use client";

import { parseAsString, parseAsStringLiteral, useQueryState } from "nuqs";
import { useRouter } from "next/navigation";
import { WatchlistMonitor } from "@/components/monitors/WatchlistMonitor";
import { PageHead } from "@/components/primitives";
import { UrlState } from "@/components/UrlState";
import { QUOTE_WINDOWS } from "@/lib/monitors";
import { route } from "@/lib/routes";

/**
 * MARKETS · Watchlist (WL): saved lists over the executable-price recorder's
 * quotes. `?list=<name>&window=1h|4h|24h`. Enter or double-click on a row
 * opens the instrument's chart.
 */
export default function WatchlistPage() {
  return (
    <>
      <PageHead
        title="Watchlist"
        intro="Your saved lists (kept on the server, versioned) over the quotes the recorder captured: OANDA practice top of book, polled. Each row shows the latest recorded quote, the change, range and a sparkline over the window, the session, and how old the data is with the platform's verdict: LIVE, STALE (session open, no recent quote), CLOSED or NO DATA. Colours are the platform's rules (Colours button). Column sets save to your profile."
      />
      <UrlState fallback={<p className="muted" role="status">Loading the watchlist…</p>}>
        <WatchlistScreen />
      </UrlState>
    </>
  );
}

function WatchlistScreen() {
  const router = useRouter();
  const [list, setList] = useQueryState("list", parseAsString.withDefault("Majors").withOptions({ history: "replace" }));
  const [window, setWindow] = useQueryState(
    "window",
    parseAsStringLiteral(QUOTE_WINDOWS).withDefault("24h").withOptions({ history: "replace" }),
  );
  return (
    <WatchlistMonitor
      list={list}
      onList={(n) => void setList(n)}
      window={window}
      onWindow={(w) => void setWindow(w)}
      onSymbol={(s) => router.push(route.instrument(s))}
    />
  );
}
