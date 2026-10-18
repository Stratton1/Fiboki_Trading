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
import type { PlacedTrade, TradeSegment } from "@/lib/chart-model";

/**
 * The trade layer: backtest trades from a stored run, and the stop and
 * target segments of paper and backtest trades, over the candles.
 *
 *  - backtest entry: ▲ long / ▼ short at the entry price, HOLLOW in the
 *    simulated ink (the provenance grammar: hollow simulated, filled
 *    executed); exit: a hollow square at the exit price;
 *  - entry-to-exit connector coloured by the SIGN of the API's net P&L (the
 *    only green and red on the chart), dashed for simulated evidence;
 *  - stop (dashed) and target (dotted) as short horizontal segments spanning
 *    the trade's own bars, in the trade's provenance ink, labelled SL / TP
 *    (TP1, TP2 for legs) with BT or PAPER, so a backtest level is never read
 *    as a live one; a stop the engine moved ends in a one-bar tick at its
 *    final level, labelled with the rule that moved it.
 *
 * Paper entry and exit MARKERS stay with OverlayPrimitive (they are the
 * journal's fills); this layer adds only their segments, so nothing is drawn
 * twice. The selected trade is drawn heavier with its labels always shown.
 *
 * It draws; it computes nothing. Every price is the API's; positions come
 * from the chart's own coordinate conversion.
 */

export interface TradeLayerState {
  trades: readonly PlacedTrade[];
  theme: ChartTheme;
  /** Show backtest trades' markers and connectors. */
  backtest: boolean;
  /** Show paper trades' segments (their markers are OverlayPrimitive's). */
  paper: boolean;
  selected: string | null;
  hovered: string | null;
}

type Ctx = CanvasRenderingContext2D;

const SEGMENT_DASH: Record<TradeSegment["role"], number[]> = {
  stop: [5, 3],
  final_stop: [],
  target: [1.5, 2.5],
};

function label(segment: TradeSegment, trade: PlacedTrade): string {
  const who = trade.source === "backtest" ? "BT" : "PAPER";
  if (segment.role === "target") return `TP${segment.leg ?? ""} ${who}`;
  if (segment.role === "final_stop") {
    const rule = trade.row?.stop_moved_by;
    return `SL ${rule ? `${rule} ` : ""}final`;
  }
  return `SL ${who}`;
}

class TradeRenderer implements IPrimitivePaneRenderer {
  constructor(private readonly layer: TradePrimitive) {}

