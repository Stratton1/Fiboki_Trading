"use client";

import {
  columnFilteringFeature,
  columnVisibilityFeature,
  createFilteredRowModel,
  createSortedRowModel,
  filterFn_includesString,
  globalFilteringFeature,
  rowSortingFeature,
  sortFn_alphanumeric,
  tableFeatures,
  useTable,
  type ColumnDef,
  type ColumnVisibilityState,
  type RowData,
} from "@tanstack/react-table";
import { useVirtualizer } from "@tanstack/react-virtual";
import { ArrowDown, ArrowUp, ArrowUpDown, ChevronDown, ChevronUp, Columns3, Download, Palette, Pin, PinOff } from "lucide-react";
import { useSearchParams } from "next/navigation";
import {
  useEffect,
  useLayoutEffect,
  useMemo,
  useRef,
  useState,
  type KeyboardEvent,
  type ReactNode,
} from "react";
import { ApiError } from "@/lib/api";
import { displayDecimals, unitDecimals } from "@/lib/format";
import { useApi } from "@/lib/query";
import type { Envelope, Figure } from "@/lib/types";
import { PATHS, saveColumnSets, type ColumnSet, type ColumnSetsView } from "@/lib/workspace";
import type { components } from "@/lib/generated/openapi";
import { FigureValue } from "../FigureValue";
import { Button } from "../ui/Button";
import { Popover } from "../ui/Popover";
import { toCsv } from "./csv";
import { loadLayout, loadSets, saveLayout, saveSets, type GridLayout, type SavedSets } from "./layout";
import type { DataGridProps, GridColumn } from "./types";

/**
 * THE data grid (plan D-F4, report E §5, report G §2.2 F3). Loaded on first
 * use (components/grid/index.tsx), so it is in no route's first load.
 *
 *  - TanStack Table 9 for sort (click a header; "no data" always sorts last),
 *    the text filter and column visibility; TanStack Virtual 3 renders only
 *    the rows in view, so 10,000 rows cost the same as 30.
 *  - Numerics are right-aligned under right-aligned headers, with the UNIT IN
 *    THE HEADER taken from the rows' own Figures ("mixed units" when they
 *    differ: the grid never pretends), fixed decimals per unit, and a shared
 *    precision for unitless columns so the digits line up.
 *  - Pinned columns stick to the start; the fixed layout keeps offsets exact.
 *  - Column ORDER (move a column up or down in the Columns menu), visibility
 *    and pins; saved column sets in localStorage, or on the server per
 *    operator (`serverSets`, GET/PUT /api/workspace/column-sets/<id>,
 *    versioned: a set saved in another tab is never overwritten silently).
 *  - Conditional formatting by the API's rules: a row's `tones` (decided by
 *    src/fiboki/api/grid_format.py) become a class per cell; `formatRules`
 *    shows the API's legend of what each colour means.
 *  - Keyboard: the rows are a roving tab stop (one row is tabbable); ↓/j, ↑/k,
 *    Home, End, PageDown, PageUp move; Enter activates. The selected row is
 *    tracked by ROW KEY, so a live update that changes a cell, or re-sorts
 *    the rows, never moves the selection to a different trade.
 *  - CSV export of the filtered, sorted rows with provenance columns
 *    (csv.ts): a number never leaves without its label.
 *  - `?row=<key>` in the URL selects and scrolls to that row (the palette's
 *    "open by id"); a key that is not in the result is said so.
 */

const features = tableFeatures({
  columnFilteringFeature,
  globalFilteringFeature,
  filteredRowModel: createFilteredRowModel(),
  filterFns: { includesString: filterFn_includesString },
  rowSortingFeature,
  sortedRowModel: createSortedRowModel(),
  sortFns: { alphanumeric: sortFn_alphanumeric },
  columnVisibilityFeature,
});

type Features = typeof features;

const UNIT_LABEL: Record<string, string> = {
  GBP: "GBP",
  USD: "USD",
  EUR: "EUR",
  JPY: "JPY",
  pct: "%",
  R: "R",
  bps: "bps",
  ms: "ms",
  s: "s",
  lots: "lots",
  pips: "pips",
  x: "x",
  ratio: "",
  count: "",
  "": "",
};

