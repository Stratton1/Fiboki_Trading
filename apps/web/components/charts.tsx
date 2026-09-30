"use client";

import { useId, useState, type KeyboardEvent, type ReactNode } from "react";
import type { Provenance, Series } from "@/lib/types";
import { useWidth } from "@/lib/use-width";
import { PROVENANCE_LABEL, formatNumber, formatTimestamp, parseUtc } from "@/lib/format";
import { singleProvenance, type ProvenanceLabel } from "@/lib/provenance";
import { ProvenanceLabelChip } from "./ProvenanceChip";

/**
 * Charts, hand-rolled as inline SVG (Wave 3 refresh).
 *
 * V1 shipped the full Plotly distribution (about 4.5 MB including mapbox-gl)
 * to draw line charts. These cost no vendor JavaScript.
 *
 * Every chart takes a provenance and prints the chip in its own caption,
 * because a chart is a pile of numbers and the same labelling rule applies.
 * Charts over many rows take a `ProvenanceLabel` derived from those rows
 * (`lib/provenance.ts`), so a mixed set renders MIXED rather than the first
 * row's label, and a payload with no provenance renders "unlabelled source".
 *
 * Wave 3 (report G W-10 to W-12):
 *  - colour comes from tokens only, through classes (globals.css "charts"):
 *    the provenance stroke grammar (dashed backtest, dotted walk-forward,
 *    dash-dot out-of-sample and holdout, solid executed), LIVE in its own
 *    magenta and never in loss red, breaches in the critical token, the
 *    correlation heatmap on a blue/orange diverging scale. No hex literal is
 *    allowed in a component (source-rules.spec.ts);
 *  - the width follows the container, so text is drawn at its real size;
 *  - a line chart's x axis is TIME (UTC ticks) when every point has a
 *    timestamp, and a missing point BREAKS the line with a hatched gap: a gap
 *    is never drawn as data;
 *  - every chart has a keyboard-reachable "View data" table, and a line
 *    chart's points can be read with the arrow keys (a live readout), so no
 *    value is only in a hover title.
 */

const HEIGHT = 240;
const PAD = { l: 60, r: 14, t: 12, b: 30 };

function inkOf(label: ProvenanceLabel): Provenance | "neutral" {
  return label.kind === "single" ? label.provenance : "neutral";
}

function niceTicks(min: number, max: number, count = 4): number[] {
  if (!Number.isFinite(min) || !Number.isFinite(max) || min === max) {
    return [min];
  }
  const span = max - min;
  const raw = span / count;
  const magnitude = 10 ** Math.floor(Math.log10(raw));
  const step = [1, 2, 2.5, 5, 10].map((m) => m * magnitude).find((s) => s >= raw) ?? magnitude * 10;
  const start = Math.ceil(min / step) * step;
  const out: number[] = [];
  for (let v = start; v <= max + step * 0.001; v += step) out.push(Number(v.toFixed(10)));
  return out;
}

const DAY = 86_400_000;
const utcDate = new Intl.DateTimeFormat("en-GB", { day: "2-digit", month: "short", timeZone: "UTC" });
const utcTime = new Intl.DateTimeFormat("en-GB", {
  hour: "2-digit",
  minute: "2-digit",
  hour12: false,
  timeZone: "UTC",
});

function timeTicks(from: number, to: number, count: number): { ms: number; label: string }[] {
  if (!(to > from)) return [{ ms: from, label: utcDate.format(from) }];
  const span = to - from;
  const out: { ms: number; label: string }[] = [];
  for (let i = 0; i <= count; i += 1) {
    const ms = from + (span * i) / count;
    out.push({ ms, label: span > 2 * DAY ? utcDate.format(ms) : utcTime.format(ms) });
  }
  return out;
}

function ChartFrame({
  title,
  provenance,
  unit,
  note,
  children,
  data,
}: {
  title: string;
  provenance: ProvenanceLabel;
  unit: string;
  note?: string;
  children: ReactNode;
  /** The "View data" table: every value the chart draws, as text. */
  data?: ReactNode;
}) {
  return (
    <figure className="chart" data-testid="chart">
      <figcaption className="row mb-1.5">
        <strong>{title}</strong>
        <ProvenanceLabelChip label={provenance} />
        {unit ? <span className="muted">({unit})</span> : null}
      </figcaption>
      {children}
      {note ? <div className="chart__legend">{note}</div> : null}
      {data ? <ChartData>{data}</ChartData> : null}
    </figure>
  );
}

