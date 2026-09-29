"use client";

import { useEffect, type ReactNode } from "react";
import { useApi } from "@/lib/query";
import type { Page } from "@/lib/types";
import { AsyncBoundary } from "./AsyncBoundary";
import { DataGrid, preloadGrid, type GridColumn } from "./grid";
import { CaveatList, PageHead, SourceBadge } from "./primitives";

/**
 * A list view on the DataGrid: head, source note, computed caveats, grid, and
 * an honest count. The four states and the source note come from
 * AsyncBoundary exactly as on ListPage; only the table is different.
 *
 * "Showing N of M" says when the platform returned fewer rows than it holds
 * (a server page), so an operator never reads the first page as the whole.
 */
export function GridPage<T>({
  title,
  intro,
  path,
  label,
  gridId,
  columns,
  rowKey,
  refreshMs,
  emptyTitle,
  emptyBody,
  onRowActivate,
  children,
  before,
}: {
  title: string;
  intro: string;
  path: string;
  label: string;
  gridId: string;
  columns: GridColumn<T>[];
  rowKey: (row: T) => string;
  refreshMs?: number;
  emptyTitle?: string;
  emptyBody?: string;
  onRowActivate?: (row: T) => void;
  /** Rendered under the grid with the page payload (charts, notes). */
  children?: (page: Page<T>) => ReactNode;
  /** Rendered above the async boundary, whatever its state (filters). */
  before?: ReactNode;
}) {
  const state = useApi<Page<T>>(path, refreshMs === undefined ? {} : { refreshMs });
  useEffect(() => {
    void preloadGrid();
  }, []);
  return (
    <>
      <PageHead title={title} intro={intro} />
      {before}
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
            <DataGrid<T>
              id={gridId}
              label={label}
              rows={page.items}
              columns={columns}
              rowKey={rowKey}
              source={{ source: page.source, path }}
              onRowActivate={onRowActivate}
            />
            <p className="muted mt-2" data-testid="grid-total">
              {page.total > page.items.length
                ? `The platform returned ${page.items.length.toLocaleString("en-GB")} of ${page.total.toLocaleString("en-GB")} ${label}; the grid holds only those.`
                : `All ${page.total.toLocaleString("en-GB")} ${label} the platform holds.`}
            </p>
            {children?.(page)}
          </>
        )}
      </AsyncBoundary>
    </>
  );
}
