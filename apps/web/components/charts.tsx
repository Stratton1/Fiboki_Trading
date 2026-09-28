import type { Provenance, Series } from "@/lib/types";
import { PROVENANCE_LABEL, formatNumber } from "@/lib/format";
import { singleProvenance, type ProvenanceLabel } from "@/lib/provenance";
import { ProvenanceLabelChip } from "./ProvenanceChip";

/**
 * Charts, hand-rolled as inline SVG.
 *
 * V1 shipped the full Plotly distribution — about 4.5 MB including mapbox-gl —
 * to draw line charts. Nothing here is imported at runtime: these are ~250
 * lines of SVG generation with no chart dependency at all, so the line, bar,
 * heatmap and distribution views cost zero kilobytes of vendor JavaScript.
 *
 * Every chart takes a provenance and prints the chip in its own legend, because
 * a chart is a pile of numbers and the same labelling rule applies. Charts over
 * many rows take a `ProvenanceLabel` derived from those rows
 * (`lib/provenance.ts`), so a mixed set renders MIXED rather than the first
 * row's label, and a payload with no provenance renders "unlabelled source".
 */

const PALETTE: Record<Provenance, string> = {
  backtest: "#8b93a7",
  walkforward: "#7f8cff",
  out_of_sample: "#5b9dff",
  holdout: "#38bdf8",
  paper: "#3fbf7f",
  shadow: "#a78bfa",
  broker_demo: "#f5a524",
  broker_live: "#ff4d4d",
};

/** Mixed, source-only and unlabelled data get a neutral ink, not a provenance's. */
const NEUTRAL = "#9aa5bd";

function inkFor(label: ProvenanceLabel): string {
  return label.kind === "single" ? PALETTE[label.provenance] : NEUTRAL;
}

interface Box {
  width: number;
  height: number;
  padL: number;
  padR: number;
  padT: number;
  padB: number;
}

const BOX: Box = { width: 720, height: 240, padL: 56, padR: 12, padT: 12, padB: 26 };

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

function ChartFrame({
  title,
  provenance,
  unit,
  note,
  children,
}: {
  title: string;
  provenance: ProvenanceLabel;
  unit: string;
  note?: string;
  children: React.ReactNode;
}) {
  return (
    <figure className="chart" style={{ margin: 0 }} data-testid="chart">
      <figcaption className="row" style={{ marginBottom: 6 }}>
        <strong>{title}</strong>
        <ProvenanceLabelChip label={provenance} />
        {unit ? <span className="muted">({unit})</span> : null}
      </figcaption>
      {children}
      {note ? <div className="chart__legend">{note}</div> : null}
    </figure>
  );
}

function EmptyChart({ message }: { message: string }) {
  return (
    <div className="state state--empty" data-testid="chart-empty">
      <div className="state__body">{message}</div>
    </div>
  );
}

