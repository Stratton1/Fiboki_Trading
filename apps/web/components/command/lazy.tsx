"use client";

import { lazy, Suspense, type ReactNode } from "react";

/**
 * Command's panels, each loaded after first paint (the home route's first-load
 * budget, .size-limit.mjs) into a box that already has its final height, so
 * nothing below moves when a panel fills in (programme P1 7). The placeholder
 * says what is coming; it is never an empty list.
 */

const Attention = lazy(() => import("./AttentionPanel").then((m) => ({ default: m.AttentionPanel })));
const Fleet = lazy(() => import("../fleet/FleetTable").then((m) => ({ default: m.FleetTable })));
const Limits = lazy(() => import("./LossLimits").then((m) => ({ default: m.LossLimits })));
const Incidents = lazy(() => import("./IncidentsPanel").then((m) => ({ default: m.IncidentsPanel })));

function Pending({ what }: { what: string }) {
  return (
    <p className="muted panel-pending" role="status" data-testid="panel-pending">
      Loading {what}…
    </p>
  );
}

function Slot({ what, children }: { what: string; children: ReactNode }) {
  return <Suspense fallback={<Pending what={what} />}>{children}</Suspense>;
}

export function LazyAttention() {
  return (
    <Slot what="the attention queue">
      <Attention />
    </Slot>
  );
}

export function LazyFleet({ limit }: { limit: number }) {
  return (
    <Slot what="the fleet">
      <Fleet compact limit={limit} />
    </Slot>
  );
}

export function LazyLossLimits() {
  return (
    <Slot what="the loss limits">
      <Limits />
    </Slot>
  );
}

export function LazyIncidents() {
  return (
    <Slot what="incidents">
      <Incidents />
    </Slot>
  );
}
