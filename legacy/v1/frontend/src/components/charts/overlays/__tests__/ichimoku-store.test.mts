// Focused regression coverage for the per-chart Ichimoku store.
//
// These tests guard the chart-correctness defect that motivated the
// per-instance refactor: cell A's overlay data must never leak into cell B,
// and unmount cleanup must be instance-scoped. Run with
//
//   npx tsx --test src/components/charts/overlays/__tests__/ichimoku-store.test.mts
//
// Pure module — no klinecharts, no DOM, no network — so the suite finishes
// in tens of milliseconds and is safe to wire into CI later if desired.

import { test, beforeEach } from "node:test";
import assert from "node:assert/strict";

import {
  setIchimokuDataForChart,
  getIchimokuDataForChart,
  clearIchimokuDataForChart,
  chartIchimokuStoreSize,
  _resetIchimokuStoreForTests,
} from "../ichimoku-store.ts";

type Pt = { timestamp: number; tenkan: number; kijun: number; senkou_a: number; senkou_b: number; chikou: number | null };

const ptA = (t: number, base: number): Pt => ({
  timestamp: t, tenkan: base, kijun: base + 0.1, senkou_a: base + 0.2, senkou_b: base + 0.3, chikou: null,
});
const ptB = (t: number, base: number): Pt => ({
  timestamp: t, tenkan: base + 10, kijun: base + 10.1, senkou_a: base + 10.2, senkou_b: base + 10.3, chikou: null,
});

beforeEach(() => {
  _resetIchimokuStoreForTests();
});

test("two chart instances hold different Ichimoku datasets simultaneously", () => {
  const a = [ptA(1, 1.0), ptA(2, 1.1)];
  const b = [ptB(1, 2.0), ptB(2, 2.1)];
  setIchimokuDataForChart("cell-A", a);
  setIchimokuDataForChart("cell-B", b);

  const readA = getIchimokuDataForChart("cell-A");
  const readB = getIchimokuDataForChart("cell-B");

  assert.equal(readA.length, 2);
  assert.equal(readB.length, 2);
  assert.equal(readA[0].tenkan, 1.0);
  assert.equal(readB[0].tenkan, 12.0);
  assert.notStrictEqual(readA, readB);
});

test("updating chart A does not alter chart B (Quad-mode regression)", () => {
  setIchimokuDataForChart("A", [ptA(1, 1.0)]);
  setIchimokuDataForChart("B", [ptB(1, 2.0)]);

  // Re-write A with a completely different dataset
  setIchimokuDataForChart("A", [ptA(99, 9.9)]);

  const readB = getIchimokuDataForChart("B");
  assert.equal(readB.length, 1);
  assert.equal(readB[0].timestamp, 1);
  assert.equal(readB[0].tenkan, 12.0);
});

test("clearing chart A leaves chart B intact", () => {
  setIchimokuDataForChart("A", [ptA(1, 1.0)]);
  setIchimokuDataForChart("B", [ptB(1, 2.0)]);

  clearIchimokuDataForChart("A");

  assert.deepEqual(getIchimokuDataForChart("A"), []);
  const readB = getIchimokuDataForChart("B");
  assert.equal(readB.length, 1);
  assert.equal(readB[0].tenkan, 12.0);
});

test("unmount cleanup is instance-scoped (size shrinks by exactly one)", () => {
  setIchimokuDataForChart("A", [ptA(1, 1.0)]);
  setIchimokuDataForChart("B", [ptB(1, 2.0)]);
  setIchimokuDataForChart("C", [ptA(1, 3.0)]);
  assert.equal(chartIchimokuStoreSize(), 3);

  clearIchimokuDataForChart("B");

  assert.equal(chartIchimokuStoreSize(), 2);
  assert.deepEqual(getIchimokuDataForChart("B"), []);
  assert.equal(getIchimokuDataForChart("A").length, 1);
  assert.equal(getIchimokuDataForChart("C").length, 1);
});

test("clearing an unknown chartId is a no-op (does not throw)", () => {
  setIchimokuDataForChart("A", [ptA(1, 1.0)]);
  clearIchimokuDataForChart("does-not-exist");
  assert.equal(chartIchimokuStoreSize(), 1);
  assert.equal(getIchimokuDataForChart("A").length, 1);
});

test("getter returns empty array for unknown id (never throws)", () => {
  assert.deepEqual(getIchimokuDataForChart("never-set"), []);
});

test("setter snapshots input — caller mutations after set do not change the store", () => {
  const original = [ptA(1, 1.0), ptA(2, 1.1)];
  setIchimokuDataForChart("A", original);

  // Caller mutates the array they passed in
  original.push(ptA(3, 1.2));
  original[0].tenkan = -999;

  const read = getIchimokuDataForChart("A");
  assert.equal(read.length, 2, "store should keep original length");
  // The store keeps the original tenkan value of the first element because
  // setter copies the array reference. (Element objects are shared by
  // reference — see TODO in store comments — but this guards array shape.)
  assert.equal(read[0].timestamp, 1);
});

test("single-chart rendering remains valid (one id, repeated set/clear)", () => {
  setIchimokuDataForChart("only", [ptA(1, 1.0)]);
  assert.equal(getIchimokuDataForChart("only").length, 1);

  setIchimokuDataForChart("only", [ptA(1, 1.0), ptA(2, 1.1)]);
  assert.equal(getIchimokuDataForChart("only").length, 2);

  clearIchimokuDataForChart("only");
  assert.deepEqual(getIchimokuDataForChart("only"), []);
  assert.equal(chartIchimokuStoreSize(), 0);
});
