"use client";

import { useState, type PointerEvent, type ReactNode } from "react";
import { formatNumber } from "@/lib/format";
import { useWidth } from "@/lib/use-width";
import { AnalyticsFrame, EmptyPlot, LegendSwatch } from "./AnalyticsFrame";
import { extent, linePath, linear, niceTicks } from "./scale";

const PAD = { l: 70, r: 12, t: 10, b: 26 };

/**
 * A Monte Carlo fan: the server's percentile paths per trade step (p5, p25,
 * p50, p75, p95), drawn as two nested bands and a median, against the initial
 * equity. Nothing is resampled here.
 */
export function FanChart({
  title,
  unit,
  steps,
  fan,
  initial,
  height = 260,
  testId,
  meta,
  note,
}: {
  title: string;
  unit: string;
  steps: number[];
  fan: Record<string, number[]>;
  initial: number;
  height?: number;
  testId?: string;
  meta?: ReactNode;
  note?: ReactNode;
}) {
  const [ref, width] = useWidth(800);
  const [hover, setHover] = useState<number | null>(null);
  const p5 = fan.p5 ?? [];
  const p25 = fan.p25 ?? [];
  const p50 = fan.p50 ?? [];
  const p75 = fan.p75 ?? [];
  const p95 = fan.p95 ?? [];
  if (steps.length < 2 || p50.length !== steps.length) {
    return (
      <AnalyticsFrame title={title} unit={unit} testId={testId} meta={meta}>
        <EmptyPlot message="The simulation returned no paths." />
      </AnalyticsFrame>
    );
  }
  const last = steps[steps.length - 1]!;
  const [y0, y1] = extent([...p5, ...p95], initial);
  const x = linear([0, last], [PAD.l, width - PAD.r]);
  const y = linear([y0, y1], [height - PAD.b, PAD.t]);
  const xs = steps.map((s) => x(s));
  const band = (lo: number[], hi: number[]) => {
    let d = "";
    steps.forEach((_, i) => {
      d += `${i === 0 ? "M" : "L"}${xs[i]!.toFixed(1)},${y(hi[i]!).toFixed(1)}`;
    });
    for (let i = steps.length - 1; i >= 0; i -= 1) d += `L${xs[i]!.toFixed(1)},${y(lo[i]!).toFixed(1)}`;
    return `${d}Z`;
  };
  const onMove = (e: PointerEvent<SVGSVGElement>) => {
    const box = e.currentTarget.getBoundingClientRect();
    const px = ((e.clientX - box.left) / box.width) * width;
    let best = 0;
    xs.forEach((v, i) => {
      if (Math.abs(v - px) < Math.abs(xs[best]! - px)) best = i;
    });
    setHover(best);
  };
  return (
    <AnalyticsFrame
      title={title}
      unit={unit}
      testId={testId}
      meta={meta}
      note={note}
      legend={
        <>
          <span className="ax__key ax__key--band" data-ink="fan-outer">
            5th to 95th percentile
          </span>
          <span className="ax__key ax__key--band" data-ink="fan-inner">
            25th to 75th
          </span>
          <LegendSwatch ink="equity" label="median" />
          <LegendSwatch ink="benchmark" label="initial equity" dashed />
        </>
      }
      data={() => (
        <table className="ax__table">
          <thead>
            <tr>
              <th scope="col" className="num">
                Trade
              </th>
              {["p5", "p25", "p50", "p75", "p95"].map((k) => (
                <th key={k} scope="col" className="num">
                  {k}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {steps.map((s, i) => (
              <tr key={s}>
                <td className="num">{s}</td>
                {[p5, p25, p50, p75, p95].map((col, j) => (
                  <td key={j} className="num">
                    {formatNumber(col[i]!, unit)}
                  </td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      )}
    >
      <div className="ax__readout" aria-live="polite">
        <span className="ax__readout-hint">
          {hover === null
            ? "Hover to read the percentiles after a given number of trades."
            : `After ${steps[hover]} trade(s): p5 ${formatNumber(p5[hover]!, unit)} · p25 ${formatNumber(p25[hover]!, unit)} · median ${formatNumber(p50[hover]!, unit)} · p75 ${formatNumber(p75[hover]!, unit)} · p95 ${formatNumber(p95[hover]!, unit)}`}
        </span>
      </div>
      <div ref={ref} className="ax__plot">
        <svg
          width={width}
          height={height}
          viewBox={`0 0 ${width} ${height}`}
          role="img"
          aria-label={`${title}. Percentile table in View data.`}
          className="ax__svg"
          onPointerMove={onMove}
          onPointerLeave={() => setHover(null)}
        >
          {niceTicks(y0, y1, 5).map((v) => (
            <g key={v}>
              <line className="ax__grid" x1={PAD.l} x2={width - PAD.r} y1={y(v)} y2={y(v)} />
              <text className="ax__tick" x={PAD.l - 5} y={y(v) + 3} textAnchor="end">
                {formatNumber(v, unit)}
              </text>
            </g>
          ))}
          {niceTicks(0, last, width > 600 ? 8 : 4)
            .filter((v) => Number.isInteger(v))
            .map((v) => (
              <text key={v} className="ax__tick" x={x(v)} y={height - 8} textAnchor="middle">
                {v}
              </text>
            ))}
          <path className="ax__fan" data-band="outer" d={band(p5, p95)} />
          <path className="ax__fan" data-band="inner" d={band(p25, p75)} />
          <line className="ax__baseline" x1={PAD.l} x2={width - PAD.r} y1={y(initial)} y2={y(initial)} />
          <g className="ax__series" data-ink="equity">
            <path className="ax__line" d={linePath(xs, p50.map((v) => y(v)))} />
          </g>
          {hover !== null ? (
            <line className="ax__cursor" x1={xs[hover]} x2={xs[hover]} y1={PAD.t} y2={height - PAD.b} />
          ) : null}
          <text className="ax__label" x={width - PAD.r} y={height - 0} textAnchor="end">
            trades →
          </text>
          <line className="ax__axis" x1={PAD.l} x2={PAD.l} y1={PAD.t} y2={height - PAD.b} />
        </svg>
      </div>
    </AnalyticsFrame>
  );
}
