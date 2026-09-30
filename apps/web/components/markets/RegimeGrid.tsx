"use client";

import { useId } from "react";
import type { RegimeRow } from "@/lib/types";

/**
 * Instruments × regime measures as heat cells (V-8), reusing the exposure
 * matrix's cells (`xheat`, globals.css): "where is the market trending or
 * stressed, and where do we not know?"
 *
 * Every value is the API's (GET /api/markets/regimes, RegimeView), printed in
 * its cell. The shade is only the value's position in the classifier's own
 * declared order for that measure (marketstate/regime.py: e.g. volatility
 * very_low → extreme, stress calm → stressed), lighter first; it is an aid to
 * scanning, never a score. UNKNOWN (no classification, a null, or `unknown`)
 * is HATCHED, never shaded as a real state, and a value the workstation does
 * not recognise is hatched and says so rather than being guessed into place.
 */

const AXES = ["volatility", "direction", "persistence", "liquidity", "stress"] as const;
type Axis = (typeof AXES)[number];

/** Declared order per axis, from marketstate/regime.py (UNKNOWN excluded). */
const ORDER: Record<Axis, readonly string[]> = {
  volatility: ["very_low", "low", "normal", "high", "extreme"],
  direction: ["strong_down", "down", "neutral", "up", "strong_up"],
  persistence: ["mean_reverting", "random", "trending"],
  liquidity: ["off_hours", "thin", "normal", "deep"],
  stress: ["calm", "elevated", "stressed"],
};

const LABEL: Record<Axis, string> = {
  volatility: "Volatility",
  direction: "Direction",
  persistence: "Persistence",
  liquidity: "Liquidity",
  stress: "Stress",
};

const LABEL_W = 92;
const HEAD_H = 22;
const CELL_W = 112;
const CELL_H = 40;
const GAP = 6;

export type RegimeCell =
  | { state: "known"; value: string; bin: number }
  | { state: "unknown"; value: null }
  | { state: "unrecognised"; value: string };

/** A cell's state and shade bin (1 to 5), from the value the API sent. */
export function regimeCell(row: RegimeRow, axis: Axis): RegimeCell {
  const value = row[axis];
  if (!row.available || value === null || value === "" || value === "unknown") return { state: "unknown", value: null };
  const order = ORDER[axis];
  const index = order.indexOf(value);
  if (index === -1) return { state: "unrecognised", value };
  const bin = order.length === 1 ? 3 : Math.round(1 + (index * 4) / (order.length - 1));
  return { state: "known", value, bin };
}

function words(value: string): string {
  return value.replace(/_/g, " ");
}

export function RegimeGrid({ rows }: { rows: readonly RegimeRow[] }) {
  const hatchId = `regime-hatch-${useId().replace(/[^a-zA-Z0-9_-]/g, "")}`;
  const width = LABEL_W + AXES.length * (CELL_W + GAP);
  const height = HEAD_H + rows.length * (CELL_H + GAP);
  const known = rows.filter((row) => row.available).length;
  return (
    <div className="xmatrix regime-grid" data-testid="regime-grid">
      <div className="xmatrix__caption">
        <strong>Regime by instrument and measure</strong>
        <span className="muted" data-testid="regime-grid-count">
          {known} of {rows.length} instrument{rows.length === 1 ? "" : "s"} classified; hatched is unknown
        </span>
      </div>
      <div
        className="xmatrix__scroll"
        tabIndex={0}
        role="region"
        aria-label="Regime grid, scrolls sideways. Every value is also in the table below."
      >
        <svg
          viewBox={`0 0 ${width} ${height}`}
          width={width}
          height={height}
          role="img"
          aria-label={`Regime measures for ${rows.length} instruments; ${rows.length - known} unknown. The values are in the table below.`}
        >
          <defs>
            <pattern id={hatchId} patternUnits="userSpaceOnUse" width={6} height={6} patternTransform="rotate(45)">
              <line className="chart__hatch-line" x1={0} y1={0} x2={0} y2={6} />
            </pattern>
          </defs>
          {AXES.map((axis, c) => (
            <text
              key={axis}
              className="xmatrix__kind"
              x={LABEL_W + c * (CELL_W + GAP) + 4}
              y={14}
            >
              {LABEL[axis]}
            </text>
          ))}
          {rows.map((row, r) => {
            const y = HEAD_H + r * (CELL_H + GAP);
            return (
              <g key={row.instrument} data-testid="regime-grid-row" data-instrument={row.instrument}>
                <text className="xheat__label" x={0} y={y + CELL_H / 2 + 4}>
                  {row.instrument}
                </text>
                {AXES.map((axis, c) => {
                  const cell = regimeCell(row, axis);
                  const x = LABEL_W + c * (CELL_W + GAP);
                  const text =
                    cell.state === "known" ? words(cell.value) : cell.state === "unknown" ? "unknown" : `? ${words(cell.value)}`;
                  return (
                    <g
                      key={axis}
                      data-testid="regime-cell"
                      data-axis={axis}
                      data-state={cell.state}
                      data-value={cell.value ?? undefined}
                    >
                      <title>{`${row.instrument} ${LABEL[axis].toLowerCase()}: ${
                        cell.state === "known"
                          ? words(cell.value)
                          : cell.state === "unknown"
                            ? `unknown. ${row.detail}`
                            : `${cell.value} (not a value the workstation recognises)`
                      }`}</title>
                      <rect
                        className="xheat"
                        data-bin={cell.state === "known" ? String(cell.bin) : "none"}
                        x={x}
                        y={y}
                        width={CELL_W}
                        height={CELL_H}
                        rx={3}
                      />
                      {cell.state === "known" ? null : (
                        <rect
                          className="regime-grid__hatch"
                          fill={`url(#${hatchId})`}
                          x={x}
                          y={y}
                          width={CELL_W}
                          height={CELL_H}
                          rx={3}
                        />
                      )}
                      <text className="regime-grid__value" data-state={cell.state} x={x + 8} y={y + CELL_H / 2 + 4}>
                        {text}
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
        <span>first in the classifier&apos;s order</span>
        {[1, 2, 3, 4, 5].map((bin) => (
          <span key={bin} className="heat-legend__swatch xheat-swatch" data-bin={bin} />
        ))}
        <span>last</span>
        <span className="xheat-swatch xheat-swatch--none regime-grid__swatch-unknown heat-legend__swatch" />
        <span>unknown</span>
      </div>
    </div>
  );
}