/**
 * "View data": a native disclosure (keyboard and screen-reader operable by
 * the browser). The table is only built once it is opened, so a 500-point
 * series adds nothing to the page until someone asks for it.
 */
function ChartData({ children }: { children: ReactNode }) {
  const [open, setOpen] = useState(false);
  return (
    <details
      className="chart__data"
      data-testid="chart-data"
      onToggle={(event) => setOpen(event.currentTarget.open)}
    >
      <summary>View data</summary>
      {open ? (
        // Focusable, because it scrolls: a keyboard user must be able to reach it.
        <div className="table-wrap chart__data-table" tabIndex={0} role="region" aria-label="Chart data">
          {children}
        </div>
      ) : null}
    </details>
  );
}

function EmptyChart({ message }: { message: string }) {
  return (
    <div className="state state--empty" data-testid="chart-empty">
      <div className="state__body">{message}</div>
    </div>
  );
}

interface Point {
  i: number;
  t: string;
  ms: number;
  v: number | null;
}

/** Equity curves, drawdowns, any ordered value series. */
export function LineChart({ series }: { series: Series }) {
  const [box, width] = useWidth();
  const [active, setActive] = useState<number | null>(null);
  const hatch = `hatch-${useId().replace(/:/g, "")}`;
  const provenance = singleProvenance(series.provenance);

  const points: Point[] = series.points.map((p, i) => ({
    i,
    t: p.t,
    ms: parseUtc(p.t).getTime(),
    v: p.v,
  }));
  const present = points.filter((p): p is Point & { v: number } => p.v !== null);
  const missing = points.length - present.length;

  const table = (
    <table>
      <caption className="sr-only">{series.name}: every point</caption>
      <thead>
        <tr>
          <th scope="col">Time (UTC)</th>
          <th scope="col" className="num">
            Value{series.unit ? ` (${series.unit})` : ""}
          </th>
          <th scope="col">Provenance</th>
        </tr>
      </thead>
      <tbody>
        {points.map((p) => (
          <tr key={`${p.t}:${p.i}`}>
            <td>{formatTimestamp(p.t)}</td>
            <td className="num">
              {p.v === null ? <span className="figure__missing">no data</span> : formatNumber(p.v, series.unit)}
            </td>
            <td>{PROVENANCE_LABEL[series.provenance].toLowerCase()}</td>
          </tr>
        ))}
      </tbody>
    </table>
  );

  if (present.length < 2) {
    return (
      <ChartFrame title={series.name} provenance={provenance} unit={series.unit} data={table}>
        <EmptyChart
          message={`Not enough points to draw a line (${present.length}). This is a missing series, not a flat one.`}
        />
      </ChartFrame>
    );
  }

  const values = present.map((p) => p.v);
  const min = Math.min(...values);
  const max = Math.max(...values);
  const span = max - min || Math.abs(max) || 1;
  const innerW = width - PAD.l - PAD.r;
  const innerH = HEIGHT - PAD.t - PAD.b;
  const timed =
    points.every((p) => Number.isFinite(p.ms)) &&
    points.every((p, i) => i === 0 || p.ms >= (points[i - 1] as Point).ms);
  const first = points[0] as Point;
  const last = points[points.length - 1] as Point;
  const tSpan = last.ms - first.ms;
  const x = (p: Point) =>
    timed && tSpan > 0
      ? PAD.l + ((p.ms - first.ms) / tSpan) * innerW
      : PAD.l + (p.i / Math.max(1, points.length - 1)) * innerW;
  const y = (v: number) => PAD.t + innerH - ((v - min) / span) * innerH;

  // Segments between missing points; each gap is drawn as a hatched band.
  const segments: (Point & { v: number })[][] = [];
  const gaps: { from: number; to: number }[] = [];
  let run: (Point & { v: number })[] = [];
  let gapStart: number | null = null;
  points.forEach((p, idx) => {
    if (p.v === null) {
      if (run.length > 0) segments.push(run);
      run = [];
      if (gapStart === null) {
        const prev = points[idx - 1];
        gapStart = prev ? x(prev) : PAD.l;
      }
      return;
    }
    if (gapStart !== null) {
      gaps.push({ from: gapStart, to: x(p) });
      gapStart = null;
    }
    run.push(p as Point & { v: number });
  });
  if (run.length > 0) segments.push(run);
  if (gapStart !== null) gaps.push({ from: gapStart, to: PAD.l + innerW });

  const base = PAD.t + innerH;
  const line = (seg: (Point & { v: number })[]) =>
    seg.map((p, i) => `${i === 0 ? "M" : "L"}${x(p).toFixed(1)},${y(p.v).toFixed(1)}`).join(" ");
  const area = (seg: (Point & { v: number })[]) => {
    const head = seg[0] as Point;
    const tail = seg[seg.length - 1] as Point;
    return `${line(seg)} L${x(tail).toFixed(1)},${base.toFixed(1)} L${x(head).toFixed(1)},${base.toFixed(1)} Z`;
  };
  const yTicks = niceTicks(min, max);
  const xTicks = timed ? timeTicks(first.ms, last.ms, Math.max(2, Math.min(6, Math.floor(innerW / 120)))) : [];
  const ink = series.provenance;
  const activePoint = active === null ? null : (present[active] ?? null);

  const onKeyDown = (event: KeyboardEvent<HTMLDivElement>) => {
    const lastIndex = present.length - 1;
    const current = active ?? -1;
    let next: number | null = null;
    if (event.key === "ArrowRight") next = Math.min(lastIndex, current + 1);
    else if (event.key === "ArrowLeft") next = Math.max(0, current === -1 ? lastIndex : current - 1);
    else if (event.key === "Home") next = 0;
    else if (event.key === "End") next = lastIndex;
    else if (event.key === "Escape") {
      setActive(null);
      return;
    }
    if (next !== null) {
      event.preventDefault();
      setActive(next);
    }
  };

  return (
    <ChartFrame
      title={series.name}
      provenance={provenance}
      unit={series.unit}
      note={`${present.length} points${missing > 0 ? `, ${missing} missing (hatched, not drawn)` : ""} · ${PROVENANCE_LABEL[series.provenance]}${timed ? " · time axis UTC" : " · points evenly spaced (no usable timestamps)"}`}
      data={table}
    >
      <div
        ref={box}
        className="chart__plot"
        tabIndex={0}
        role="group"
        aria-roledescription="line chart"
        aria-label={`${series.name}. ${present.length} points. Use the left and right arrow keys to read each value, or open View data.`}
        onKeyDown={onKeyDown}
        onBlur={() => setActive(null)}
        data-testid="chart-plot"
      >
        <svg
          viewBox={`0 0 ${width} ${HEIGHT}`}
          width={width}
          height={HEIGHT}
          aria-hidden="true"
          onMouseLeave={() => setActive(null)}
          onMouseMove={(event) => {
            const rect = event.currentTarget.getBoundingClientRect();
            const px = ((event.clientX - rect.left) / rect.width) * width;
            let best = 0;
            let bestD = Infinity;
            present.forEach((p, i) => {
              const d = Math.abs(x(p) - px);
              if (d < bestD) {
                bestD = d;
                best = i;
              }
            });
            setActive(best);
          }}
        >
          <defs>
            <pattern id={hatch} width="6" height="6" patternUnits="userSpaceOnUse" patternTransform="rotate(45)">
              <line x1="0" y1="0" x2="0" y2="6" className="chart__hatch-line" />
            </pattern>
          </defs>
          {yTicks.map((t) => (
            <g key={t}>
              <line x1={PAD.l} x2={width - PAD.r} y1={y(t)} y2={y(t)} className="chart__grid" />
              <text x={PAD.l - 6} y={y(t) + 3} textAnchor="end" className="chart__tick">
                {formatNumber(t, series.unit)}
              </text>
            </g>
          ))}
          {xTicks.map((t) => {
            const tx = PAD.l + ((t.ms - first.ms) / (tSpan || 1)) * innerW;
            return (
              <g key={t.ms}>
                <line x1={tx} x2={tx} y1={base} y2={base + 4} className="chart__axis" />
                <text x={tx} y={base + 16} textAnchor="middle" className="chart__tick">
                  {t.label}
                </text>
              </g>
            );
          })}
          <line x1={PAD.l} x2={width - PAD.r} y1={base} y2={base} className="chart__axis" />
          {gaps.map((g) => (
            <rect
              key={`${g.from}-${g.to}`}
              x={g.from}
              y={PAD.t}
              width={Math.max(2, g.to - g.from)}
              height={innerH}
              fill={`url(#${hatch})`}
              className="chart__gap"
              data-testid="chart-gap"
            />
          ))}
          {segments.map((seg) => (
            <g key={`${seg[0]?.i}`} className="chart-ink" data-ink={ink}>
              {seg.length > 1 ? <path d={area(seg)} className="chart__area" /> : null}
              {seg.length > 1 ? (
                <path d={line(seg)} className="chart__line" data-testid="chart-line" />
              ) : (
                <circle cx={x(seg[0] as Point)} cy={y((seg[0] as Point & { v: number }).v)} r={2} className="chart__dot" />
              )}
            </g>
          ))}
          {activePoint ? (
            <g className="chart-ink" data-ink={ink}>
              <line x1={x(activePoint)} x2={x(activePoint)} y1={PAD.t} y2={base} className="chart__cursor" />
              <circle cx={x(activePoint)} cy={y(activePoint.v)} r={4} className="chart__marker" />
            </g>
          ) : null}
        </svg>
      </div>
      <p className="chart__readout" aria-live="polite" data-testid="chart-readout">
        {activePoint
          ? `${formatTimestamp(activePoint.t)}: ${formatNumber(activePoint.v, series.unit)}`
          : " "}
      </p>
    </ChartFrame>
  );
}

