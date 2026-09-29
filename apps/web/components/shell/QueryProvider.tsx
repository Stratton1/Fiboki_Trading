"use client";

import { QueryClientProvider } from "@tanstack/react-query";
import { lazy, Suspense, useState, type ReactNode } from "react";
import { makeQueryClient } from "@/lib/query";

/**
 * TanStack Query for the whole workstation: one cache per tab, so every view
 * of a resource shares one fetch and the stream updates them all at once.
 *
 * The devtools exist only in `next dev`. In a production build the branch is
 * the constant `false` and the import is never reached, so they are not in
 * any first-load bundle (the byte budgets in tests/e2e/source-rules.spec.ts).
 */
const Devtools =
  process.env.NODE_ENV === "development"
    ? lazy(() =>
        import("@tanstack/react-query-devtools").then((m) => ({
          default: m.ReactQueryDevtools,
        })),
      )
    : null;

export function QueryProvider({ children }: { children: ReactNode }) {
  const [client] = useState(makeQueryClient);
  return (
    <QueryClientProvider client={client}>
      {children}
      {Devtools ? (
        <Suspense fallback={null}>
          <Devtools initialIsOpen={false} buttonPosition="bottom-left" />
        </Suspense>
      ) : null}
    </QueryClientProvider>
  );
}
