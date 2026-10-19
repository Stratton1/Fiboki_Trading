"use client";

import { parseAsArrayOf, parseAsInteger, parseAsString, useQueryStates } from "nuqs";
import { NewsMonitor } from "@/components/monitors/NewsMonitor";
import { PageHead } from "@/components/primitives";
import { UrlState } from "@/components/UrlState";

/**
 * MARKETS · News monitor (NWS; `EURUSD NEWS` on the command line):
 * `?instrument=&currency=&source=&hours=`.
 */
export default function NewsPage() {
  return (
    <>
      <PageHead
        title="News"
        intro="Headlines the recorder captured, newest first by the time WE first saw them (the point-in-time key), never by the vendor's timestamp. Filter by instrument, currency or source; central-bank feeds are tagged with their currency, vendor feeds are not and pass every currency filter (said so). Each headline links to the charts of the instruments its currency moves; the chart draws the same headlines. Nothing here is classified or scored."
      />
      <UrlState fallback={<p className="muted" role="status">Loading the news monitor…</p>}>
        <NewsScreen />
      </UrlState>
    </>
  );
}

function NewsScreen() {
  const [q, setQ] = useQueryStates(
    {
      instrument: parseAsString,
      currency: parseAsArrayOf(parseAsString).withDefault([]),
      source: parseAsString,
      hours: parseAsInteger.withDefault(24),
    },
    { history: "replace" },
  );
  return (
    <NewsMonitor
      filter={{ instrument: q.instrument, currencies: q.currency, source: q.source, hours: q.hours }}
      onFilter={(patch) =>
        void setQ({
          ...(patch.instrument !== undefined ? { instrument: patch.instrument } : {}),
          ...(patch.currencies !== undefined ? { currency: patch.currencies } : {}),
          ...(patch.source !== undefined ? { source: patch.source } : {}),
          ...(patch.hours !== undefined ? { hours: patch.hours } : {}),
        })
      }
    />
  );
}
