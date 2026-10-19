import type {
  IChartApiBase,
  IPrimitivePaneRenderer,
  IPrimitivePaneView,
  ISeriesApi,
  ISeriesPrimitive,
  Logical,
  PrimitivePaneViewZOrder,
  SeriesAttachedParameter,
  SeriesType,
  Time,
} from "lightweight-charts";
import { markerFill, type ChartTheme } from "@/lib/chart-theme";
import type { ChartModel, PlacedFill, PlacedSignal } from "@/lib/chart-model";

/**
 * The owned overlay layer (plan §6; Joe, 2026-09-29: "best available charts,
 * own overlay layer"). A Lightweight Charts series primitive that draws what
 * the library's built-in markers cannot say:
 *
 *  - regime bands: a full-height tint per run plus a 3 px ribbon with the
 *    label at the foot of the price pane; `unknown` is hatched, never tinted
 *    as range (report G C6);
 *  - signals: ▲ long below the bar, ▼ short above it, ◆ when the API could
 *    not tell the side; a BLOCKED signal is struck through;
 *  - fills: ● entry, ■ exit, at the fill price the platform recorded;
 *  - entry-to-exit connectors, coloured by the sign of the platform's net
 *    P&L (the only green/red on the chart), dashed when simulated.
 *
 * Every marker is FILLED for executed provenance (paper, shadow, demo, live)
 * and HOLLOW for simulated (backtest, walk-forward, OOS, holdout), in the
 * provenance ink (lib/chart-theme.ts), exactly as the provenance chips are.
 * The library's `createSeriesMarkers` draws only filled shapes, which would
 * erase that distinction; that is why this layer exists.
 *
 * It draws; it computes nothing. Positions come from the chart's own
 * coordinate conversion of the API's times and prices.
 */

export interface OverlayLayers {
  regimes: boolean;
  signals: boolean;
  fills: boolean;
  /** Scheduled calendar events on the time axis (Wave 2B). */
  events?: boolean;
}

export interface OverlayDrawState {
  model: ChartModel;
  theme: ChartTheme;
  layers: OverlayLayers;
  /** The event `?event=` focuses: drawn in the accent, thicker. */
  focusEvent?: string | null;
}

const RIBBON_PX = 3;

type Ctx = CanvasRenderingContext2D;

function markerSize(spacing: number): number {
  return Math.max(7, Math.min(12, spacing * 0.9));
}

function paint(ctx: Ctx, ink: string, filled: boolean) {
  if (filled) {
    ctx.fillStyle = ink;
    ctx.fill();
    return;
  }
  ctx.lineWidth = 1.5;
  ctx.strokeStyle = ink;
  ctx.stroke();
}

function triangle(ctx: Ctx, x: number, y: number, size: number, up: boolean) {
  const h = size * 0.9;
  ctx.beginPath();
  if (up) {
    ctx.moveTo(x, y);
    ctx.lineTo(x + size / 2, y + h);
    ctx.lineTo(x - size / 2, y + h);
  } else {
    ctx.moveTo(x, y);
    ctx.lineTo(x + size / 2, y - h);
    ctx.lineTo(x - size / 2, y - h);
  }
  ctx.closePath();
}

function diamond(ctx: Ctx, x: number, y: number, size: number) {
  const r = size / 2;
  ctx.beginPath();
  ctx.moveTo(x, y - r);
  ctx.lineTo(x + r, y);
  ctx.lineTo(x, y + r);
  ctx.lineTo(x - r, y);
  ctx.closePath();
}

class RegimeRenderer implements IPrimitivePaneRenderer {
  constructor(private readonly layer: OverlayPrimitive) {}

