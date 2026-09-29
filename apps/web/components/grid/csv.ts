import type { Figure } from "@/lib/types";
import type { GridColumn, GridSource } from "./types";

/**
 * CSV export with provenance (report G §2.2 F3, plan §3 "every number
 * carries provenance").
 *
 *  - every figure column is exported as three columns: the value (full
 *    precision, "." decimal, ASCII minus, no separators), `<id>_unit` and
 *    `<id>_provenance`, so a number can never leave the workstation without
 *    its label;
 *  - a missing value is an EMPTY cell, never 0 (and `<id>_provenance` still
 *    says whose missing value it is);
 *  - every row carries the payload's `source_kind`, `source_as_of`, the REST
 *    path, and the export time in UTC;
 *  - text that a spreadsheet would execute (leading = + - @, tab, CR) is
 *    prefixed with an apostrophe (CSV injection).
 */

const FORMULA = /^[=+\-@\t\r]/;

export function csvCell(value: string | number | null | undefined): string {
  if (value === null || value === undefined) return "";
  if (typeof value === "number") return Number.isFinite(value) ? String(value) : "";
  const guarded = FORMULA.test(value) ? `'${value}` : value;
  return /[",\n\r]/.test(guarded) ? `"${guarded.replace(/"/g, '""')}"` : guarded;
}

export function toCsv<T>(
  rows: T[],
  columns: GridColumn<T>[],
  meta: { source?: GridSource; exportedAt: string },
): string {
  const exported = columns.filter((c) => c.csv !== false);
  const header: string[] = [];
  for (const column of exported) {
    header.push(column.id);
    if (column.figure) header.push(`${column.id}_unit`, `${column.id}_provenance`);
  }
  header.push("source_kind", "source_as_of", "source_path", "exported_at_utc");

  const lines = [header.map(csvCell).join(",")];
  for (const row of rows) {
    const cells: (string | number | null)[] = [];
    for (const column of exported) {
      const figure: Figure | undefined = column.figure?.(row);
      const value =
        typeof column.csv === "function"
          ? column.csv(row)
          : column.value
            ? column.value(row)
            : figure
              ? figure.value
              : null;
      cells.push(value);
      if (column.figure) cells.push(figure?.unit ?? null, figure?.provenance ?? null);
    }
    cells.push(
      meta.source?.source?.kind ?? null,
      meta.source?.source?.as_of ?? null,
      meta.source?.path ?? null,
      meta.exportedAt,
    );
    lines.push(cells.map(csvCell).join(","));
  }
  return `${lines.join("\r\n")}\r\n`;
}
