"use client";

import { useState, type ReactNode } from "react";
import { formatNumber } from "@/lib/format";
import { useWidth } from "@/lib/use-width";
import { AnalyticsFrame, EmptyPlot } from "./AnalyticsFrame";
import { extent, linear, niceTicks } from "./scale";

export interface ScatterPoint {
  id: string;
  x: number;
  y: number;
  /** Win, loss or flat: drawn as a shape AND a colour, never colour alone. */
  outcome: "win" | "loss" | "flat";
  label: string;
}

const PAD = { l: 56, r: 12, t: 10, b: 30 };

/** Per-trade scatter (MAE or MFE against the trade's result). */
export function ScatterChart({
  title,
  xLabel,
  yLabel,
  unit,
  points,
  height = 260,
  diagonal = false,
  testId,
  meta,
  note,
  dataTable,
}: {
  title: string;
  xLabel: string;
  yLabel: string;
  unit: string;
  points: ScatterPoint[];
  height?: number;
  /** Draw y = x and y = -x guides (excursion versus result). */
  diagonal?: boolean;
  testId?: string;
  meta?: ReactNode;
  note?: ReactNode;
  dataTable?: () => ReactNode;
}) {
  const [ref, width] = useWidth(600);
  const [hover, setHover] = useState<number | null>(null);
  if (points.length === 0) {
    return (
      <AnalyticsFrame title={title} unit={unit} testId={testId} meta={meta} note={note}>
        <EmptyPlot message="No trade carries both values." />
      </AnalyticsFrame>
    );
  }
  const [x0, x1] = extent(points.map((p) => p.x), 0);
  const [y0, y1] = extent(points.map((p) => p.y), 0);
  const x = linear([x0, x1], [PAD.l, width - PAD.r]);
  const y = linear([y0, y1], [height - PAD.b, PAD.t]);
  const h = hover === null ? null : points[hover];
  return (
    <AnalyticsFrame
      title={title}
      unit={unit}
      testId={testId}
      meta={meta}
      note={note}
      data={dataTable}
      legend={
        <>
          <span className="ax__key" data-ink="win">
            <svg width="10" height="10" aria-hidden="true">
              <circle cx="5" cy="5" r="3.5" className="ax__pt" data-outcome="win" />
            </svg>
            winner
          </span>
          <span className="ax__key" data-ink="loss">
            <svg width="10" height="10" aria-hidden="true">
              <rect x="1.5" y="1.5" width="7" height="7" className="ax__pt" data-outcome="loss" />
            </svg>
            loser
          </span>
        </>
      }
    >
      <div className="ax__readout" aria-live="polite">
        <span className="ax__readout-hint">
          {h
            ? `${h.label}: ${xLabel} ${formatNumber(h.x, unit)}, ${yLabel} ${formatNumber(h.y, unit)}`
            : "Hover or focus a point to read the trade."}
        </span>
      </div>
      <div ref={ref} className="ax__plot">
        <svg
          width={width}
          height={height}
          viewBox={`0 0 ${width} ${height}`}
          role="img"
          aria-label={`${title}. Use the arrow keys to step through the trades; values are in View data.`}
          className="ax__svg"
          tabIndex={0}
          onKeyDown={(e) => {
            const n = points.length;
            if (e.key === "ArrowRight" || e.key === "ArrowDown") setHover((h) => (h === null ? 0 : Math.min(n - 1, h + 1)));
            else if (e.key === "ArrowLeft" || e.key === "ArrowUp") setHover((h) => (h === null ? n - 1 : Math.max(0, h - 1)));
            else if (e.key === "Escape") setHover(null);
            else return;
            e.preventDefault();
          }}
          onBlur={() => setHover(null)}
        >
          {niceTicks(y0, y1, 4).map((v) => (
            <g key={`y${v}`}>
              <line className="ax__grid" x1={PAD.l} x2={width - PAD.r} y1={y(v)} y2={y(v)} />
              <text className="ax__tick" x={PAD.l - 5} y={y(v) + 3} textAnchor="end">
                {formatNumber(v, unit)}
              </text>
            </g>
          ))}
          {niceTicks(x0, x1, width > 500 ? 6 : 3).map((v) => (
            <text key={`x${v}`} className="ax__tick" x={x(v)} y={height - 16} textAnchor="middle">
              {formatNumber(v, unit)}
            </text>
          ))}
          <text className="ax__label" x={width - PAD.r} y={height - 3} textAnchor="end">
            {xLabel} →
          </text>
          <text className="ax__label" x={PAD.l + 4} y={PAD.t + 9}>
            ↑ {yLabel}
          </text>
          <line className="ax__zero" x1={PAD.l} x2={width - PAD.r} y1={y(0)} y2={y(0)} />
          <line className="ax__zero" x1={x(0)} x2={x(0)} y1={PAD.t} y2={height - PAD.b} />
          {diagonal ? (
            <line
              className="ax__guide"
              x1={x(Math.max(x0, y0))}
              y1={y(Math.max(x0, y0))}
              x2={x(Math.min(x1, y1))}
              y2={y(Math.min(x1, y1))}
            />
          ) : null}
          {points.map((p, i) => {
            const common = {
              className: "ax__pt",
              "data-outcome": p.outcome,
              "data-active": hover === i || undefined,
              onPointerEnter: () => setHover(i),
              onPointerLeave: () => setHover(null),
              "data-testid": "ax-point",
            };
            return p.outcome === "loss" ? (
              <rect key={p.id} x={x(p.x) - 3} y={y(p.y) - 3} width={6} height={6} {...common} />
            ) : (
              <circle key={p.id} cx={x(p.x)} cy={y(p.y)} r={3.4} {...common} />
            );
          })}
          <line className="ax__axis" x1={PAD.l} x2={PAD.l} y1={PAD.t} y2={height - PAD.b} />
        </svg>
      </div>
    </AnalyticsFrame>
  );
}