  draw(target: Parameters<IPrimitivePaneRenderer["draw"]>[0]) {
    const scope = this.layer.scope();
    if (!scope || !scope.state.layers.regimes) return;
    const { chart, state } = scope;
    const { theme, model } = state;
    const time = chart.timeScale();
    const spacing = time.options().barSpacing;
    target.useMediaCoordinateSpace(({ context: ctx, mediaSize }) => {
      const height = mediaSize.height;
      ctx.save();
      ctx.font = `600 10px ${theme.fontUi}`;
      ctx.textBaseline = "bottom";
      for (const run of model.regimes) {
        const a = time.logicalToCoordinate(run.start as Logical);
        const b = time.logicalToCoordinate(run.end as Logical);
        if (a === null || b === null) continue;
        const x1 = a - spacing / 2;
        const x2 = b + spacing / 2;
        if (x2 < 0 || x1 > mediaSize.width) continue;
        const width = x2 - x1;
        const label = run.item.label;
        ctx.fillStyle = theme.regimeTint[label];
        ctx.fillRect(x1, 0, width, height);
        if (label === "unknown") {
          ctx.save();
          ctx.beginPath();
          ctx.rect(x1, 0, width, height);
          ctx.clip();
          ctx.strokeStyle = theme.regimeRibbon.unknown;
          ctx.globalAlpha = 0.35;
          ctx.lineWidth = 1;
          ctx.beginPath();
          for (let d = -height; d < width; d += 8) {
            ctx.moveTo(x1 + d, height);
            ctx.lineTo(x1 + d + height, 0);
          }
          ctx.stroke();
          ctx.restore();
        }
        ctx.fillStyle = theme.regimeRibbon[label];
        ctx.fillRect(x1, height - RIBBON_PX, width, RIBBON_PX);
        const text = label.toUpperCase();
        const textWidth = ctx.measureText(text).width;
        if (width > textWidth + 8) {
          ctx.fillStyle = theme.textMuted;
          ctx.fillText(text, Math.max(x1, 0) + 4, height - RIBBON_PX - 2);
        }
      }
      ctx.restore();
    });
  }
}

class MarkerRenderer implements IPrimitivePaneRenderer {
  constructor(private readonly layer: OverlayPrimitive) {}

  draw(target: Parameters<IPrimitivePaneRenderer["draw"]>[0]) {
    const scope = this.layer.scope();
    if (!scope) return;
    const { chart, series, state } = scope;
    const { theme, model, layers } = state;
    const time = chart.timeScale();
    const size = markerSize(time.options().barSpacing);
    const x = (index: number) => time.logicalToCoordinate(index as Logical);
    const y = (price: number) => series.priceToCoordinate(price);

    target.useMediaCoordinateSpace(({ context: ctx, mediaSize }) => {
      ctx.save();
      const visible = (px: number | null): px is number =>
        px !== null && px > -size && px < mediaSize.width + size;

      if (layers.fills) {
        // Connectors under the fill markers.
        for (const c of model.connectors) {
          const x1 = x(c.entry.index);
          const x2 = x(c.exit.index);
          const p1 = c.entry.item.price.value;
          const p2 = c.exit.item.price.value;
          if (x1 === null || x2 === null || p1 === null || p2 === null) continue;
          const y1 = y(p1);
          const y2 = y(p2);
          if (y1 === null || y2 === null) continue;
          ctx.beginPath();
          ctx.moveTo(x1, y1);
          ctx.lineTo(x2, y2);
          ctx.lineWidth = 1.25;
          ctx.strokeStyle =
            c.pnlSign === null || c.pnlSign === 0
              ? theme.neutral
              : c.pnlSign > 0
                ? theme.pnlUp
                : theme.pnlDown;
          ctx.setLineDash(markerFill(c.provenance) === "hollow" ? [4, 3] : []);
          ctx.stroke();
        }
        ctx.setLineDash([]);
        for (const fill of model.fills) drawFill(fill);
      }
      if (layers.signals) {
        const stacked = new Map<string, number>();
        for (const signal of model.signals) drawSignal(signal, stacked);
      }
      ctx.restore();

      function drawFill(fill: PlacedFill) {
        const px = x(fill.index);
        const price = fill.item.price.value;
        if (!visible(px) || price === null) return;
        const py = y(price);
        if (py === null) return;
        const ink = theme.provenance[fill.item.provenance];
        const filled = markerFill(fill.item.provenance) === "filled";
        ctx.beginPath();
        if (fill.item.role === "entry") {
          ctx.arc(px, py, size / 2 - 0.5, 0, Math.PI * 2);
        } else {
          ctx.rect(px - size / 2 + 0.5, py - size / 2 + 0.5, size - 1, size - 1);
        }
        paint(ctx, ink, filled);
        if (filled) {
          // A hairline in the chart background keeps a filled marker legible
          // over a candle of similar lightness.
          ctx.lineWidth = 1;
          ctx.strokeStyle = theme.background;
          ctx.stroke();
        }
      }

      function drawSignal(signal: PlacedSignal, stacked: Map<string, number>) {
        const px = x(signal.index);
        const bar = model.bars[signal.index];
        if (!visible(px) || !bar) return;
        const side = signal.item.side;
        const below = side === "long";
        const key = `${signal.index}|${below ? "below" : "above"}`;
        const n = stacked.get(key);
        const k = n === undefined ? 0 : n;
        stacked.set(key, k + 1);
        const anchor = y(below ? bar.l : bar.h);
        if (anchor === null) return;
        const gap = 4 + k * (size + 3);
        const ink = theme.provenance[signal.item.provenance];
        const filled = markerFill(signal.item.provenance) === "filled";
        let cy: number;
        if (side === "long") {
          triangle(ctx, px, anchor + gap, size, true);
          cy = anchor + gap + size * 0.45;
        } else if (side === "short") {
          triangle(ctx, px, anchor - gap, size, false);
          cy = anchor - gap - size * 0.45;
        } else {
          cy = anchor - gap - size / 2;
          diamond(ctx, px, cy, size);
        }
        paint(ctx, ink, filled);
        if (signal.item.outcome === "blocked") {
          ctx.beginPath();
          ctx.moveTo(px - size * 0.7, cy + size * 0.7);
          ctx.lineTo(px + size * 0.7, cy - size * 0.7);
          ctx.lineWidth = 1.75;
          ctx.strokeStyle = ink;
          ctx.stroke();
        }
      }
    });
  }
}

