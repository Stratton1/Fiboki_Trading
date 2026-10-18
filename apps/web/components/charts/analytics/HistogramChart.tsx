"use client";

import { useState, type ReactNode } from "react";
import { formatNumber } from "@/lib/format";
import { useWidth } from "@/lib/use-width";
import { AnalyticsFrame, EmptyPlot } from "./AnalyticsFrame";
import { linear, niceTicks } from "./scale";

const PAD = { l: 40, r: 10, t: 10, b: 24 };

/**
 * A server-binned distribution (edges and counts from the API). A bar wholly
 * above zero is profit-coloured, wholly below loss-coloured, one spanning zero
 * neutral; the mean and median arrive with the bins and are drawn as rules.
 */
export function HistogramChart({
  title,
  unit,
  edges,
  counts,
  mean,
  median,
  height = 200,
  testId,
  meta,
  note,
}: {
  title: string;
  unit: string;
  edges: number[];
  counts: number[];
  mean: number | null;
  median: number | null;
  height?: number;
  testId?: string;
  meta?: ReactNode;
  note?: ReactNode;
}) {
  const [ref, width] = useWidth(600);
  const [hover, setHover] = useState<number | null>(null);
  if (counts.length === 0 || edges.length !== counts.length + 1) {
    return (
      <AnalyticsFrame title={title} unit={unit} testId={testId} meta={meta}>
        <EmptyPlot message="No closed trades to distribute." />
      </AnalyticsFrame>
    );
  }
  const lo = edges[0]!;
  const hi = edges[edges.length - 1]!;
  const max = Math.max(...counts);
  const x = linear([lo, hi], [PAD.l, width - PAD.r]);
  const y = linear([0, max], [height - PAD.b, PAD.t]);
  const xTicks = niceTicks(lo, hi, width > 500 ? 6 : 3);
  const yTicks = niceTicks(0, max, 3).filter((v) => Number.isInteger(v));
  const rule = (v: number | null, cls: string, label: string) =>
    v === null ? null : (
      <g>
        <line className={cls} x1={x(v)} x2={x(v)} y1={PAD.t} y2={height - PAD.b} />
        <text className="ax__tick" x={x(v) + 3} y={PAD.t + 9}>
          {label}
        </text>
      </g>
    );
  const readout =
    hover === null
      ? "Hover or focus a bar to read it."
      : `${formatNumber(edges[hover]!, unit)} to ${formatNumber(edges[hover + 1]!, unit)}: ${counts[hover]} trade(s)`;
  return (
    <AnalyticsFrame
      title={title}
      unit={unit}
      testId={testId}
      meta={meta}
      note={note}
      legend={
        <span className="ax__key">
          mean {mean === null ? "no data" : formatNumber(mean, unit)} · median{" "}
          {median === null ? "no data" : formatNumber(median, unit)} · n = {counts.reduce((a, b) => a + b, 0)}
        </span>
      }
      data={() => (
        <table className="ax__table">
          <thead>
            <tr>
              <th scope="col">From</th>
              <th scope="col">To</th>
              <th scope="col" className="num">
                Trades
              </th>
            </tr>
          </thead>
          <tbody>
            {counts.map((c, i) => (
              <tr key={i}>
                <td className="num">{formatNumber(edges[i]!, unit)}</td>
                <td className="num">{formatNumber(edges[i + 1]!, unit)}</td>
                <td className="num">{c}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    >
      <div className="ax__readout" aria-live="polite">
        <span className="ax__readout-hint">{readout}</span>
      </div>
      <div ref={ref} className="ax__plot">
        <svg
          width={width}
          height={height}
          viewBox={`0 0 ${width} ${height}`}
          role="img"
          aria-label={`${title}. Use the arrow keys to read each bin; values are in View data.`}
          className="ax__svg"
          tabIndex={0}
          onKeyDown={(e) => {
            const n = counts.length;
            if (e.key === "ArrowRight") setHover((h) => (h === null ? 0 : Math.min(n - 1, h + 1)));
            else if (e.key === "ArrowLeft") setHover((h) => (h === null ? n - 1 : Math.max(0, h - 1)));
            else if (e.key === "Escape") setHover(null);
            else return;
            e.preventDefault();
          }}
          onBlur={() => setHover(null)}
        >
          {yTicks.map((v) => (
            <g key={v}>
              <line className="ax__grid" x1={PAD.l} x2={width - PAD.r} y1={y(v)} y2={y(v)} />
              <text className="ax__tick" x={PAD.l - 5} y={y(v) + 3} textAnchor="end">
                {v}
              </text>
            </g>
          ))}
          {counts.map((c, i) => {
            const a = edges[i]!;
            const b = edges[i + 1]!;
            const side = b <= 0 ? "down" : a >= 0 ? "up" : "zero";
            return (
              <rect
                key={i}
                className="ax__bin"
                data-side={side}
                data-active={hover === i || undefined}
                x={x(a) + 0.5}
                y={y(c)}
                width={Math.max(1, x(b) - x(a) - 1)}
                height={Math.max(0, height - PAD.b - y(c))}
                onPointerEnter={() => setHover(i)}
                onPointerLeave={() => setHover(null)}
                data-testid="ax-bin"
              />
            );
          })}
          {lo < 0 && hi > 0 ? <line className="ax__zero" x1={x(0)} x2={x(0)} y1={PAD.t} y2={height - PAD.b} /> : null}
          {rule(mean, "ax__rule", "mean")}
          {rule(median, "ax__rule ax__rule--alt", "median")}
          {xTicks.map((v) => (
            <text key={v} className="ax__tick" x={x(v)} y={height - 6} textAnchor="middle">
              {formatNumber(v, unit)}
            </text>
          ))}
          <line className="ax__axis" x1={PAD.l} x2={width - PAD.r} y1={height - PAD.b} y2={height - PAD.b} />
        </svg>
      </div>
    </AnalyticsFrame>
  );
}
