import { expect, test } from "@playwright/test";
import {
  backtestSegments,
  buildChartModel,
  placeBacktestTrades,
  placePaperTrades,
  toSeconds,
  tradeSpan,
} from "../../lib/chart-model";
import {
  distToSegment,
  fibLevels,
  formatSpan,
  logicalToTime,
  measure,
  rayEnd,
  snapTime,
  timeToLogical,
} from "../../lib/drawing-geometry";
import type { BacktestTradeRow, BacktestTradesView } from "../../lib/types";
import { backtestTrades, barTime, chartBar, chartBars, chartOverlays } from "./fixtures";

/**
 * Unit tests, run in Node (no browser), for the pure parts the chart's trade
 * overlay and drawings rest on: placing a trade on the bar axis, the span of
 * its SL/TP segments, and the drawing geometry (time to bar and back, the
 * Fibonacci ratios, measurement, hit testing). None of these computes a
 * trading figure; they place the API's figures and the operator's points.
 */

test.describe.configure({ mode: "parallel" });
test.beforeEach(({}, testInfo) => {
  test.skip(testInfo.project.name !== "desktop", "pure Node tests; run once");
});

const times = Array.from({ length: 120 }, (_, i) => toSeconds(barTime(i)));
const H = 3_600;

test.describe("trade placement", () => {
  test("a trade spans the bar open at entry to the bar open at exit", () => {
    expect(tradeSpan(times, barTime(30), barTime(40))).toEqual({ from: 30, to: 40, entryIn: true, exitIn: true });
    // Mid-bar times belong to the bar that was open.
    const mid = (i: number) => new Date((times[i] as number) * 1000 + 25 * 60_000).toISOString();
    expect(tradeSpan(times, mid(30), mid(40))).toMatchObject({ from: 30, to: 40 });
  });

  test("a trade crossing an edge is clamped and says so; one wholly outside is not placed", () => {
    const before = new Date((times[0] as number) * 1000 - 5 * H * 1000).toISOString();
    const after = new Date((times[119] as number) * 1000 + 5 * H * 1000).toISOString();
    expect(tradeSpan(times, before, barTime(10))).toEqual({ from: 0, to: 10, entryIn: false, exitIn: true });
    expect(tradeSpan(times, barTime(110), after)).toEqual({ from: 110, to: 119, entryIn: true, exitIn: false });
    expect(tradeSpan(times, before, before)).toBeNull();
    expect(tradeSpan(times, after, after)).toBeNull();
    expect(tradeSpan([], barTime(1), barTime(2))).toBeNull();
  });

  test("SL/TP segments span the trade; a moved stop is a one-bar tick at the exit, never a path", () => {
    const view = backtestTrades("bt_0001").data as unknown as BacktestTradesView;
    const t3 = view.trades[2] as BacktestTradeRow;
    const segments = backtestSegments(t3, 90, 110);
    expect(segments.map((s) => [s.role, s.from, s.to, s.leg])).toEqual([
      ["stop", 90, 110, null],
      ["final_stop", 110, 110, null],
      ["target", 90, 110, 1],
      ["target", 90, 110, 2],
    ]);
    expect(segments[0]?.price).toBe(t3.stop_loss.value);
    expect(segments[1]?.price).toBe(t3.final_stop.value);
    // One target: no leg number.
    expect(backtestSegments(view.trades[0] as BacktestTradeRow, 30, 40).map((s) => s.leg)).toEqual([null, null]);
  });

  test("a trade with no recorded stop or target gets no segment, not an estimated one", () => {
    const old = backtestTrades("bt_old").data as unknown as BacktestTradesView;
    const placed = placeBacktestTrades(times, old);
    expect(placed.trades).toHaveLength(1);
    expect(placed.trades[0]?.segments).toEqual([]);
  });

  test("backtest trades carry the API's prices and the sign of its P&L", () => {
    const view = backtestTrades("bt_0001").data as unknown as BacktestTradesView;
    const placed = placeBacktestTrades(times, view);
    expect(placed.unplaced).toBe(0);
    expect(placed.trades.map((t) => [t.key, t.entryIndex, t.exitIndex, t.pnlSign, t.direction])).toEqual([
      ["bt|bt_0001|bt_t1", 30, 40, 1, "long"],
      ["bt|bt_0001|bt_t2", 60, 66, -1, "short"],
      ["bt|bt_0001|bt_t3", 90, 110, 1, "long"],
    ]);
    expect(placed.trades[0]?.entryPrice).toBe(chartBar(30).o);
    expect(placeBacktestTrades(times, null)).toEqual({ trades: [], unplaced: 0 });
    // A shorter window: the trades outside it are counted, not dropped silently.
    const shorter = placeBacktestTrades(times.slice(0, 50), view);
    expect(shorter.trades.map((t) => t.tradeId)).toEqual(["bt_t1"]);
    expect(shorter.unplaced).toBe(2);
  });

  test("paper trades come from the journal's connectors, with closed-trade levels as segments", () => {
    const overlays = chartOverlays().data;
    const closed = {
      ...overlays.levels[1],
      role: "stop",
      from: barTime(11),
      to: barTime(20),
      position_id: "trd_0001",
      session_id: "ps_001",
      session_label: "ps_001",
    };
    const model = buildChartModel(chartBars().data.bars as never, { ...overlays, levels: [...overlays.levels, closed] } as never, "tick_volume");
    const paper = placePaperTrades(model);
    expect(paper).toHaveLength(1);
    expect(paper[0]).toMatchObject({ key: "paper|ps_001|trd_0001", entryIndex: 11, exitIndex: 20, source: "paper", pnlSign: 1 });
    expect(paper[0]?.segments).toEqual([{ role: "stop", price: closed.price.value, from: 11, to: 20, leg: null }]);
  });
});

