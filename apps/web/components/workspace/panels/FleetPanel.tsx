"use client";

import { FleetTable } from "@/components/fleet/FleetTable";
import type { PanelProps } from "../types";

/** FLT in a tile: the fleet table, worst first, compact unless maximised. */
export default function FleetPanel({ maximised }: PanelProps) {
  return <FleetTable compact={!maximised} limit={maximised ? undefined : 12} />;
}
