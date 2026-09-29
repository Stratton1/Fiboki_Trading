"use client";

import type { ReactNode } from "react";
import { useApi } from "@/lib/api";
import type { Page } from "@/lib/types";
import { AsyncBoundary } from "./AsyncBoundary";
import { CaveatList, PageHead, SourceBadge, TableWrap } from "./primitives";

export interface Column<T> {
  key: string;
  header: string;
  /** Rendered per row. Figures go through <FigureValue>, so the chip comes too. */
  cell: (row: T) => ReactNode;
  wrap?: boolean;
}

/**
 * The standard list view: head, source note, computed caveats, table.
 *
 * Centralising it is what makes the four states and the source note impossible
 * to forget. V1 had 19 pages each hand-rolling their own fetch, and that is how
 * 14 of them came to render without ever asking what mode they were in.
 */
export function ListPage<T>({
  title,
  intro,
  path,
  label,
  columns,
  rowKey,
  emptyTitle,
  emptyBody,
  children,
}: {
  title: string;
  intro: string;
  path: string;
  label: string;
  columns: Column<T>[];
  /**
   * A stable key per row. The index is supplied for payloads with no id of
   * their own; a random key would remount every row on every render.
   */
  rowKey: (row: T, index: number) => string;
  emptyTitle?: string;
  emptyBody?: string;
  children?: (page: Page<T>) => ReactNode;
}) {
  const state = useApi<Page<T>>(path);
  return (
    <>
      <PageHead title={title} intro={intro} />
      <AsyncBoundary
        state={state}
        label={label}
        onRetry={state.reload}
        isEmpty={(page) => page.items.length === 0}
        emptyTitle={emptyTitle ?? `No ${label}`}
        emptyBody={
          emptyBody ??
          `The platform answered successfully with no ${label}. That is a real, empty result — not a failure to load.`
        }
      >
        {(page) => (
          <>
            <SourceBadge source={page.source} />
            <CaveatList caveats={page.caveats} />
            {children?.(page)}
            <TableWrap>
              <table>
                <thead>
                  <tr>
                    {columns.map((column) => (
                      <th key={column.key}>{column.header}</th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {page.items.map((row, index) => (
                    <tr key={rowKey(row, index)}>
                      {columns.map((column) => (
                        <td
                          key={column.key}
                          className={column.wrap ? "wrap" : undefined}
                        >
                          {column.cell(row)}
                        </td>
                      ))}
                    </tr>
                  ))}
                </tbody>
              </table>
            </TableWrap>
            <p className="muted mt-2">
              Showing {page.items.length} of {page.total}.
            </p>
          </>
        )}
      </AsyncBoundary>
    </>
  );
}

/** The single-object equivalent of {@link ListPage}. */
export function DetailPage<T>({
  title,
  intro,
  path,
  label,
  children,
}: {
  title: string;
  intro: string;
  path: string;
  label: string;
  children: (data: T) => ReactNode;
}) {
  const state = useApi<{ data: T; source: Parameters<typeof SourceBadge>[0]["source"]; caveats: [] }>(
    path,
  );
  return (
    <>
      <PageHead title={title} intro={intro} />
      <AsyncBoundary state={state} label={label} onRetry={state.reload}>
        {(envelope) => (
          <>
            <SourceBadge source={envelope.source} />
            <CaveatList caveats={envelope.caveats} />
            {children(envelope.data)}
          </>
        )}
      </AsyncBoundary>
    </>
  );
}
