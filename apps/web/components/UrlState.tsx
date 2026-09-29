"use client";

import { NuqsAdapter } from "nuqs/adapters/next/app";
import { Suspense, type ReactNode } from "react";

/**
 * URL state (plan D-F3): filters, the selected entity and the open tab live in
 * the query string, so a view can be linked, bookmarked and restored, and the
 * back button undoes a filter change.
 *
 * The nuqs adapter is mounted per page, around only the parts that read the
 * URL, so pages without URL state do not carry it in their first load. Every
 * page here is prerendered, and reading the query string opts a subtree out of
 * prerendering; `fallback` is what the prerendered HTML shows until the client
 * has read the URL (the default view, not a blank).
 */
export function UrlState({ children, fallback }: { children: ReactNode; fallback: ReactNode }) {
  return (
    <NuqsAdapter>
      <Suspense fallback={fallback}>{children}</Suspense>
    </NuqsAdapter>
  );
}
