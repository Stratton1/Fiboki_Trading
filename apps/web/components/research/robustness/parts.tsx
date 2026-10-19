"use client";

import type { ReactNode } from "react";
import { FigureValue } from "@/components/FigureValue";
import { formatTimestamp } from "@/lib/format";
import type { BadgeTone, ToneLabel } from "@/lib/tones";
import type { DistributionView, HoldoutBoundaryView, RunMetricsView } from "@/lib/robustness";
import type { Figure } from "@/lib/types";

/**
 * Shared pieces of the robustness panels. Rendering only: every number, band,
 * verdict and boundary is the API's (research/robustness). Geometry (a bar's
 * width) is layout, computed from the numbers the API sent; no statistic is.
 */

function lookup(table: Record<string, [BadgeTone, string]>, value: string): ToneLabel {
  const hit = Object.prototype.hasOwnProperty.call(table, value) ? table[value] : undefined;
  if (hit) return { tone: hit[0], label: hit[1], known: true };
  return { tone: "unknown", label: value ? `UNRECOGNISED: ${value.toUpperCase()}` : "NOT SUPPLIED", known: false };
}

/** A re-run job's state (routers/research_robustness.py RerunStatusView.status). */
const RERUN_STATUS: Record<string, [BadgeTone, string]> = {
  done: ["ok", "COMPUTED"],
  pending: ["degraded", "QUEUED"],
  running: ["degraded", "RUNNING"],
  failed: ["down", "FAILED"],
  dead_letter: ["down", "DEAD-LETTERED"],
  not_run: ["unknown", "NOT RUN"],
};
export const rerunTone = (v: string) => lookup(RERUN_STATUS, v);

/** A boolean finding the API computed, as a badge. `good` names which value is reassuring. */
export function findingTone(value: boolean | null, labels: [string, string], good: boolean): ToneLabel {
  if (value === null) return { tone: "unknown", label: "NOT COMPARABLE", known: true };
  return value === good
    ? { tone: "ok", label: value ? labels[0] : labels[1], known: true }
    : { tone: "down", label: value ? labels[0] : labels[1], known: true };
}

export function BoundaryStamp({ b }: { b: HoldoutBoundaryView }) {
  return (
    <p className="rb-boundary" data-testid="holdout-boundary" data-basis={b.basis} title={b.detail}>
      <span className="rb-boundary__k">Holdout fence</span>
      <span className="mono">{formatTimestamp(b.start)}</span>
      <span className={`rb-tag rb-tag--${b.basis === "registry" ? "ok" : "warn"}`}>
        {b.basis === "registry" ? "registry segment" : "provisional 20%"}
      </span>
      <span className="rb-boundary__x">
        excluded <Fig f={b.trades_excluded} /> trades · <Fig f={b.points_excluded} /> points
      </span>
    </p>
  );
}

export function Fig({ f, sign = false }: { f: Figure; sign?: boolean }) {
  return <FigureValue figure={f} showChip={false} colourSign={sign} />;
}

/** A distribution as `p50 [p5 – p95]`, each a labelled figure. */
export function Dist({ d, sign = false }: { d: DistributionView; sign?: boolean }) {
  return (
    <span className="rb-dist" data-testid="dist">
      <Fig f={d.p50} sign={sign} />
      <span className="rb-dist__range">
        [<Fig f={d.p5} sign={sign} /> – <Fig f={d.p95} sign={sign} />]
      </span>
    </span>
  );
}

const METRIC_COLS: [keyof RunMetricsView, string, boolean][] = [
  ["n_trades", "Trades", false],
  ["net_profit", "Net", true],
  ["total_return_pct", "Return", true],
  ["sharpe", "Sharpe", false],
  ["max_drawdown_pct", "Max DD", false],
  ["profit_factor", "PF", false],
  ["win_rate_pct", "Win", false],
  ["total_costs", "Costs", false],
];

export function MetricsStrip({ m, label }: { m: RunMetricsView; label: string }) {
  return (
    <dl className="rb-strip" aria-label={label} data-testid="metrics-strip">
      <dt className="rb-strip__label">{label}</dt>
      <dd className="rb-strip__body">
        {METRIC_COLS.map(([k, name, sign]) => (
          <span key={k} className="rb-strip__item">
            <span className="rb-strip__k">{name}</span>
            <Fig f={m[k]} sign={sign} />
          </span>
        ))}
      </dd>
    </dl>
  );
}

/**
 * A horizontal range glyph: p5 to p95 as a bar, p50 as a tick, the point as a
 * dot, on an axis spanning the values handed in (layout only).
 */
export function RangeGlyph({ lo, mid, hi, point, min, max, label }: {
  lo: number | null; mid: number | null; hi: number | null; point: number | null;
  min: number; max: number; label: string;
}) {
  const span = max - min || 1;
  const x = (v: number) => 2 + ((v - min) / span) * 116;
  return (
    <svg className="rb-range" viewBox="0 0 120 12" role="img" aria-label={label} preserveAspectRatio="none">
      <line x1="2" x2="118" y1="6" y2="6" className="rb-range__axis" />
      {min < 0 && max > 0 ? <line x1={x(0)} x2={x(0)} y1="1" y2="11" className="rb-range__zero" /> : null}
      {lo !== null && hi !== null ? <rect x={x(lo)} width={Math.max(1, x(hi) - x(lo))} y="3" height="6" className="rb-range__band" /> : null}
      {mid !== null ? <line x1={x(mid)} x2={x(mid)} y1="1" y2="11" className="rb-range__mid" /> : null}
      {point !== null ? <circle cx={x(point)} cy="6" r="2" className="rb-range__point" /> : null}
    </svg>
  );
}

export function Notes({ items }: { items: ReactNode[] }) {
  if (!items.length) return null;
  return (
    <ul className="rs-notes">
      {items.map((n, i) => (
        <li key={i}>{n}</li>
      ))}
    </ul>
  );
}