/** Horizontal bars for exposure, utilisation, per-strategy comparisons. */
export function BarChart({
  title,
  provenance,
  unit,
  bars,
}: {
  title: string;
  provenance: ProvenanceLabel;
  unit: string;
  bars: { label: string; value: number | null; limit?: number | null }[];
}) {
  const [box, width] = useWidth();
  // Narrowed with a predicate rather than coalesced: a bar with no value is
  // dropped from the chart and counted in the caption, never drawn as a zero.
  const usable = bars.filter(
    (b): b is { label: string; value: number; limit?: number | null } => b.value !== null,
  );
  const dropped = bars.length - usable.length;
  const table = (
    <table>
      <caption className="sr-only">{title}: every bar</caption>
      <thead>
        <tr>
          <th scope="col">Bar</th>
          <th scope="col" className="num">
            Value{unit ? ` (${unit})` : ""}
          </th>
          <th scope="col" className="num">
            Limit
          </th>
          <th scope="col">Breached</th>
        </tr>
      </thead>
      <tbody>
        {bars.map((bar) => (
          <tr key={bar.label}>
            <td>{bar.label}</td>
            <td className="num">
              {bar.value === null ? <span className="figure__missing">no data</span> : formatNumber(bar.value, unit)}
            </td>
            <td className="num">
              {bar.limit === null || bar.limit === undefined ? "—" : formatNumber(bar.limit, unit)}
            </td>
            <td>
              {bar.value !== null && bar.limit != null && bar.value > bar.limit ? "BREACHED" : "no"}
            </td>
          </tr>
        ))}
      </tbody>
    </table>
  );
  if (usable.length === 0) {
    return (
      <ChartFrame title={title} provenance={provenance} unit={unit} data={table}>
        <EmptyChart message="No values are available for this chart." />
      </ChartFrame>
    );
  }
  const max = Math.max(
    ...usable.map((b) => Math.max(b.value, b.limit == null ? b.value : b.limit)),
    1,
  );
  const rowH = 22;
  const labelW = Math.min(170, Math.max(90, width * 0.22));
  const valueW = 70;
  const plotW = Math.max(40, width - labelW - valueW);
  const height = usable.length * rowH + 8;
  const ink = inkOf(provenance);

  return (
    <ChartFrame
      title={title}
      provenance={provenance}
      unit={unit}
      note={
        dropped > 0
          ? `${dropped} bucket(s) omitted because the platform supplied no value for them.`
          : undefined
      }
      data={table}
    >
      <div ref={box} className="chart__plot-static">
        <svg viewBox={`0 0 ${width} ${height}`} width={width} height={height} role="img" aria-label={`${title}. The values are in View data.`}>
          {usable.map((bar, i) => {
            const w = (bar.value / max) * plotW;
            const y = i * rowH + 4;
            const breached = bar.limit != null && bar.value > bar.limit;
            return (
              <g key={bar.label}>
                <text x={0} y={y + 13} className="chart__label">
                  {bar.label.length > 22 ? `${bar.label.slice(0, 21)}…` : bar.label}
                </text>
                <rect
                  x={labelW}
                  y={y + 4}
                  width={Math.max(1, w)}
                  height={rowH - 10}
                  className={breached ? "chart__bar chart__bar--breach" : "chart__bar chart-ink"}
                  data-ink={breached ? undefined : ink}
                  data-breached={breached}
                />
                {bar.limit != null ? (
                  <line
                    x1={labelW + (bar.limit / max) * plotW}
                    x2={labelW + (bar.limit / max) * plotW}
                    y1={y + 1}
                    y2={y + rowH - 3}
                    className="chart__limit"
                  />
                ) : null}
                <text x={labelW + Math.max(1, w) + 6} y={y + 13} className="chart__tick">
                  {formatNumber(bar.value, unit)}
                  {breached ? " ▲ limit" : ""}
                </text>
              </g>
            );
          })}
        </svg>
      </div>
    </ChartFrame>
  );
}

