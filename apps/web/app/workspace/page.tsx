"use client";

import { UrlState } from "@/components/UrlState";
import { PageHead } from "@/components/primitives";
import { Workspace } from "@/components/workspace/Workspace";

/**
 * WORKSPACE (WS, F10; `LAYOUT <name>` on the command line): tiling panels
 * from the typed panel registry, TWS-style colour link groups, and named
 * layouts kept per operator on the server (versioned, audited, 409 on a stale
 * write, debounced autosave). Every panel reads its own API and carries its
 * own freshness stamp; the workspace itself computes nothing.
 */
export default function WorkspacePage() {
  return (
    <>
      <PageHead
        title="Workspace"
        intro="Tile any screen's panel into one view. Split, drag the dividers, close and add panels; double-click a header to maximise (Esc restores); Ctrl/⌘+1..9 focuses a panel and Tab moves between tile headers. Panels in the same colour group follow each other's symbol: a source publishes its selection, a destination follows. Layouts are saved per operator on the server, with a version history; edits autosave after a pause, and a layout changed in another tab is never overwritten without your choice."
      />
      <UrlState
        fallback={
          <div className="state state--loading" role="status" data-testid="state-loading">
            <div className="state__title">Loading the workspace…</div>
          </div>
        }
      >
        <Workspace />
      </UrlState>
    </>
  );
}