function isNumeric<T>(column: GridColumn<T>): boolean {
  return column.kind === "figure" || column.kind === "number" || column.figure !== undefined;
}

function defaultWidth<T>(column: GridColumn<T>): number {
  if (column.width) return column.width;
  if (column.wrap) return 280;
  if (column.kind === "time") return 150;
  if (isNumeric(column)) return column.chip ? 150 : 112;
  return 140;
}

function sortValue<T>(column: GridColumn<T>, row: T): string | number | undefined {
  const value = column.value
    ? column.value(row)
    : column.figure
      ? column.figure(row).value
      : null;
  // Undefined is what the table sorts last in either direction.
  return value === null ? undefined : value;
}

interface ColumnPresentation {
  /** The header's unit label, or "mixed units". */
  unit: string;
  /** Cells drop their unit affixes (the header carries it). */
  bare: boolean;
  /** A shared precision for unitless figures, so they align. */
  decimals: number | null;
}

/** Derived from the ROWS' own Figures: the server's units, never assumed. */
function present<T>(column: GridColumn<T>, rows: T[]): ColumnPresentation {
  if (!column.figure) return { unit: "", bare: false, decimals: null };
  const units = new Set<string>();
  let widest = 0;
  for (const row of rows) {
    const figure = column.figure(row);
    units.add(figure.unit);
    if (figure.value !== null && unitDecimals(figure.unit) === null) {
      widest = Math.max(widest, displayDecimals(figure.value, figure.unit));
    }
  }
  if (units.size > 1) return { unit: "mixed units", bare: false, decimals: null };
  const only = units.values().next().value ?? "";
  const label = UNIT_LABEL[only] ?? only;
  return {
    unit: label,
    bare: label !== "" || only === "count" || only === "ratio",
    decimals: unitDecimals(only) === null ? Math.min(5, widest) : null,
  };
}

function initialLayout<T>(id: string, columns: GridColumn<T>[]): GridLayout {
  const stored = loadLayout(id);
  const known = new Set(columns.map((c) => c.id));
  if (stored) {
    return {
      visibility: Object.fromEntries(
        Object.entries(stored.visibility).filter(([key]) => known.has(key)),
      ),
      pinned: stored.pinned.filter((key) => known.has(key)),
    };
  }
  return defaultLayout(columns);
}

function defaultLayout<T>(columns: GridColumn<T>[]): GridLayout {
  return {
    visibility: Object.fromEntries(columns.filter((c) => c.hidden).map((c) => [c.id, false])),
    pinned: columns.filter((c) => c.pin).map((c) => c.id),
  };
}

function readRowHeight(el: HTMLElement | null): number {
  if (!el) return 32;
  const value = parseFloat(getComputedStyle(el).getPropertyValue("--row-h"));
  return Number.isFinite(value) && value > 0 ? value : 32;
}

