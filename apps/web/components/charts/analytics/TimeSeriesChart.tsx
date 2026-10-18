"use client";

import { useMemo, useState, type KeyboardEvent, type PointerEvent, type ReactNode } from "react";
import { formatNumber, formatTimestamp, parseUtc } from "@/lib/format";
import type { SeriesPoint } from "@/lib/types";
import { useWidth } from "@/lib/use-width";
import { AnalyticsFrame, EmptyPlot, LegendSwatch } from "./AnalyticsFrame";
import { areaPath, extent, linePath, linear, nearestIndex, niceTicks, timeTicks } from "./scale";

export interface TimeLine {
  id: string;
  label: string;
  points: SeriesPoint[];
  /** A palette key: equity, benchmark, loss, s1 … s6 (analytics.css). */
  ink: string;
  dashed?: boolean;
  /** Fill between the line and the zero line (underwater). */
  area?: boolean;
}

export interface TimeBand {
  from: string;
  to: string | null;
  label: string;
}

const PAD = { l: 66, r: 12, t: 8, b: 22 };

interface Prepared {
  line: TimeLine;
  times: number[];
  values: (number | null)[];
}

/**
 * Lines over UTC time, drawn as SVG at the container's width. Several charts
 * can share one cursor (`cursor` / `onCursor`, a time in ms): the tearsheet
 * stacks equity, underwater and rolling Sharpe so one hover reads all three.
 * The arrow keys move the cursor point by point (Home/End jump), and every
 * value is in "View data".
 */