/** Equity curves, drawdowns, any ordered value series. */
export function LineChart({ series }: { series: Series }) {
  const points = series.points.filter(
    (p): p is { t: string; v: number } => p.v !== null,
  );
  if (points.length < 2) {
    return (
      <ChartFrame
        title={series.name}
        provenance={singleProvenance(series.provenance)}
        unit={series.unit}
      >
        <EmptyChart
          message={`Not enough points to draw a line (${points.length}). This is a missing series, not a flat one.`}
        />
      </ChartFrame>
    );
  }

  const values = points.map((p) => p.v);
  const min = Math.min(...values);
  const max = Math.max(...values);
  const span = max - min || Math.abs(max) || 1;
  const { width, height, padL, padR, padT, padB } = BOX;
  const innerW = width - padL - padR;
  const innerH = height - padT - padB;

  const x = (i: number) => padL + (i / (points.length - 1)) * innerW;
  const y = (v: number) => padT + innerH - ((v - min) / span) * innerH;

  const path = points.map((p, i) => `${i === 0 ? "M" : "L"}${x(i).toFixed(1)},${y(p.v).toFixed(1)}`).join(" ");
  const area = `${path} L${x(points.length - 1).toFixed(1)},${(padT + innerH).toFixed(1)} L${padL},${(padT + innerH).toFixed(1)} Z`;
  const colour = PALETTE[series.provenance];
  const ticks = niceTicks(min, max);

  return (
    <ChartFrame
      title={series.name}
      provenance={singleProvenance(series.provenance)}
      unit={series.unit}
      note={`${points.length} points · ${PROVENANCE_LABEL[series.provenance]}`}
    >
      <svg viewBox={`0 0 ${width} ${height}`} role="img" aria-label={series.name}>
        {ticks.map((t) => (
          <g key={t}>
            <line
              x1={padL}
              x2={width - padR}
              y1={y(t)}
              y2={y(t)}
              stroke="currentColor"
              strokeOpacity={0.12}
            />
            <text x={padL - 6} y={y(t) + 3} textAnchor="end" fontSize="10" fill="currentColor" fillOpacity={0.55}>
              {formatNumber(t, series.unit)}
            </text>
          </g>
        ))}
        <path d={area} fill={colour} fillOpacity={0.12} />
        <path d={path} fill="none" stroke={colour} strokeWidth={1.6} />
      </svg>
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
  // Narrowed with a predicate rather than coalesced: a bar with no value is
  // dropped from the chart and counted in the caption, never drawn as a zero.
  const usable = bars.filter(
    (b): b is { label: string; value: number; limit?: number | null } =>
      b.value !== null,
  );
  const dropped = bars.length - usable.length;
  if (usable.length === 0) {
    return (
      <ChartFrame title={title} provenance={provenance} unit={unit}>
        <EmptyChart message="No values are available for this chart." />
      </ChartFrame>
    );
  }
  const max = Math.max(
    ...usable.map((b) => Math.max(b.value, b.limit == null ? b.value : b.limit)),
    1,
  );
  const rowH = 22;
  const width = 720;
  const labelW = 150;
  const height = usable.length * rowH + 8;
  const colour = inkFor(provenance);

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
    >
      <svg viewBox={`0 0 ${width} ${height}`} role="img" aria-label={title}>
        {usable.map((bar, i) => {
          const value = bar.value;
          const w = (value / max) * (width - labelW - 60);
          const y = i * rowH + 4;
          const breached = bar.limit != null && value > bar.limit;
          return (
            <g key={bar.label}>
              <text x={0} y={y + 13} fontSize="11" fill="currentColor" fillOpacity={0.75}>
                {bar.label.length > 22 ? `${bar.label.slice(0, 21)}…` : bar.label}
              </text>
              <rect
                x={labelW}
                y={y + 4}
                width={Math.max(1, w)}
                height={rowH - 10}
                fill={breached ? "#ff4d4d" : colour}
                fillOpacity={0.8}
              />
              {bar.limit != null ? (
                <line
                  x1={labelW + (bar.limit / max) * (width - labelW - 60)}
                  x2={labelW + (bar.limit / max) * (width - labelW - 60)}
                  y1={y + 1}
                  y2={y + rowH - 3}
                  stroke="#ff4d4d"
                  strokeDasharray="2 2"
                />
              ) : null}
              <text
                x={labelW + Math.max(1, w) + 6}
                y={y + 13}
                fontSize="10"
                fill="currentColor"
                fillOpacity={0.6}
              >
                {formatNumber(value, unit)}
              </text>
            </g>
          );
        })}
      </svg>
    </ChartFrame>
  );
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

  const colour = (v: number) => {
    const clamped = Math.max(-1, Math.min(1, v));
    return clamped >= 0
      ? `rgba(255,77,77,${(clamped * 0.85).toFixed(3)})`
      : `rgba(63,191,127,${(Math.abs(clamped) * 0.85).toFixed(3)})`;
  };

  return (
    <ChartFrame
      title={title}
      provenance={provenance}
      unit=""
      note="Red is positively correlated (risk concentrates); green is negatively correlated."
    >
      <svg viewBox={`0 0 ${size} ${size}`} role="img" aria-label={title}>
        {labels.map((label, r) => (
          <g key={label}>
            <text x={pad - 6} y={pad + r * cell + cell / 2 + 3} textAnchor="end" fontSize="9" fill="currentColor" fillOpacity={0.7}>
              {label}
            </text>
            <text
              x={pad + r * cell + cell / 2}
              y={pad - 8}
              fontSize="9"
              fill="currentColor"
              fillOpacity={0.7}
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
                fill={colour(v)}
              >
                <title>{`${label} / ${labels[c] ?? "?"}: ${v.toFixed(2)}`}</title>
              </rect>
            ))}
          </g>
        ))}
      </svg>
    </ChartFrame>
  );
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
  const { width, height, padL, padR, padT, padB } = BOX;
  const innerW = width - padL - padR;
  const innerH = height - padT - padB;
  const barW = innerW / bins;
  const colour = inkFor(provenance);
  const zeroX = padL + ((0 - min) / (max - min || 1)) * innerW;

  return (
    <ChartFrame
      title={title}
      provenance={provenance}
      unit={unit}
      note={`${values.length} observations · ${formatNumber(min, unit)} to ${formatNumber(max, unit)}`}
    >
      <svg viewBox={`0 0 ${width} ${height}`} role="img" aria-label={title}>
        {counts.map((count, i) => {
          const h = (count / peak) * innerH;
          const binStart = min + i * step;
          return (
            <rect
              key={i}
              x={padL + i * barW}
              y={padT + innerH - h}
              width={Math.max(1, barW - 1)}
              height={h}
              fill={binStart < 0 ? "#ff6b6b" : colour}
              fillOpacity={0.75}
            >
              <title>{`${formatNumber(binStart, unit)} … ${formatNumber(binStart + step, unit)}: ${count}`}</title>
            </rect>
          );
        })}
        {min < 0 && max > 0 ? (
          <line x1={zeroX} x2={zeroX} y1={padT} y2={padT + innerH} stroke="currentColor" strokeOpacity={0.4} />
        ) : null}
      </svg>
    </ChartFrame>
  );
}
