import { expect, test } from "@playwright/test";
import {
  areaPath,
  divergingBin,
  extent,
  linePath,
  linear,
  nearestIndex,
  niceTicks,
} from "../../components/charts/analytics/scale";
import { gateStatusTone, gateVerdictTone, jobTone } from "../../components/research/tones";
import { researchHref } from "../../lib/research";

/**
 * Unit tests for the research analytics drawing helpers (no browser). These
 * are presentation only: value-to-pixel geometry, tick placement, colour bins
 * and badge tones. No statistic is computed in the frontend, so there is no
 * statistic to test here; the maths is tested in tests/unit/test_research_tearsheet.py.
 */
test.describe("analytics geometry", () => {
  test("linear maps domain ends to range ends", () => {
    const s = linear([10, 20], [0, 100]);
    expect(s(10)).toBe(0);
    expect(s(15)).toBe(50);
    expect(s(20)).toBe(100);
  });

  test("ticks are round and cover the span", () => {
    expect(niceTicks(0, 1, 5)).toEqual([0, 0.2, 0.4, 0.6, 0.8, 1]);
    expect(niceTicks(-3.3, 7.1, 5)).toEqual([-2.5, 0, 2.5, 5]);
    expect(niceTicks(5, 5)).toEqual([5]);
  });

  test("extent pads and can be forced through a value", () => {
    const [lo, hi] = extent([1, 2, null, 3], 0);
    expect(lo).toBeLessThan(0);
    expect(hi).toBeGreaterThan(3);
    expect(extent([null, undefined])).toEqual([0, 1]);
  });

  test("a missing point breaks the line and the area, it is never drawn as data", () => {
    expect(linePath([0, 1, 2, 3], [5, null, 6, 7])).toBe("M0.0,5.0M2.0,6.0L3.0,7.0");
    expect(areaPath([0, 1, 2], [1, 2, null], 10)).toBe("M0.0,10.0L0.0,1.0L1.0,2.0L1.0,10.0Z");
  });

  test("nearest index by time", () => {
    const times = [0, 10, 20, 30];
    expect(nearestIndex(times, 14)).toBe(1);
    expect(nearestIndex(times, 16)).toBe(2);
    expect(nearestIndex(times, -5)).toBe(0);
    expect(nearestIndex([], 3)).toBe(-1);
  });

  test("diverging bins are symmetric and clamped", () => {
    expect(divergingBin(1, 1)).toBe(5);
    expect(divergingBin(-1, 1)).toBe(-5);
    expect(divergingBin(0.1, 1)).toBe(1);
    expect(divergingBin(9, 1)).toBe(5);
    expect(divergingBin(0.5, 0)).toBe(0);
  });
});

test.describe("research tones and routes", () => {
  test("a failing gate is never green and an unknown value says so", () => {
    expect(gateVerdictTone("fail").tone).toBe("down");
    expect(gateVerdictTone("pass").tone).toBe("ok");
    expect(gateStatusTone("not_evaluated").tone).not.toBe("ok");
    const unknown = jobTone("exploded");
    expect(unknown.known).toBe(false);
    expect(unknown.tone).toBe("unknown");
  });

  test("entity routes are the agreed paths", () => {
    expect(researchHref.run("bt_1")).toBe("/research/runs/bt_1");
    expect(researchHref.strategy("donchian_breakout_atr")).toBe("/research/strategies/donchian_breakout_atr");
    expect(researchHref.compare(["a", "b"])).toBe("/research/compare?runs=a,b");
    expect(researchHref.chart("EURUSD", "bt_1")).toBe("/markets/EURUSD?run=bt_1");
  });
});