export function TimeSeriesChart({
  title,
  unit,
  lines,
  height = 220,
  baseline,
  baselineLabel,
  zeroLine = false,
  bands = [],
  cursor,
  onCursor,
  testId,
  meta,
  note,
  signed = false,
}: {
  title: string;
  unit: string;
  lines: TimeLine[];
  height?: number;
  baseline?: number | null;
  baselineLabel?: string;
  zeroLine?: boolean;
  bands?: TimeBand[];
  cursor?: number | null;
  onCursor?: (ms: number | null) => void;
  testId?: string;
  meta?: ReactNode;
  note?: ReactNode;
  /** Values are signed (P&L, returns): readout shows an explicit sign. */
  signed?: boolean;
}) {
  const [ref, width] = useWidth(900);
  const [ownCursor, setOwnCursor] = useState<number | null>(null);
  const cur = cursor === undefined ? ownCursor : cursor;
  const setCur = (ms: number | null) => (onCursor ? onCursor(ms) : setOwnCursor(ms));

  const prepared: Prepared[] = useMemo(
    () =>
      lines.map((line) => ({
        line,
        times: line.points.map((p) => parseUtc(p.t).getTime()),
        values: line.points.map((p) => p.v),
      })),
    [lines],
  );
  const allTimes = prepared.flatMap((p) => p.times);
  const hasData = prepared.some((p) => p.values.some((v) => v !== null));
  const u = unit === "index" ? "ratio" : unit;
  const fmt = (v: number) => (signed && v > 0 ? "+" : "") + formatNumber(v, u);

  if (!hasData) {
    return (
      <AnalyticsFrame title={title} unit={unitLabel(unit)} testId={testId} meta={meta} note={note}>
        <EmptyPlot message="No points were recorded for this series." />
      </AnalyticsFrame>
    );
  }

  const t0 = Math.min(...allTimes);
  const t1 = Math.max(...allTimes);
  const [y0, y1] = extent(
    prepared.flatMap((p) => p.values),
    zeroLine ? 0 : (baseline ?? undefined),
  );
  const x = linear([t0, t1], [PAD.l, width - PAD.r]);
  const y = linear([y0, y1], [height - PAD.b, PAD.t]);
  const yTicks = niceTicks(y0, y1, height > 180 ? 5 : 3);
  const xTicks = timeTicks(t0, t1, width > 700 ? 6 : 3);
  const primary = prepared[0]!;
  const ci = cur === null ? -1 : nearestIndex(primary.times, cur);

  const onMove = (e: PointerEvent<SVGSVGElement>) => {
    const box = e.currentTarget.getBoundingClientRect();
    const px = ((e.clientX - box.left) / box.width) * width;
    if (px < PAD.l || px > width - PAD.r) return;
    const ms = t0 + ((px - PAD.l) / (width - PAD.l - PAD.r)) * (t1 - t0);
    setCur(ms);
  };
  const onKey = (e: KeyboardEvent<SVGSVGElement>) => {
    const n = primary.times.length;
    if (n === 0) return;
    let i = ci < 0 ? n - 1 : ci;
    const step = e.shiftKey ? Math.max(1, Math.round(n / 20)) : 1;
    if (e.key === "ArrowLeft") i = Math.max(0, i - step);
    else if (e.key === "ArrowRight") i = Math.min(n - 1, i + step);
    else if (e.key === "Home") i = 0;
    else if (e.key === "End") i = n - 1;
    else if (e.key === "Escape") return setCur(null);
    else return;
    e.preventDefault();
    setCur(primary.times[i]!);
  };

  const readout =
    ci >= 0 ? (
      <span className="ax__readout-row">
        <span className="ax__readout-time">{formatTimestamp(primary.line.points[ci]!.t)}</span>
        {prepared.map((p) => {
          const j = nearestIndex(p.times, primary.times[ci]!);
          const v = j >= 0 ? p.values[j] : null;
          return (
            <span key={p.line.id} className="ax__readout-item" data-ink={p.line.ink}>
              {p.line.label} <b>{v === null || v === undefined ? "no data" : fmt(v)}</b>
            </span>
          );
        })}
      </span>
    ) : (
      <span className="ax__readout-hint">Hover, or focus and use the arrow keys, to read values.</span>
    );

  return (
    <AnalyticsFrame
      title={title}
      unit={unitLabel(unit)}
      testId={testId}
      meta={meta}
      note={note}
      legend={
        <>
          {lines.map((l) => (
            <LegendSwatch key={l.id} ink={l.ink} label={l.label} dashed={l.dashed} />
          ))}
          {bands.length ? <span className="ax__key ax__key--band">shaded: {bands[0]!.label}</span> : null}
        </>
      }
      data={() => <SeriesTable lines={lines} unit={unit} />}
    >
      <div className="ax__readout" aria-live="polite">
        {readout}
      </div>
      <div ref={ref} className="ax__plot">
        <svg
          width={width}
          height={height}
          viewBox={`0 0 ${width} ${height}`}
          role="img"
          aria-label={`${title}. ${lines.map((l) => l.label).join(", ")}. Values are in View data.`}
          tabIndex={0}
          onPointerMove={onMove}
          onPointerLeave={() => setCur(null)}
          onKeyDown={onKey}
          className="ax__svg"
        >
          {bands.map((b, i) => {
            const bx0 = x(parseUtc(b.from).getTime());
            const bx1 = b.to ? x(parseUtc(b.to).getTime()) : width - PAD.r;
            return (
              <rect
                key={i}
                className="ax__band"
                x={Math.max(PAD.l, bx0)}
                y={PAD.t}
                width={Math.max(1, bx1 - bx0)}
                height={height - PAD.t - PAD.b}
              />
            );
          })}
          {yTicks.map((v) => (
            <g key={`y${v}`}>
              <line className="ax__grid" x1={PAD.l} x2={width - PAD.r} y1={y(v)} y2={y(v)} />
              <text className="ax__tick" x={PAD.l - 6} y={y(v) + 3} textAnchor="end">
                {formatNumber(v, u)}
              </text>
            </g>
          ))}
          {xTicks.map((t, i) => (
            <text
              key={`x${i}`}
              className="ax__tick"
              x={x(t.ms)}
              y={height - 6}
              textAnchor={i === 0 ? "start" : i === xTicks.length - 1 ? "end" : "middle"}
            >
              {t.label}
            </text>
          ))}
          {zeroLine ? <line className="ax__zero" x1={PAD.l} x2={width - PAD.r} y1={y(0)} y2={y(0)} /> : null}
          {baseline !== undefined && baseline !== null ? (
            <g>
              <line className="ax__baseline" x1={PAD.l} x2={width - PAD.r} y1={y(baseline)} y2={y(baseline)} />
              {baselineLabel ? (
                <text className="ax__tick" x={width - PAD.r - 2} y={y(baseline) - 3} textAnchor="end">
                  {baselineLabel}
                </text>
              ) : null}
            </g>
          ) : null}
          {prepared.map((p) => {
            const xs = p.times.map((t) => x(t));
            const ys = p.values.map((v) => (v === null ? null : y(v)));
            return (
              <g key={p.line.id} className="ax__series" data-ink={p.line.ink} data-testid={`ax-line-${p.line.id}`}>
                {p.line.area ? <path className="ax__area" d={areaPath(xs, ys, y(0))} /> : null}
                <path className="ax__line" data-dashed={p.line.dashed || undefined} d={linePath(xs, ys)} />
              </g>
            );
          })}
          {ci >= 0 ? (
            <g>
              <line className="ax__cursor" x1={x(primary.times[ci]!)} x2={x(primary.times[ci]!)} y1={PAD.t} y2={height - PAD.b} />
              {prepared.map((p) => {
                const j = nearestIndex(p.times, primary.times[ci]!);
                const v = j >= 0 ? p.values[j] : null;
                if (v === null || v === undefined) return null;
                return (
                  <circle key={p.line.id} className="ax__dot" data-ink={p.line.ink} cx={x(p.times[j]!)} cy={y(v)} r={3} />
                );
              })}
            </g>
          ) : null}
          <line className="ax__axis" x1={PAD.l} x2={PAD.l} y1={PAD.t} y2={height - PAD.b} />
        </svg>
      </div>
    </AnalyticsFrame>
  );
}

export function unitLabel(unit: string): string {
  if (unit === "pct") return "%";
  if (unit === "ratio") return "ratio";
  if (unit === "index") return "rebased to 100";
  return unit;
}

function SeriesTable({ lines, unit }: { lines: TimeLine[]; unit: string }) {
  return (
    <>
      {lines.map((l) => (
        <table key={l.id} className="ax__table">
          <caption>
            {l.label} ({l.points.length} points)
          </caption>
          <thead>
            <tr>
              <th scope="col">Time (UTC)</th>
              <th scope="col" className="num">
                Value
              </th>
            </tr>
          </thead>
          <tbody>
            {l.points.map((p, i) => (
              <tr key={i}>
                <td>{formatTimestamp(p.t)}</td>
                <td className="num">{p.v === null ? "no data" : formatNumber(p.v, unit === "index" ? "ratio" : unit)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      ))}
    </>
  );
}