/** The diverging bin (−5 … +5) for a correlation in [−1, 1]. */
export function heatBin(value: number): number {
  const clamped = Math.max(-1, Math.min(1, value));
  return Math.round(clamped * 5);
}

/** Correlation matrices and any symmetric grid. */
export function Heatmap({
  title,
  provenance,
  labels,
  matrix,
}: {
  title: string;
  provenance: ProvenanceLabel;
  labels: string[];
  matrix: number[][];
}) {
  if (labels.length === 0 || matrix.length === 0) {
    return (
      <ChartFrame title={title} provenance={provenance} unit="">
        <EmptyChart message="No matrix is available. An empty grid is not an uncorrelated book." />
      </ChartFrame>
    );
  }
  const cell = 26;
  const pad = 70;
  const size = labels.length * cell + pad;
  const table = (
    <table>
      <caption className="sr-only">{title}: every cell</caption>
      <thead>
        <tr>
          <th scope="col">
            <span className="sr-only">Row</span>
          </th>
          {labels.map((label) => (
            <th key={label} scope="col" className="num">
              {label}
            </th>
          ))}
        </tr>
      </thead>
      <tbody>
        {labels.map((label, r) => (
          <tr key={label}>
            <th scope="row">{label}</th>
            {labels.map((other, c) => {
              const v = matrix[r]?.[c];
              return (
                <td key={other} className="num">
                  {v === undefined || v === null ? "—" : formatNumber(v, "ratio")}
                </td>
              );
            })}
          </tr>
        ))}
      </tbody>
    </table>
  );

  return (
    <ChartFrame
      title={title}
      provenance={provenance}
      unit=""
      note="Orange is positively correlated (risk concentrates); blue is negatively correlated; the paler the cell, the nearer zero. Values are in View data."
      data={table}
    >
      <div
        className="chart__plot-static chart__plot-scroll"
        tabIndex={0}
        role="region"
        aria-label={`${title}: heatmap, scrolls sideways`}
      >
        <svg viewBox={`0 0 ${size} ${size}`} width={size} height={size} role="img" aria-label={`${title}. The values are in View data.`}>
          {labels.map((label, r) => (
            <g key={label}>
              <text x={pad - 6} y={pad + r * cell + cell / 2 + 3} textAnchor="end" className="chart__label chart__label--small">
                {label}
              </text>
              <text
                x={pad + r * cell + cell / 2}
                y={pad - 8}
                className="chart__label chart__label--small"
                transform={`rotate(-55 ${pad + r * cell + cell / 2} ${pad - 8})`}
              >
                {label}
              </text>
              {(matrix[r] ?? []).map((v, c) => (
                <rect
                  key={`${r}-${c}`}
                  x={pad + c * cell}
                  y={pad + r * cell}
                  width={cell - 1}
                  height={cell - 1}
                  className="heat"
                  data-bin={heatBin(v)}
                  data-testid="heat-cell"
                />
              ))}
            </g>
          ))}
        </svg>
      </div>
      <div className="heat-legend" aria-hidden="true">
        <span>−1</span>
        {[-5, -4, -3, -2, -1, 0, 1, 2, 3, 4, 5].map((bin) => (
          <span key={bin} className="heat-legend__swatch heat-swatch" data-bin={bin} />
        ))}
        <span>+1</span>
      </div>
    </ChartFrame>
  );
}