/**
 * Calendar events on the time axis: a vertical rule at the bar open at the
 * event's time, as loud as the API's impact says. HIGH: a solid full-height
 * rule in the foreground ink with its name at the top; MEDIUM: a dashed
 * muted rule with a short label; LOW: a tick on the axis only. The focused
 * event (`?event=`) is drawn in the accent. Neutral inks: no event colour
 * can be mistaken for P&L.
 */
class EventRenderer implements IPrimitivePaneRenderer {
  constructor(private readonly layer: OverlayPrimitive) {}

  draw(target: Parameters<IPrimitivePaneRenderer["draw"]>[0]) {
    const scope = this.layer.scope();
    if (!scope || !scope.state.layers.events) return;
    const { chart, state } = scope;
    const { theme, model } = state;
    const time = chart.timeScale();
    target.useMediaCoordinateSpace(({ context: ctx, mediaSize }) => {
      const height = mediaSize.height;
      ctx.save();
      ctx.textBaseline = "top";
      for (const ev of model.events) {
        const x = time.logicalToCoordinate(ev.index as Logical);
        if (x === null || x < -2 || x > mediaSize.width + 2) continue;
        const focused = state.focusEvent === ev.item.event_id;
        const impact = ev.item.impact;
        const ink = focused ? theme.drawing : impact === "high" ? theme.text : impact === "medium" ? theme.textMuted : theme.neutral;
        ctx.strokeStyle = ink;
        ctx.fillStyle = ink;
        if (impact === "high" || impact === "medium" || focused) {
          ctx.lineWidth = focused ? 2 : impact === "high" ? 1.25 : 1;
          ctx.setLineDash(impact === "high" || focused ? [] : [3, 3]);
          ctx.beginPath();
          ctx.moveTo(x + 0.5, 0);
          ctx.lineTo(x + 0.5, height);
          ctx.stroke();
          ctx.setLineDash([]);
          ctx.font = `${impact === "high" || focused ? 700 : 500} 10px ${theme.fontUi}`;
          const label = `${ev.item.currency} ${ev.item.name}`;
          ctx.fillText(label.length > 28 ? `${label.slice(0, 27)}…` : label, x + 3, 2);
        }
        // Every event, whatever its impact, has its tick on the time axis.
        const tick = impact === "high" || focused ? 10 : impact === "medium" ? 7 : 5;
        ctx.fillRect(x - 1, height - tick, 3, tick);
      }
      ctx.restore();
    });
  }
}

class View implements IPrimitivePaneView {
  constructor(
    private readonly order: PrimitivePaneViewZOrder,
    private readonly impl: IPrimitivePaneRenderer,
  ) {}
  zOrder(): PrimitivePaneViewZOrder {
    return this.order;
  }
  renderer(): IPrimitivePaneRenderer {
    return this.impl;
  }
}

export class OverlayPrimitive implements ISeriesPrimitive<Time> {
  private chart: IChartApiBase<Time> | null = null;
  private series: ISeriesApi<SeriesType, Time> | null = null;
  private request: (() => void) | null = null;
  private readonly views: readonly IPrimitivePaneView[];

  constructor(private state: OverlayDrawState) {
    this.views = [
      new View("bottom", new RegimeRenderer(this)),
      new View("bottom", new EventRenderer(this)),
      new View("top", new MarkerRenderer(this)),
    ];
  }

  attached(param: SeriesAttachedParameter<Time>) {
    this.chart = param.chart;
    this.series = param.series;
    this.request = param.requestUpdate;
  }

  detached() {
    this.chart = null;
    this.series = null;
    this.request = null;
  }

  update(state: OverlayDrawState) {
    this.state = state;
    this.request?.();
  }

  paneViews() {
    return this.views;
  }

  scope() {
    if (!this.chart || !this.series) return null;
    return { chart: this.chart, series: this.series, state: this.state };
  }
}
