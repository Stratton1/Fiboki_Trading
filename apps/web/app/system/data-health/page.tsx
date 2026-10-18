"use client";

import { SystemBoard } from "@/components/system/SystemBoard";

/** SYSTEM · Data Health: the System board, opened at its data group (one page, one clock). */
export default function Page() {
  return <SystemBoard focus="data" />;
}
