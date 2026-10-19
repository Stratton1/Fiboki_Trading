import type { Series } from "@/lib/types";

/**
 * A sparkline of the API's bucket closes (owned SVG, no chart engine). It
 * scales the points the API sent to the box and nothing else: a bucket with
 * no recorded quote is a gap in the line, never an interpolated value.
 */
export function Sparkline({ series, width = 96, height = 18 }: { series: Series; width?: number; height?: number }) {
  const values = series.points.map((p) => p.v);
  const known = values.filter((v): v is number => v !== null);
  if (known.length < 2) {
    return (
      <span className="spark spark--empty muted" data-testid="sparkline" data-points={known.length}>
        {known.length === 0 ? "no quotes" : "one quote"}
      </span>
    );
  }
  const lo = Math.min(...known);
  const hi = Math.max(...known);
  const span = hi - lo || 1;
  const step = values.length > 1 ? (width - 2) / (values.length - 1) : 0;
  const segments: string[] = [];
  let current: string[] = [];
  values.forEach((v, i) => {
    if (v === null) {
      if (current.length > 1) segments.push(current.join(" "));
      current = [];
      return;
    }
    const x = 1 + i * step;
    const y = 1 + (height - 2) * (1 - (v - lo) / span);
    current.push(`${x.toFixed(1)},${y.toFixed(1)}`);
  });
  if (current.length > 1) segments.push(current.join(" "));
  return (
    <svg
      className="spark"
      width={width}
      height={height}
      viewBox={`0 0 ${width} ${height}`}
      role="img"
      aria-label={`${series.name}: ${known.length} of ${values.length} buckets recorded`}
      data-testid="sparkline"
      data-points={known.length}
    >
      {segments.map((points, i) => (
        <polyline key={i} points={points} fill="none" className="spark__line" />
      ))}
    </svg>
  );
}
