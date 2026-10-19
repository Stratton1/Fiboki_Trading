"use client";

import { parseAsArrayOf, parseAsInteger, parseAsString, useQueryStates } from "nuqs";
import { CalendarMonitor } from "@/components/monitors/CalendarMonitor";
import { PageHead } from "@/components/primitives";
import { UrlState } from "@/components/UrlState";

/** MARKETS · Economic calendar (ECO; `EURUSD CAL`): `?impact=&currency=&instrument=&days=`. */
export default function CalendarPage() {
  return (
    <>
      <PageHead
        title="Economic calendar"
        intro="Scheduled official releases (central-bank decisions, payrolls, CPI, UK data) from the newest version of the committed official calendar, the version paper trading reads. Research and backtests pin an older version and are not affected by this screen. Filter by impact and currency; each event opens the chart at that event. The calendar covers USD, EUR, GBP and JPY only, and is complete only to its declared end: anything else is unknown, said so."
      />
      <UrlState fallback={<p className="muted" role="status">Loading the calendar…</p>}>
        <CalendarScreen />
      </UrlState>
    </>
  );
}

function CalendarScreen() {
  const [q, setQ] = useQueryStates(
    {
      impact: parseAsArrayOf(parseAsString).withDefault([]),
      currency: parseAsArrayOf(parseAsString).withDefault([]),
      instrument: parseAsString,
      days: parseAsInteger.withDefault(7),
    },
    { history: "replace" },
  );
  return (
    <CalendarMonitor
      filter={{ impacts: q.impact, currencies: q.currency, instrument: q.instrument, days: q.days }}
      onFilter={(patch) =>
        void setQ({
          ...(patch.impacts !== undefined ? { impact: patch.impacts } : {}),
          ...(patch.currencies !== undefined ? { currency: patch.currencies } : {}),
          ...(patch.instrument !== undefined ? { instrument: patch.instrument } : {}),
          ...(patch.days !== undefined ? { days: patch.days } : {}),
        })
      }
    />
  );
}