  draw(target: Parameters<IPrimitivePaneRenderer["draw"]>[0]) {
    const scope = this.layer.scope();
    if (!scope) return;
    const { chart, series, state } = scope;
    const { theme } = state;
    const time = chart.timeScale();
    const spacing = time.options().barSpacing;
    const size = Math.max(7, Math.min(12, spacing * 0.9));
    const range = time.getVisibleLogicalRange();
    const x = (index: number) => time.logicalToCoordinate(index as Logical);
    const y = (price: number) => series.priceToCoordinate(price);

    target.useMediaCoordinateSpace(({ context: ctx, mediaSize }) => {
      ctx.save();
      ctx.font = `600 9.5px ${theme.fontUi}`;
      ctx.textBaseline = "bottom";
      const emphasised: PlacedTrade[] = [];
      for (const trade of state.trades) {
        const shown = trade.source === "backtest" ? state.backtest : state.paper;
        if (!shown) continue;
        // Cheap cull: a trade wholly outside the visible bars draws nothing.
        if (range && (trade.exitIndex < range.from - 1 || trade.entryIndex > range.to + 1)) continue;
        if (trade.key === state.selected || trade.key === state.hovered) {
          emphasised.push(trade);
          continue;
        }
        drawTrade(ctx, trade, false);
      }
      for (const trade of emphasised) {
        // The selected trade's bars get a faint band, so a row picked in the
        // table is found at a glance after the chart scrolls to it.
        if (trade.key === state.selected) band(ctx, trade);
        drawTrade(ctx, trade, true);
      }
      ctx.restore();

      function band(c: Ctx, trade: PlacedTrade) {
        const a = x(trade.entryIndex);
        const b = x(trade.exitIndex);
        if (a === null || b === null) return;
        const half = Math.max(2, spacing / 2);
        c.fillStyle = theme.drawingFill;
        c.fillRect(a - half, 0, b - a + 2 * half, mediaSize.height);
      }

      function drawTrade(c: Ctx, trade: PlacedTrade, strong: boolean) {
        const ink = theme.provenance[trade.provenance];
        const hollow = markerFill(trade.provenance) === "hollow";
        const x1raw = x(trade.entryIndex);
        const x2raw = x(trade.exitIndex);
        if (x1raw === null || x2raw === null) return;
        const half = Math.max(2, spacing / 2);
        // Segments: from the left edge of the entry bar to the right edge of the exit bar.
        for (const segment of trade.segments) {
          const sy = y(segment.price);
          if (sy === null) continue;
          const sx1 = (segment.from === trade.entryIndex ? x1raw : (x(segment.from) ?? x1raw)) - half;
          const sx2 = (segment.to === trade.exitIndex ? x2raw : (x(segment.to) ?? x2raw)) + half;
          if (sx2 < 0 || sx1 > mediaSize.width) continue;
          c.beginPath();
          c.setLineDash(SEGMENT_DASH[segment.role]);
          c.lineWidth = strong ? 2 : segment.role === "final_stop" ? 2 : 1.25;
          c.strokeStyle = ink;
          c.globalAlpha = strong ? 1 : 0.85;
          c.moveTo(sx1, sy);
          c.lineTo(sx2, sy);
          c.stroke();
          c.globalAlpha = 1;
          if (strong || sx2 - sx1 > 64) {
            c.setLineDash([]);
            c.fillStyle = ink;
            const text = label(segment, trade);
            c.fillText(text, Math.max(2, sx1) + 2, sy - 2);
          }
        }
        c.setLineDash([]);
        if (trade.source !== "backtest") return;
        // Connector, coloured by the sign of the platform's net P&L.
        if (trade.entryPrice !== null && trade.exitPrice !== null) {
          const y1 = y(trade.entryPrice);
          const y2 = y(trade.exitPrice);
          if (y1 !== null && y2 !== null) {
            c.beginPath();
            c.moveTo(x1raw, y1);
            c.lineTo(x2raw, y2);
            c.lineWidth = strong ? 2.25 : 1.25;
            c.strokeStyle =
              trade.pnlSign === null || trade.pnlSign === 0
                ? theme.neutral
                : trade.pnlSign > 0
                  ? theme.pnlUp
                  : theme.pnlDown;
            c.setLineDash(hollow ? [4, 3] : []);
            c.stroke();
            c.setLineDash([]);
          }
        }
        // Entry: a triangle pointing the trade's way; exit: a square.
        if (trade.entryPrice !== null && trade.entryInWindow) {
          const py = y(trade.entryPrice);
          if (py !== null) {
            const up = trade.direction !== "short";
            const h = size * 0.9;
            c.beginPath();
            if (up) {
              c.moveTo(x1raw, py - h / 2);
              c.lineTo(x1raw + size / 2, py + h / 2);
              c.lineTo(x1raw - size / 2, py + h / 2);
            } else {
              c.moveTo(x1raw, py + h / 2);
              c.lineTo(x1raw + size / 2, py - h / 2);
              c.lineTo(x1raw - size / 2, py - h / 2);
            }
            c.closePath();
            paint(c, ink, hollow, theme.background, strong);
          }
        }
        if (trade.exitPrice !== null && trade.exitInWindow) {
          const py = y(trade.exitPrice);
          if (py !== null) {
            const s = size - 2;
            c.beginPath();
            c.rect(x2raw - s / 2, py - s / 2, s, s);
            paint(c, ink, hollow, theme.background, strong);
          }
        }
      }
    });
  }
}

function paint(c: Ctx, ink: string, hollow: boolean, background: string, strong: boolean) {
  if (hollow) {
    // A background fill under the outline keeps a hollow marker legible over a candle.
    c.fillStyle = background;
    c.fill();
    c.lineWidth = strong ? 2.25 : 1.5;
    c.strokeStyle = ink;
    c.stroke();
    return;
  }
  c.fillStyle = ink;
  c.fill();
  c.lineWidth = 1;
  c.strokeStyle = background;
  c.stroke();
}

class View implements IPrimitivePaneView {
  constructor(private readonly impl: IPrimitivePaneRenderer) {}
  zOrder(): PrimitivePaneViewZOrder {
    return "top";
  }
  renderer(): IPrimitivePaneRenderer {
    return this.impl;
  }
}

export class TradePrimitive implements ISeriesPrimitive<Time> {
  private chart: IChartApiBase<Time> | null = null;
  private series: ISeriesApi<SeriesType, Time> | null = null;
  private request: (() => void) | null = null;
  private readonly views: readonly IPrimitivePaneView[];

  constructor(private state: TradeLayerState) {
    this.views = [new View(new TradeRenderer(this))];
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

  update(state: TradeLayerState) {
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