export default function DataGrid<T extends RowData>({
  id,
  label,
  rows: data,
  columns,
  rowKey,
  source,
  onRowActivate,
  maxHeight = "min(70vh, 720px)",
  testId = "data-grid",
  rowTestId = "grid-row",
  tones,
  formatRules,
  serverSets = false,
}: DataGridProps<T>) {
  const [layout, setLayout] = useState<GridLayout>(() => initialLayout(id, columns));
  const [sets, setSets] = useState<SavedSets>(() => loadSets(id));
  const [globalFilter, setGlobalFilter] = useState("");
  const [activeId, setActiveId] = useState<string | null>(null);
  const focusRequest = useRef<string | null>(null);
  const scrollRef = useRef<HTMLDivElement>(null);
  const headRef = useRef<HTMLTableSectionElement>(null);
  const [rowHeight, setRowHeight] = useState(32);
  const [headHeight, setHeadHeight] = useState(32);

  useEffect(() => saveLayout(id, layout), [id, layout]);

  const byId = useMemo(() => new Map(columns.map((c) => [c.id, c])), [columns]);
  const defs = useMemo(
    () =>
      columns.map((column) => ({
        id: column.id,
        header: column.header,
        accessorFn: (row: T) => sortValue(column, row),
        enableSorting: column.sortable !== false,
        enableHiding: true,
        sortUndefined: "last" as const,
        // Every column sorts ascending on the first click, numbers included.
        sortDescFirst: false,
        sortFn: isNumeric(column) ? ("auto" as const) : ("alphanumeric" as const),
      })) as unknown as ColumnDef<Features, T>[],
    [columns],
  );

  const columnVisibility = layout.visibility as ColumnVisibilityState;
  const table = useTable<Features, T>({
    features,
    columns: defs,
    data,
    getRowId: (row: T) => rowKey(row),
    state: { columnVisibility, globalFilter },
    onColumnVisibilityChange: (updater) =>
      setLayout((prev) => ({
        ...prev,
        visibility: typeof updater === "function" ? updater(prev.visibility) : updater,
      })),
    onGlobalFilterChange: (updater) =>
      setGlobalFilter((prev) => String(typeof updater === "function" ? updater(prev) : updater)),
    globalFilterFn: "includesString",
    getColumnCanGlobalFilter: (column) => byId.get(column.id)?.filterable !== false,
    enableSortingRemoval: true,
  });

  const rows = table.getRowModel().rows;

  // Pinned first (in pin order), then the rest in the operator's order
  // (declared order for any column the saved order does not name).
  const byOrder = orderedColumns(columns, layout.order);
  const visible = byOrder.filter((c) => layout.visibility[c.id] !== false);
  const pinnedVisible = layout.pinned
    .map((key) => visible.find((c) => c.id === key))
    .filter((c): c is GridColumn<T> => c !== undefined);
  const ordered = [...pinnedVisible, ...visible.filter((c) => !layout.pinned.includes(c.id))];
  const offsets = new Map<string, number>();
  {
    let left = 0;
    for (const c of pinnedVisible) {
      offsets.set(c.id, left);
      left += defaultWidth(c);
    }
  }
  const totalWidth = ordered.reduce((sum, c) => sum + defaultWidth(c), 0);
  const presentation = useMemo(
    () => new Map(columns.map((c) => [c.id, present(c, data)])),
    [columns, data],
  );

  useLayoutEffect(() => {
    setRowHeight(readRowHeight(scrollRef.current));
    const head = headRef.current;
    if (!head) return;
    const measure = () => setHeadHeight(head.getBoundingClientRect().height || 32);
    measure();
    const observer = new ResizeObserver(measure);
    observer.observe(head);
    return () => observer.disconnect();
  }, []);

  // This build does not run the React Compiler; the virtualizer's functions
  // are used inside this component only, so nothing memoises them stale.
  // eslint-disable-next-line react-hooks/incompatible-library
  const virtualizer = useVirtualizer({
    count: rows.length,
    getScrollElement: () => scrollRef.current,
    estimateSize: () => rowHeight,
    getItemKey: (index) => rows[index]?.id ?? index,
    overscan: 10,
    paddingStart: headHeight,
    scrollPaddingStart: headHeight,
    useFlushSync: false,
  });
  const items = virtualizer.getVirtualItems();
  const first = items[0];
  const last = items[items.length - 1];
  const padTop = first ? Math.max(0, first.start - headHeight) : 0;
  const padBottom = last ? Math.max(0, virtualizer.getTotalSize() - last.end) : 0;

  const activeIndex = activeId === null ? -1 : rows.findIndex((row) => row.id === activeId);
  const tabbableIndex = activeIndex >= 0 ? activeIndex : 0;
  const tabbableRendered = items.some((item) => item.index === tabbableIndex);

  // Move focus to a row that a keystroke selected, once it is rendered.
  useLayoutEffect(() => {
    const want = focusRequest.current;
    if (want === null || !scrollRef.current) return;
    const el = scrollRef.current.querySelector<HTMLElement>(
      `tr[data-row-id="${CSS.escape(want)}"]`,
    );
    if (el) {
      focusRequest.current = null;
      el.focus({ preventScroll: true });
    }
  });

  // `?row=<key>`: select and reveal the requested row.
  const requested = useSearchParams().get("row");
  const [missing, setMissing] = useState<string | null>(null);
  const handledRequest = useRef<string | null>(null);
  useEffect(() => {
    if (!requested || handledRequest.current === requested || rows.length === 0) return;
    handledRequest.current = requested;
    const index = rows.findIndex((row) => row.id === requested);
    if (index === -1) {
      setMissing(requested);
      return;
    }
    setMissing(null);
    setActiveId(requested);
    virtualizer.scrollToIndex(index, { align: "center" });
  }, [requested, rows, virtualizer]);

  const moveTo = (index: number) => {
    if (rows.length === 0) return;
    const clamped = Math.max(0, Math.min(rows.length - 1, index));
    const target = rows[clamped];
    if (!target) return;
    setActiveId(target.id);
    focusRequest.current = target.id;
    virtualizer.scrollToIndex(clamped, { align: "auto" });
  };

  const pageSize = Math.max(1, Math.floor((scrollRef.current?.clientHeight ?? 400) / rowHeight) - 1);
  const onKeyDown = (event: KeyboardEvent<HTMLTableSectionElement>) => {
    if (!(event.target instanceof HTMLTableRowElement)) return;
    if (event.altKey || event.ctrlKey || event.metaKey) return;
    const current = activeIndex >= 0 ? activeIndex : 0;
    const key = event.key;
    let handled = true;
    if (key === "ArrowDown" || (key === "j" && !event.shiftKey)) moveTo(current + 1);
    else if (key === "ArrowUp" || (key === "k" && !event.shiftKey)) moveTo(current - 1);
    else if (key === "Home") moveTo(0);
    else if (key === "End") moveTo(rows.length - 1);
    else if (key === "PageDown") moveTo(current + pageSize);
    else if (key === "PageUp") moveTo(current - pageSize);
    else if (key === "Enter" && onRowActivate) {
      const row = rows[current];
      if (row) onRowActivate(row.original);
    } else handled = false;
    if (handled) event.preventDefault();
  };

  const exportCsv = () => {
    const exportedAt = new Date().toISOString();
    const csv = toCsv(
      rows.map((row) => row.original),
      byOrder,
      { source, exportedAt },
    );
    const blob = new Blob([csv], { type: "text/csv;charset=utf-8" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = `fiboki-${id}-${exportedAt.replace(/[:.]/g, "-")}.csv`;
    document.body.append(a);
    a.click();
    a.remove();
    setTimeout(() => URL.revokeObjectURL(url), 10_000);
  };

  const cellStyle = (column: GridColumn<T>) => {
    const left = offsets.get(column.id);
    return left === undefined ? undefined : { insetInlineStart: `${left}px` };
  };
  const cellClass = (column: GridColumn<T>, base: string) =>
    [
      base,
      isNumeric(column) ? "num" : "",
      column.wrap ? "wrap" : "",
      offsets.has(column.id) ? "grid__pinned" : "",
      column.id === pinnedVisible[pinnedVisible.length - 1]?.id ? "grid__pinned-last" : "",
    ]
      .filter(Boolean)
      .join(" ");

  const renderCell = (column: GridColumn<T>, row: T): ReactNode => {
    if (column.cell) return column.cell(row);
    if (column.figure) {
      const figure: Figure = column.figure(row);
      const p = presentation.get(column.id);
      const decimals = column.decimals?.(row) ?? p?.decimals ?? null;
      return (
        <FigureValue
          figure={figure}
          showChip={column.chip === true}
          colourSign={column.signed === true}
          decimals={decimals}
          bare={p?.bare === true}
        />
      );
    }
    const value = column.value?.(row);
    return value === null || value === undefined ? (
      <span className="figure__missing">no data</span>
    ) : (
      String(value)
    );
  };

  return (
    <div className="grid" data-testid={testId} data-grid-id={id}>
      <div className="grid__toolbar">
        <input
          type="search"
          className="grid__filter"
          placeholder="Filter rows"
          aria-label={`Filter ${label}`}
          value={globalFilter}
          onChange={(event) => setGlobalFilter(event.target.value)}
          data-testid="grid-filter"
        />
        <span className="grid__count muted" data-testid="grid-count" role="status" aria-live="polite">
          {rows.length === data.length
            ? `${data.length.toLocaleString("en-GB")} rows`
            : `${rows.length.toLocaleString("en-GB")} of ${data.length.toLocaleString("en-GB")} rows match`}
        </span>
        <span className="grid__spacer" />
        {formatRules ? <FormatLegend grid={formatRules} /> : null}
        <ColumnsMenu
          columns={byOrder}
          layout={layout}
          sets={sets}
          onLayout={setLayout}
          onSets={(next) => {
            setSets(next);
            saveSets(id, next);
          }}
          onReset={() => setLayout(defaultLayout(columns))}
          label={label}
          server={serverSets ? id : null}
        />
        <Button size="sm" onClick={exportCsv} data-testid="grid-export">
          <Download size={12} aria-hidden="true" />
          Export CSV
        </Button>
      </div>
      {missing ? (
        <p className="muted grid__missing" data-testid="grid-row-missing" role="status">
          <span className="mono">{missing}</span> is not in this result. It may be outside the rows
          the platform returned, or filtered out.
        </p>
      ) : null}
      <div
        ref={scrollRef}
        className="grid__scroll"
        data-max-height={maxHeight}
        tabIndex={tabbableRendered || rows.length === 0 ? -1 : 0}
        aria-label={tabbableRendered ? undefined : `${label}: press Tab to reach the selected row`}
        onFocus={(event) => {
          if (event.target !== event.currentTarget || rows.length === 0) return;
          moveTo(tabbableIndex);
        }}
        style={{ maxHeight }}
      >
        <table
          role="grid"
          className="grid__table"
          aria-label={label}
          aria-rowcount={rows.length + 1}
          aria-colcount={ordered.length}
          style={{ width: `${totalWidth}px` }}
        >
          <colgroup>
            {ordered.map((column) => (
              <col key={column.id} style={{ width: `${defaultWidth(column)}px` }} />
            ))}
          </colgroup>
          <thead ref={headRef}>
            <tr aria-rowindex={1}>
              {ordered.map((column) => {
                const tableColumn = table.getColumn(column.id);
                const sorted = tableColumn?.getIsSorted() ?? false;
                const unit = presentation.get(column.id)?.unit ?? "";
                const text = (
                  <>
                    <span>{column.header}</span>
                    {unit ? (
                      <span className="grid__unit" data-testid="grid-unit">
                        ({unit})
                      </span>
                    ) : null}
                  </>
                );
                return (
                  <th
                    key={column.id}
                    scope="col"
                    className={cellClass(column, "grid__th")}
                    style={cellStyle(column)}
                    aria-sort={
                      sorted === "asc" ? "ascending" : sorted === "desc" ? "descending" : undefined
                    }
                    data-column={column.id}
                    data-numeric={isNumeric(column) ? "true" : undefined}
                  >
                    {column.sortable !== false && tableColumn ? (
                      <button
                        type="button"
                        className="grid__sort"
                        onClick={() => tableColumn.toggleSorting(undefined, false)}
                        data-testid={`grid-sort-${column.id}`}
                      >
                        {text}
                        {sorted === "asc" ? (
                          <ArrowUp size={11} aria-hidden="true" />
                        ) : sorted === "desc" ? (
                          <ArrowDown size={11} aria-hidden="true" />
                        ) : (
                          <ArrowUpDown size={11} aria-hidden="true" className="grid__sort-idle" />
                        )}
                      </button>
                    ) : (
                      text
                    )}
                  </th>
                );
              })}
            </tr>
          </thead>
          <tbody onKeyDown={onKeyDown}>
            {padTop > 0 ? (
              <tr aria-hidden="true" className="grid__pad">
                <td colSpan={ordered.length} style={{ height: `${padTop}px` }} />
              </tr>
            ) : null}
            {items.map((item) => {
              const row = rows[item.index];
              if (!row) return null;
              const selected = row.id === activeId;
              const rowTones = tones?.(row.original) ?? null;
              return (
                <tr
                  key={row.id}
                  ref={virtualizer.measureElement}
                  data-index={item.index}
                  data-row-id={row.id}
                  data-testid={rowTestId}
                  aria-rowindex={item.index + 2}
                  aria-selected={selected}
                  tabIndex={item.index === tabbableIndex ? 0 : -1}
                  onClick={() => setActiveId(row.id)}
                  onDoubleClick={() => onRowActivate?.(row.original)}
                  onFocus={(event) => {
                    if (event.target === event.currentTarget) setActiveId(row.id);
                  }}
                >
                  {ordered.map((column) => {
                    const tone = rowTones?.[column.id];
                    return (
                    <td
                      key={column.id}
                      className={cellClass(column, tone ? `grid__td tone-${tone}` : "grid__td")}
                      style={cellStyle(column)}
                      data-column={column.id}
                      data-tone={tone}
                    >
                      {renderCell(column, row.original)}
                    </td>
                    );
                  })}
                </tr>
              );
            })}
            {padBottom > 0 ? (
              <tr aria-hidden="true" className="grid__pad">
                <td colSpan={ordered.length} style={{ height: `${padBottom}px` }} />
              </tr>
            ) : null}
          </tbody>
        </table>
        {rows.length === 0 && data.length > 0 ? (
          <p className="muted grid__none" data-testid="grid-no-match">
            No row matches the filter. The platform returned {data.length} row
            {data.length === 1 ? "" : "s"}; the filter hides them.
          </p>
        ) : null}
      </div>
    </div>
  );
}

function ColumnsMenu<T>({
  columns,
  layout,
  sets,
  onLayout,
  onSets,
  onReset,
  label,
  server,
}: {
  /** In the current display order. */
  columns: GridColumn<T>[];
  layout: GridLayout;
  sets: SavedSets;
  onLayout: (update: (prev: GridLayout) => GridLayout) => void;
  onSets: (next: SavedSets) => void;
  onReset: () => void;
  label: string;
  /** The grid id when its sets are kept on the server, else null. */
  server: string | null;
}) {
  const [name, setName] = useState("");
  const remote = useServerSets(server);
  const [hadLocal] = useState(() => server !== null && loadLayout(server) !== null);
  const appliedDefault = useRef(false);
  // A server-side default set is applied when the grid opens with no
  // arrangement remembered in this browser.
  useEffect(() => {
    if (appliedDefault.current || hadLocal || remote.view === null) return;
    appliedDefault.current = true;
    const preferred = remote.view.sets.find((x) => x.name === remote.view?.default);
    if (preferred) onLayout(() => fromServerSet(preferred));
  }, [hadLocal, remote.view, onLayout]);

  const toggleVisible = (key: string, on: boolean) =>
    onLayout((prev) => ({ ...prev, visibility: { ...prev.visibility, [key]: on } }));
  const togglePin = (key: string) =>
    onLayout((prev) => ({
      ...prev,
      pinned: prev.pinned.includes(key) ? prev.pinned.filter((k) => k !== key) : [...prev.pinned, key],
    }));
  const ids = columns.map((c) => c.id);
  const move = (key: string, by: -1 | 1) =>
    onLayout((prev) => {
      const order = [...ids];
      const at = order.indexOf(key);
      const to = at + by;
      if (at < 0 || to < 0 || to >= order.length) return prev;
      order.splice(at, 1);
      order.splice(to, 0, key);
      return { ...prev, order };
    });
  const serverSets: ColumnSet[] = remote.view?.sets ?? [];
  const named: [string, GridLayout][] =
    server === null ? Object.entries(sets) : serverSets.map((x) => [x.name, fromServerSet(x)]);
  const saveSet = (setName: string) => {
    if (server === null) {
      onSets({ ...sets, [setName]: layout });
      return;
    }
    const next = [...serverSets.filter((x) => x.name !== setName), toServerSet(setName, { ...layout, order: layout.order ?? ids }, columns)];
    void remote.write(next, remote.view?.default ?? null);
  };
  const deleteSet = (setName: string) => {
    if (server === null) {
      const next = { ...sets };
      delete next[setName];
      onSets(next);
      return;
    }
    const def = remote.view?.default === setName ? null : (remote.view?.default ?? null);
    void remote.write(serverSets.filter((x) => x.name !== setName), def);
  };
  const canSave = server === null || remote.view?.can_edit === true;
  return (
    <Popover
      title={`Columns: ${label}`}
      testId="grid-columns-popover"
      align="end"
      trigger={
        <button
          type="button"
          className="grid__tool"
          data-testid="grid-columns"
          aria-label={`Columns of ${label}: order, show, hide, pin and saved sets`}
        >
          <Columns3 size={12} aria-hidden="true" />
          Columns
        </button>
      }
    >
      <fieldset className="grid__menu">
        <legend className="field-label">Order, show and pin</legend>
        <ul className="grid__menu-list">
          {columns.map((column, index) => {
            const shown = layout.visibility[column.id] !== false;
            const pinned = layout.pinned.includes(column.id);
            return (
              <li key={column.id} className="grid__menu-row" data-testid={`grid-order-${column.id}`}>
                <label>
                  <input
                    type="checkbox"
                    checked={shown}
                    onChange={(event) => toggleVisible(column.id, event.target.checked)}
                    data-testid={`grid-show-${column.id}`}
                  />
                  {column.header}
                </label>
                <span className="row">
                  <button
                    type="button"
                    className="grid__pin"
                    aria-label={`Move ${column.header} earlier`}
                    disabled={index === 0}
                    onClick={() => move(column.id, -1)}
                    data-testid={`grid-up-${column.id}`}
                  >
                    <ChevronUp size={12} aria-hidden="true" />
                  </button>
                  <button
                    type="button"
                    className="grid__pin"
                    aria-label={`Move ${column.header} later`}
                    disabled={index === columns.length - 1}
                    onClick={() => move(column.id, 1)}
                    data-testid={`grid-down-${column.id}`}
                  >
                    <ChevronDown size={12} aria-hidden="true" />
                  </button>
                  <button
                    type="button"
                    className="grid__pin"
                    aria-pressed={pinned}
                    aria-label={`${pinned ? "Unpin" : "Pin"} ${column.header}`}
                    onClick={() => togglePin(column.id)}
                    data-testid={`grid-pin-${column.id}`}
                  >
                    {pinned ? <PinOff size={12} aria-hidden="true" /> : <Pin size={12} aria-hidden="true" />}
                  </button>
                </span>
              </li>
            );
          })}
        </ul>
      </fieldset>
      <form
        className="grid__menu-save"
        onSubmit={(event) => {
          event.preventDefault();
          const trimmed = name.trim();
          if (!trimmed || !canSave) return;
          saveSet(trimmed);
          setName("");
        }}
      >
        <label className="field-label" htmlFor={`grid-set-name-${label}`}>
          Save these columns as a set{server === null ? " (this browser)" : " (on the server, yours)"}
        </label>
        <div className="row">
          <input
            id={`grid-set-name-${label}`}
            type="text"
            value={name}
            maxLength={48}
            onChange={(event) => setName(event.target.value)}
            data-testid="grid-set-name"
          />
          <Button size="sm" type="submit" data-testid="grid-set-save" disabled={!canSave}>
            Save
          </Button>
        </div>
      </form>
      {server !== null && remote.state.status === "error" ? (
        <p className="muted" role="status" data-testid="grid-sets-error">
          Saved sets could not be read: {remote.state.error.message}
        </p>
      ) : null}
      {remote.message ? (
        <p className="muted" role="status" data-testid="grid-sets-message">
          {remote.message}
        </p>
      ) : null}
      {named.length > 0 ? (
        <ul className="grid__menu-list" data-testid="grid-sets" data-server={server !== null || undefined}>
          {named.map(([setName, saved]) => (
            <li key={setName} className="grid__menu-row">
              <span>
                {setName}
                {server !== null && remote.view?.default === setName ? <span className="muted"> · default</span> : null}
              </span>
              <span className="row">
                <Button size="sm" onClick={() => onLayout(() => saved)} data-testid={`grid-set-apply-${setName}`}>
                  Apply
                </Button>
                {server !== null && canSave && remote.view?.default !== setName ? (
                  <Button
                    size="sm"
                    variant="ghost"
                    onClick={() => void remote.write(serverSets, setName)}
                    data-testid={`grid-set-default-${setName}`}
                  >
                    Default
                  </Button>
                ) : null}
                {canSave ? (
                  <Button size="sm" variant="ghost" onClick={() => deleteSet(setName)} aria-label={`Delete column set ${setName}`}>
                    Delete
                  </Button>
                ) : null}
              </span>
            </li>
          ))}
        </ul>
      ) : null}
      <Button size="sm" variant="ghost" onClick={onReset} data-testid="grid-reset">
        Reset to the default columns
      </Button>
    </Popover>
  );
}

/** Columns in the operator's order; ids the order does not name keep their declared place after it. */
export function orderedColumns<T>(columns: GridColumn<T>[], order: string[] | undefined): GridColumn<T>[] {
  if (!order || order.length === 0) return columns;
  const rank = new Map(order.map((id, i) => [id, i]));
  return columns
    .map((column, declared) => ({ column, key: rank.has(column.id) ? (rank.get(column.id) as number) : order.length + declared }))
    .sort((a, b) => a.key - b.key)
    .map((x) => x.column);
}

type FormatRule = components["schemas"]["FormatRule"];
type FormatRulesView = components["schemas"]["FormatRulesView"];

/** The API's conditional-format rules for this grid: what each colour means. */
function FormatLegend({ grid }: { grid: string }) {
  return (
    <Popover
      title="Colours in this grid (the API's rules)"
      testId="grid-format-legend"
      align="end"
      trigger={
        <button type="button" className="grid__tool" data-testid="grid-format" aria-label="What the colours in this grid mean">
          <Palette size={12} aria-hidden="true" />
          Colours
        </button>
      }
    >
      <LegendBody grid={grid} />
    </Popover>
  );
}

function LegendBody({ grid }: { grid: string }) {
  const rules = useApi<Envelope<FormatRulesView>>(`/api/workspace/format-rules/${encodeURIComponent(grid)}`);
  if (rules.status === "loading") return <p className="muted">Loading the rules…</p>;
  if (rules.status === "error") return <p className="muted">The rules could not be loaded: {rules.error.message}</p>;
  const list: FormatRule[] = rules.data.data.rules;
  if (list.length === 0) return <p className="muted">The API applies no conditional formatting to this grid.</p>;
  return (
    <ul className="grid__menu-list" data-testid="grid-format-rules">
      {list.map((r, i) => (
        <li key={`${r.column}-${i}`} className="grid__menu-row">
          <span className={`grid__swatch tone-${r.tone}`} data-tone={r.tone}>
            {r.tone}
          </span>
          <span>
            <span className="mono">{r.column}</span>: {r.description}
          </span>
        </li>
      ))}
    </ul>
  );
}

function toServerSet(name: string, layout: GridLayout, columns: { id: string }[]): ColumnSet {
  return {
    name,
    order: layout.order ?? columns.map((c) => c.id),
    hidden: Object.entries(layout.visibility).filter(([, on]) => on === false).map(([k]) => k),
    pinned: layout.pinned,
  };
}

function fromServerSet(set: ColumnSet): GridLayout {
  return {
    visibility: Object.fromEntries((set.hidden ?? []).map((k) => [k, false])),
    pinned: set.pinned ?? [],
    order: set.order ?? [],
  };
}

/** Saved column sets on the server, per operator and grid. */
function useServerSets(grid: string | null) {
  const path = grid === null ? null : PATHS.columnSets(grid);
  const state = useApi<Envelope<ColumnSetsView>>(path);
  const [message, setMessage] = useState<string | null>(null);
  const view = state.status === "success" ? state.data.data : null;
  const write = async (sets: ColumnSet[], defaultSet: string | null) => {
    if (grid === null || view === null) return;
    try {
      await saveColumnSets(grid, view.version, sets, defaultSet);
      setMessage(null);
    } catch (error) {
      setMessage(
        error instanceof ApiError && error.code === "workspace_conflict"
          ? "These sets changed in another tab; reloaded. Save again to apply your change."
          : `Not saved: ${error instanceof Error ? error.message : String(error)}`,
      );
    }
    state.reload();
  };
  return { view, state, message, write };
}
