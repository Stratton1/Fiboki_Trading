"use client";

import Link from "next/link";
import { LazyAttention, LazyFleet, LazyIncidents, LazyLossLimits } from "@/components/command/lazy";
import { MoneyStrip } from "@/components/command/MoneyStrip";
import { PageHead } from "@/components/primitives";
import { LazyKillSwitchTimeline } from "@/components/LazyChart";
import { route } from "@/lib/routes";

/**
 * COMMAND (terminal programme Wave 1A): "Is anything wrong, and what needs me
 * now?" and, beside it, "where is the money?"
 *
 *   money strip      GET /api/command/summary (equity, day and week P&L,
 *                    unrealised, positions, bots by state, research load)
 *   attention        GET /api/command/attention, one line per item, in the
 *                    server's order
 *   fleet            GET /api/trading/fleet, worst bot first (F2 for all)
 *   loss limits      GET /api/trading/limits, the Risk board's own rows
 *   incidents, kill-switch month
 *
 * Every figure is computed by the API. Each panel has a fixed height and
 * loads after first paint, so nothing moves as they fill in.
 */
export default function CommandPage() {
  return (
    <div className="command" data-testid="command-page">
      <PageHead
        title="Command"
        intro="The book in one strip, then what needs you now in the platform's own order, every bot worst first, and how close the book is to its loss limits. Incidents and the kill switch's month follow."
      />
      <MoneyStrip />
      <div className="command__grid">
        <section className="card cmd-panel cmd-panel--attention" aria-labelledby="cmd-attention">
          <h2 id="cmd-attention" className="card__title">
            Needs attention
          </h2>
          <div className="cmd-panel__body">
            <LazyAttention />
          </div>
        </section>
        <section className="card cmd-panel cmd-panel--fleet" aria-labelledby="cmd-fleet">
          <h2 id="cmd-fleet" className="card__title">
            Fleet{" "}
            <Link href={route.fleet()} className="card__link" data-testid="command-fleet-link">
              all bots · F2
            </Link>
          </h2>
          <div className="cmd-panel__body">
            <LazyFleet limit={8} />
          </div>
        </section>
        <section className="card cmd-panel cmd-panel--limits" aria-labelledby="cmd-limits">
          <h2 id="cmd-limits" className="card__title">
            Loss limits{" "}
            <Link href={route.risk()} className="card__link">
              board · F6
            </Link>
          </h2>
          <div className="cmd-panel__body">
            <LazyLossLimits />
          </div>
        </section>
      </div>
      <div className="command__lower">
        <section className="card cmd-panel cmd-panel--incidents" aria-labelledby="cmd-incidents">
          <h2 id="cmd-incidents" className="card__title">
            Incidents
          </h2>
          <div className="cmd-panel__body">
            <LazyIncidents />
          </div>
        </section>
        <div className="cmd-panel--timeline">
          <LazyKillSwitchTimeline />
        </div>
      </div>
    </div>
  );
}