test.describe("drawing geometry", () => {
  test("time and bar index map both ways, inside, between and beyond the bars", () => {
    expect(timeToLogical(times, times[10] as number)).toBe(10);
    expect(timeToLogical(times, (times[10] as number) + H / 2)).toBeCloseTo(10.5, 10);
    // Beyond the last bar: extrapolated at the last interval (a ray into the future).
    expect(timeToLogical(times, (times[119] as number) + 3 * H)).toBeCloseTo(122, 10);
    expect(timeToLogical(times, (times[0] as number) - 2 * H)).toBeCloseTo(-2, 10);
    for (const logical of [0, 7.25, 64, 119, 125.5, -3]) {
      const t = logicalToTime(times, logical) as number;
      expect(timeToLogical(times, t)).toBeCloseTo(logical, 9);
    }
    expect(timeToLogical([], 5)).toBeNull();
  });

  test("a weekend gap takes no room on the axis: interpolation is per bar, not per hour", () => {
    // Friday 21:00 and then Sunday 22:00: adjacent bars.
    const fx = [toSeconds("2026-10-09T21:00:00Z"), toSeconds("2026-10-11T22:00:00Z"), toSeconds("2026-10-11T23:00:00Z")];
    expect(timeToLogical(fx, toSeconds("2026-10-11T22:00:00Z"))).toBe(1);
    expect(snapTime(fx, toSeconds("2026-10-10T12:00:00Z"))).toBe(fx[0]);
  });

  test("an anchor survives a timeframe change: the same time lands on the H4 bar that holds it", () => {
    const h4 = Array.from({ length: 30 }, (_, i) => toSeconds(barTime(i * 4)));
    const t = toSeconds(barTime(42)); // inside the H4 bar that opened at bar 40
    expect(timeToLogical(h4, t)).toBeCloseTo(10.5, 10);
    expect(logicalToTime(h4, timeToLogical(h4, t) as number)).toBe(t);
  });

  test("Fibonacci levels are the standard ratios between the two points (a drawing)", () => {
    const levels = fibLevels(1.2, 1.1, false);
    expect(levels.map((l) => l.ratio)).toEqual([0, 0.236, 0.382, 0.5, 0.618, 0.786, 1]);
    expect(levels[0]?.price).toBeCloseTo(1.1, 12);
    expect(levels[6]?.price).toBeCloseTo(1.2, 12);
    expect(levels[3]?.price).toBeCloseTo(1.15, 12);
    expect(levels[4]?.price).toBeCloseTo(1.1618, 12);
    const ext = fibLevels(1.2, 1.1, true);
    expect(ext.slice(-2).map((l) => [l.ratio, Number(l.price.toFixed(4)), l.extension])).toEqual([
      [1.272, 1.2272, true],
      [1.618, 1.2618, true],
    ]);
  });

  test("measure reads price, percent, bars and time between two points", () => {
    const m = measure(times, { t: times[10] as number, price: 1.1 }, { t: times[22] as number, price: 1.1055 });
    expect(m.priceDelta).toBeCloseTo(0.0055, 12);
    expect(m.percent).toBeCloseTo(0.5, 10);
    expect(m.bars).toBe(12);
    expect(m.seconds).toBe(12 * H);
    expect(formatSpan(12 * H)).toBe("12h");
    expect(formatSpan(3 * 86_400 + 4 * H)).toBe("3d 4h");
    expect(formatSpan(-45 * 60)).toBe("−45m");
    expect(measure(times, { t: times[1] as number, price: 0 }, { t: times[2] as number, price: 1 }).percent).toBeNull();
  });

  test("hit testing and ray clipping", () => {
    expect(distToSegment({ x: 5, y: 5 }, { x: 0, y: 0 }, { x: 10, y: 0 })).toBe(5);
    expect(distToSegment({ x: 15, y: 0 }, { x: 0, y: 0 }, { x: 10, y: 0 })).toBe(5);
    expect(rayEnd({ x: 0, y: 50 }, { x: 10, y: 40 }, 100, 100)).toEqual({ x: 50, y: 0 });
    expect(rayEnd({ x: 10, y: 10 }, { x: 20, y: 10 }, 100, 100)).toEqual({ x: 100, y: 10 });
  });
});
