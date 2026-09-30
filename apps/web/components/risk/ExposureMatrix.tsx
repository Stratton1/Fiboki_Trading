"use client";

import { useState } from "react";
import { formatNumber } from "@/lib/format";
import { exposureKind, exposureRows, utilisationBin, type LimitRow } from "@/lib/limits";
import { deriveProvenance } from "@/lib/provenance";
import type { ExposureRow } from "@/lib/types";
import { FigureValue } from "../FigureValue";
import { ProvenanceLabelChip } from "../ProvenanceChip";
import { LimitStateTag } from "./LimitBoard";

/**
 * Exposure as a matrix of heat cells: one row per bucket kind the API
 * reports (instrument, currency, strategy), one cell per bucket, in the API's
 * order, shaded by how much of that bucket's limit is used.
 *
 * Owned SVG, no chart library. The shade is a single-hue lightness ramp in
 * five 20% steps (tokens `--heat-util-1..5`), so it reads under any colour
 * vision deficiency and in greyscale; every cell also prints its utilisation,
 * and a cell at or over a display band carries the band's glyph and outline.
 * A bucket with no utilisation (no equity to divide by) is hatched and says
 * "no data"; it is never shaded as zero.
 *
 * "View data" is the same rows as a table, in the matrix's order, every value
 * the cells draw.
 */

const KIND_ORDER = ["instrument", "currency", "strategy"];
const KIND_LABEL: Record<string, string> = {
  instrument: "Instrument",
  currency: "Currency",
  strategy: "Strategy",
};

const CELL_W = 104;
const CELL_H = 52;
const GAP = 10;
const LABEL_W = 92;

const BAND_GLYPH: Record<string, string> = { warn: " ◐", critical: " ◆", breached: " ✕" };

function kindsOf(items: readonly ExposureRow[]): string[] {
  const seen: string[] = [];
  for (const row of items) {
    const kind = exposureKind(row);
    if (!seen.includes(kind)) seen.push(kind);
  }
  const known = KIND_ORDER.filter((k) => seen.includes(k));
  return [...known, ...seen.filter((k) => !KIND_ORDER.includes(k))];
}

function usedText(row: LimitRow): string {
  return row.utilisation === null ? "no data" : formatNumber(row.utilisation, "pct", { decimals: 1 });
}

function short(label: string): string {
  return label.length > 12 ? `${label.slice(0, 11)}…` : label;
}

export function ExposureMatrix({ items }: { items: readonly ExposureRow[] }) {
  const rows = exposureRows(items);
  const kinds = kindsOf(items);
  const byKind = kinds.map((kind) => ({ kind, cells: rows.filter((r) => r.kind === kind) }));
  const cols = Math.max(1, ...byKind.map((k) => k.cells.length));
  const width = LABEL_W + cols * (CELL_W + GAP);
  const height = byKind.length * (CELL_H + GAP);
  // Cells start GAP/2 in, so a band outline (3px outside a cell) is never clipped.
  const inset = GAP / 2;
  const label = deriveProvenance(items.flatMap((r) => [r.exposure_pct.provenance, r.limit_pct.provenance]));

  return (
    <div className="xmatrix" data-testid="exposure-matrix">
      <div className="xmatrix__caption">
        <strong>Utilisation of each bucket&apos;s limit</strong>
        <ProvenanceLabelChip label={label} />
        <span className="muted">
          {rows.length} bucket{rows.length === 1 ? "" : "s"}; darker is closer to its limit
        </span>
      </div>
      <div
        className="xmatrix__scroll"
        tabIndex={0}
        role="region"
        aria-label="Exposure matrix, scrolls sideways. Every value is also in View data."
        data-testid="exposure-matrix-scroll"
      >
        <svg
          viewBox={`0 0 ${width} ${height}`}
          width={width}
          height={height}
          role="img"
          aria-label="Exposure utilisation by bucket. The values are in View data."
        >
          {byKind.map(({ kind, cells }, r) => {
            const y = inset + r * (CELL_H + GAP);
            return (
              <g key={kind} data-testid="exposure-matrix-row" data-kind={kind}>
                <text x={0} y={y + CELL_H / 2 + 4} className="xmatrix__kind">
                  {KIND_LABEL[kind] ?? kind}
                </text>
                {cells.map((row, c) => {
                  const x = LABEL_W + inset + c * (CELL_W + GAP);
                  const bin = row.utilisation === null ? "none" : String(utilisationBin(row.utilisation));
                  return (
                    <g
                      key={row.key}
                      data-testid="exposure-cell"
                      data-key={row.key.slice("exposure:".length)}
                      data-state={row.state}
                      data-used={usedText(row)}
                    >
                      <rect
                        x={x}
                        y={y}
                        width={CELL_W}
                        height={CELL_H}
                        rx={3}
                        className="xheat"
                        data-bin={bin}
                        data-state={row.state}
                      />
                      {BAND_GLYPH[row.state] ? (
                        // The band outline sits OUTSIDE the cell, on the
                        // surface, so its contrast never depends on the shade.
                        <rect
                          x={x - 3}
                          y={y - 3}
                          width={CELL_W + 6}
                          height={CELL_H + 6}
                          rx={5}
                          className="xheat__band"
                          data-state={row.state}
                        />
                      ) : null}
                      <text x={x + 8} y={y + 20} className="xheat__label">
                        {short(row.label)}
                      </text>
                      <text x={x + 8} y={y + 40} className="xheat__value">
                        {usedText(row)}
                        {BAND_GLYPH[row.state] ?? ""}
                      </text>
                    </g>
                  );
                })}
              </g>
            );
          })}
        </svg>
      </div>
      <div className="heat-legend xmatrix__legend" aria-hidden="true">
        <span>0%</span>
        {[1, 2, 3, 4, 5].map((bin) => (
          <span key={bin} className="heat-legend__swatch xheat-swatch" data-bin={bin} />
        ))}
        <span>100% of limit</span>
        <span className="xheat-swatch xheat-swatch--none heat-legend__swatch" />
        <span>no data</span>
      </div>
      <ExposureTable rows={byKind.flatMap((k) => k.cells)} />
    </div>
  );
}

/** "View data": a native disclosure, built only when opened. */
function ExposureTable({ rows }: { rows: readonly LimitRow[] }) {
  const [open, setOpen] = useState(false);
  return (
    <details
      className="chart__data"
      data-testid="exposure-data"
      onToggle={(event) => setOpen(event.currentTarget.open)}
    >
      <summary>View data</summary>
      {open ? (
        <div className="table-wrap chart__data-table" tabIndex={0} role="region" aria-label="Exposure data">
          <table>
            <caption className="sr-only">Exposure by bucket: every value the matrix draws</caption>
            <thead>
              <tr>
                <th scope="col">Kind</th>
                <th scope="col">Bucket</th>
                <th scope="col" className="num">
                  Exposure (% equity)
                </th>
                <th scope="col" className="num">
                  Limit
                </th>
                <th scope="col" className="num">
                  Used
                </th>
                <th scope="col">State</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((row) => (
                <tr
                  key={row.key}
                  data-testid="exposure-data-row"
                  data-key={row.key.slice("exposure:".length)}
                  data-used={usedText(row)}
                >
                  <td>{row.kind}</td>
                  <th scope="row">{row.label}</th>
                  <td>{row.value ? <FigureValue figure={row.value} showChip={false} /> : "—"}</td>
                  <td>{row.limit ? <FigureValue figure={row.limit} showChip={false} /> : "—"}</td>
                  <td className="num">{usedText(row)}</td>
                  <td>
                    <LimitStateTag state={row.state} />
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : null}
    </details>
  );
}
