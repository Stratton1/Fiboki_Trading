"use client";

import { ViewStateTag } from "./ui/ViewStateTag";

/** A source the platform says is not wired. This is not a failed request. */
export function AbsentNotice({ title, detail }: { title: string; detail: string }) {
  return (
    <div className="state state--absent" data-testid="state-absent" role="status">
      <div className="state__title">
        <ViewStateTag state="absent" />
        <span>{title}</span>
      </div>
      <p className="state__body">{detail}</p>
    </div>
  );
}

/** HTTP codes that mean "this source is not connected", not "the request crashed". */
export const UNWIRED_CODES = new Set([
  "regime_engine_not_wired",
  "correlation_engine_not_wired",
  "research_memory_not_wired",
]);
