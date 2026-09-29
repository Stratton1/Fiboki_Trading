import type { ReactNode } from "react";
import type { Figure, SourceNote } from "@/lib/types";

/**
 * One DataGrid column. Pages describe columns in these terms; the grid owns
 * the TanStack Table model, so no page depends on the table library.
 *
 *  - `figure` columns carry a Figure per row: right-aligned, fixed decimals
 *    for their unit, the unit in the HEADER (derived from the rows' own
 *    units; mixed units are said so, never averaged), sorted by value with
 *    "no data" always last, exported with a provenance column;
 *  - `number` columns are plain numbers (a sequence number): right-aligned;
 *  - `text` and `time` columns sort as text (time columns are ISO UTC, which
 *    sorts correctly as text).
 */
export interface GridColumn<T> {
  id: string;
  header: string;
  kind?: "text" | "number" | "figure" | "time";
  /** The Figure for this cell (figure columns). */
  figure?: (row: T) => Figure;
  /** The sort, filter and CSV value. Defaults to the figure's value. */
  value?: (row: T) => string | number | null;
  /** Custom cell content. Figure columns render FigureValue by default. */
  cell?: (row: T) => ReactNode;
  /** A signed quantity (P&L, R): colour, explicit sign after rounding. */
  signed?: boolean;
  /** Show the provenance chip in each cell (figures). */
  chip?: boolean;
  /** A precision the server supplied for this row (e.g. from pip size). */
  decimals?: (row: T) => number | null;
  /** Width in px (the grid uses a fixed layout so pinned offsets are exact). */
  width?: number;
  /** Allow wrapping (the row grows; virtualisation measures it). */
  wrap?: boolean;
  /** Pinned to the start by default (the identity column). */
  pin?: boolean;
  /** Hidden by default (the operator can show it). */
  hidden?: boolean;
  sortable?: boolean;
  /** Take part in the text filter. Default true. */
  filterable?: boolean;
  /** CSV value; `false` leaves the column out of the export. */
  csv?: false | ((row: T) => string | number | null);
}

/** The payload-level provenance a CSV export carries on every row. */
export interface GridSource {
  source: SourceNote | null;
  /** The REST path the rows came from. */
  path: string;
}

export interface DataGridProps<T> {
  /** Stable id: the localStorage key for layouts and the CSV file name. */
  id: string;
  label: string;
  rows: T[];
  columns: GridColumn<T>[];
  rowKey: (row: T) => string;
  /** The payload's source note and path, stamped into CSV exports. */
  source?: GridSource;
  /** Enter on a row (or a double click). */
  onRowActivate?: (row: T) => void;
  /** Maximum scroll height of the grid body, CSS length. */
  maxHeight?: string;
  testId?: string;
  /** A test id on every row (defaults to `grid-row`). */
  rowTestId?: string;
}