/**
 * Which side of zero a histogram bin lies on. P&L colours mean P&L, both
 * ways (inventory F-11): a bin wholly above zero is profit, wholly below is
 * loss, and one that spans zero is neither.
 */
function binSign(from: number, step: number): "up" | "down" | "zero" {
  if (from + step <= 0) return "down";
  if (from >= 0) return "up";
  return "zero";
}

/** R-multiple and P&L distributions. */
export function Distribution({
  title,
  provenance,
  unit,
  values,
  bins = 24,
}: {
  title: string;
  provenance: ProvenanceLabel;
  unit: string;
  values: number[];
  bins?: number;
}) {
  const [box, width] = useWidth();
  if (values.length < 2) {
    return (
      <ChartFrame title={title} provenance={provenance} unit={unit}>
        <EmptyChart message={`Only ${values.length} observation(s); a distribution needs more.`} />
      </ChartFrame>
    );
  }
  const min = Math.min(...values);
  const max = Math.max(...values);
  const step = (max - min) / bins || 1;
  // Histogram counters. These are counts of observations we HAVE, never a
  // stand-in for a value the platform could not supply, so incrementing from
  // zero is correct here in a way that `figure.value ?? 0` never is.
  const counts = new Array<number>(bins).fill(0);
  for (const v of values) {
    const index = Math.min(bins - 1, Math.max(0, Math.floor((v - min) / step)));
    counts[index] = (counts[index] as number) + 1;
  }
  const peak = Math.max(...counts, 1);
  const innerW = width - PAD.l - PAD.r;
  const innerH = HEIGHT - PAD.t - PAD.b;
  const barW = innerW / bins;
  const zeroX = PAD.l + ((0 - min) / (max - min || 1)) * innerW;
  const ink = inkOf(provenance);
  const table = (
    <table>
      <caption className="sr-only">{title}: every bin</caption>
      <thead>
        <tr>
          <th scope="col">Bin{unit ? ` (${unit})` : ""}</th>
          <th scope="col" className="num">
            Observations
          </th>
        </tr>
      </thead>
      <tbody>
        {counts.map((count, i) => {
          const from = min + i * step;
          const sign = binSign(from, step);
          return (
            <tr key={i} data-sign={sign}>
              <td>
                {sign === "up" ? "▲ " : sign === "down" ? "▼ " : ""}
                {formatNumber(from, unit)} to {formatNumber(from + step, unit)}
              </td>
              <td className="num">{count}</td>
            </tr>
          );
        })}
      </tbody>
    </table>
  );

  return (
    <ChartFrame
      title={title}
      provenance={provenance}
      unit={unit}
      note={`${values.length} observations · ${formatNumber(min, unit)} to ${formatNumber(max, unit)} · ▲ bins wholly above zero in the profit colour, ▼ bins wholly below zero in the loss colour, a bin spanning zero in the source's ink`}
      data={table}
    >
      <div ref={box} className="chart__plot-static">
        <svg viewBox={`0 0 ${width} ${HEIGHT}`} width={width} height={HEIGHT} role="img" aria-label={`${title}. The bins are in View data.`}>
          {counts.map((count, i) => {
            const h = (count / peak) * innerH;
            const binStart = min + i * step;
            const sign = binSign(binStart, step);
            return (
              <rect
                key={i}
                x={PAD.l + i * barW}
                y={PAD.t + innerH - h}
                width={Math.max(1, barW - 1)}
                height={h}
                className={
                  sign === "down"
                    ? "chart__bar chart__bar--down"
                    : sign === "up"
                      ? "chart__bar chart__bar--up"
                      : "chart__bar chart-ink"
                }
                data-sign={sign}
                data-ink={sign === "zero" ? ink : undefined}
                data-testid="distribution-bin"
              />
            );
          })}
          {min < 0 && max > 0 ? (
            <line x1={zeroX} x2={zeroX} y1={PAD.t} y2={PAD.t + innerH} className="chart__zero" />
          ) : null}
          <line x1={PAD.l} x2={width - PAD.r} y1={PAD.t + innerH} y2={PAD.t + innerH} className="chart__axis" />
          {niceTicks(min, max, Math.max(2, Math.min(6, Math.floor(innerW / 110)))).map((t) => {
            const tx = PAD.l + ((t - min) / (max - min || 1)) * innerW;
            return (
              <text key={t} x={tx} y={PAD.t + innerH + 16} textAnchor="middle" className="chart__tick">
                {formatNumber(t, unit)}
              </text>
            );
          })}
        </svg>
      </div>
    </ChartFrame>
  );
}
