"use client";

import Link from "next/link";
import { LazyAttention, LazyFleet, LazyIncidents, LazyLossLimits } from "@/components/command/lazy";
import { MoneyStrip } from "@/components/command/MoneyStrip";
import { Panel } from "@/components/command/Panel";
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
 * loads after first paint, so nothing moves as they fill in; each can be
 * maximised (its button, or a double-click on its title) and restored (Esc).
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
        <Panel id="cmd-attention" title="Needs attention" className="cmd-panel--attention">
          <LazyAttention />
        </Panel>
        <Panel
          id="cmd-fleet"
          title="Fleet"
          className="cmd-panel--fleet"
          actions={
            <Link href={route.fleet()} className="card__link" data-testid="command-fleet-link">
              all bots · F2
            </Link>
          }
        >
          <LazyFleet limit={8} />
        </Panel>
        <Panel
          id="cmd-limits"
          title="Loss limits"
          className="cmd-panel--limits"
          actions={
            <Link href={route.risk()} className="card__link">
              board · F6
            </Link>
          }
        >
          <LazyLossLimits />
        </Panel>
      </div>
      <div className="command__lower">
        <Panel id="cmd-incidents" title="Incidents" className="cmd-panel--incidents">
          <LazyIncidents />
        </Panel>
        <div className="cmd-panel--timeline">
          <LazyKillSwitchTimeline />
        </div>
      </div>
    </div>
  );
}
