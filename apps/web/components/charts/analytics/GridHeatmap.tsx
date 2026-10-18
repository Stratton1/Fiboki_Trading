"use client";

import type { ReactNode } from "react";
import { AnalyticsFrame } from "./AnalyticsFrame";

export interface HeatCell {
  /** The value as text, already formatted. Shown in the cell (never colour alone). */
  text: string;
  /** Diverging bin −5 … +5 (analytics.css), or null for an empty / missing cell. */
  bin: number | null;
  /** Accessible description of the cell (row, column, value, marks). */
  label: string;
  /** Extra state flags drawn as outlines/glyphs: plateau, peak, selected… */
  marks?: string[];
  onSelect?: () => void;
  testId?: string;
}

/**
 * A heatmap as an HTML table: every cell prints its value, so a colour-blind
 * reader and a screen reader get the same information as the colour. Used for
 * monthly returns, correlation matrices and the parameter surface.
 */
export function GridHeatmap({
  title,
  unit,
  rowHeader,
  rowLabels,
  colLabels,
  cells,
  extraCols,
  meta,
  legend,
  note,
  testId,
  dense = false,
  rotateCols = false,
}: {
  title: string;
  unit?: string;
  rowHeader: string;
  rowLabels: ReactNode[];
  colLabels: ReactNode[];
  cells: (HeatCell | null)[][];
  /** Trailing non-heat columns (a year total, say): header and per-row content. */
  extraCols?: { header: ReactNode; cells: ReactNode[] }[];
  meta?: ReactNode;
  legend?: ReactNode;
  note?: ReactNode;
  testId?: string;
  dense?: boolean;
  rotateCols?: boolean;
}) {
  return (
    <AnalyticsFrame title={title} unit={unit} meta={meta} legend={legend} note={note} testId={testId}>
      <div className="ax__heat-scroll" tabIndex={0} role="region" aria-label={`${title}, scrolls sideways`}>
        <table className="ax__heat" data-dense={dense || undefined} data-rotate={rotateCols || undefined}>
          <caption className="sr-only">{title}</caption>
          <thead>
            <tr>
              <th scope="col" className="ax__heat-corner">
                {rowHeader}
              </th>
              {colLabels.map((c, i) => (
                <th key={i} scope="col" className="ax__heat-col">
                  <span>{c}</span>
                </th>
              ))}
              {extraCols?.map((e, i) => (
                <th key={`e${i}`} scope="col" className="ax__heat-col ax__heat-extra">
                  {e.header}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {rowLabels.map((r, ri) => (
              <tr key={ri}>
                <th scope="row" className="ax__heat-row">
                  {r}
                </th>
                {colLabels.map((_, ci) => {
                  const cell = cells[ri]?.[ci] ?? null;
                  if (cell === null) {
                    return (
                      <td key={ci} className="ax__heat-cell" data-empty="true">
                        <span className="sr-only">no value</span>
                      </td>
                    );
                  }
                  const marks = cell.marks ?? [];
                  const body = (
                    <>
                      <span className="ax__heat-text">{cell.text}</span>
                      {marks.includes("peak") ? (
                        <span className="ax__heat-glyph" aria-hidden="true">
                          ▲
                        </span>
                      ) : null}
                    </>
                  );
                  return (
                    <td
                      key={ci}
                      className="ax__heat-cell heat-swatch"
                      data-bin={cell.bin ?? undefined}
                      data-empty={cell.bin === null || undefined}
                      data-marks={marks.join(" ") || undefined}
                      data-testid={cell.testId}
                    >
                      {cell.onSelect ? (
                        <button type="button" className="ax__heat-btn" aria-label={cell.label} onClick={cell.onSelect}>
                          {body}
                        </button>
                      ) : (
                        <span className="ax__heat-static" title={cell.label}>
                          {body}
                          <span className="sr-only">{cell.label}</span>
                        </span>
                      )}
                    </td>
                  );
                })}
                {extraCols?.map((e, i) => (
                  <td key={`e${i}`} className="ax__heat-extra num">
                    {e.cells[ri]}
                  </td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </AnalyticsFrame>
  );
}

export { divergingBin } from "./scale";

export function HeatLegend({ low, high }: { low: string; high: string }) {
  return (
    <span className="ax__heat-legend" aria-hidden="true">
      <span>{low}</span>
      {[-5, -4, -3, -2, -1, 0, 1, 2, 3, 4, 5].map((b) => (
        <span key={b} className="heat-legend__swatch heat-swatch" data-bin={b} />
      ))}
      <span>{high}</span>
    </span>
  );
}
