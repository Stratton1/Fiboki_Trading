"use client";

import { SystemBoard } from "@/components/system/SystemBoard";

/** SYSTEM · Workers: the System board, opened at its workers group (one page, one clock). */
export default function Page() {
  return <SystemBoard focus="workers" />;
}
