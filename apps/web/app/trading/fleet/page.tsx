"use client";

import { parseAsString, useQueryState } from "nuqs";
import { useEffect } from "react";
import { Attribution } from "@/components/fleet/Attribution";
import { FleetTable } from "@/components/fleet/FleetTable";
import { Card, PageHead } from "@/components/primitives";
import { UrlState } from "@/components/UrlState";

/**
 * TRADING · Fleet (F2): what is every bot doing, and what is open?
 *
 * One row per paper session (FX forward, crypto forward, replays), worst state
 * first, from GET /api/trading/fleet; then the executing mode's P&L by
 * strategy and by instrument. `?session=<id>` selects a bot, so every other
 * screen can link straight to one.
 */
export default function FleetPage() {
  return (
    <>
      <PageHead
        title="Fleet"
        intro="Every paper bot: its state as the platform judges it, its last gateway decision, what it has open, and its P&L. Worst first. Then the paper book's P&L by strategy and by instrument."
      />
      <Card title="Bots">
        <UrlState fallback={<FleetTable />}>
          <SelectedFleet />
        </UrlState>
      </Card>
      <Card title="P&L attribution">
        <Attribution />
      </Card>
    </>
  );
}

function SelectedFleet() {
  const [session] = useQueryState("session", parseAsString);
  useEffect(() => {
    if (!session) return;
    const id = window.setTimeout(() => document.getElementById(`bot-${session}`)?.scrollIntoView({ block: "center" }), 300);
    return () => window.clearTimeout(id);
  }, [session]);
  return <FleetTable selected={session} />;
}
