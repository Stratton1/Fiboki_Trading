"use client";

import { lazy, Suspense, type ComponentType } from "react";
import type { DataGridProps } from "./types";

export type { DataGridProps, GridColumn, GridSource } from "./types";

/**
 * The DataGrid, loaded on first use: TanStack Table and Virtual are in no
 * route's first-load JavaScript (plan D-F7 budgets). Pages that show a grid
 * call `preloadGrid()` on mount so the chunk arrives while their data does.
 */
export const preloadGrid = () => import("./DataGrid");
const Impl = lazy(preloadGrid) as unknown as ComponentType<DataGridProps<unknown>>;

export function DataGrid<T>(props: DataGridProps<T>) {
  return (
    <Suspense
      fallback={
        <div className="grid grid--pending" data-testid="grid-pending" role="status">
          Preparing the {props.label} grid ({props.rows.length.toLocaleString("en-GB")} rows)…
        </div>
      }
    >
      <Impl {...(props as unknown as DataGridProps<unknown>)} />
    </Suspense>
  );
}
