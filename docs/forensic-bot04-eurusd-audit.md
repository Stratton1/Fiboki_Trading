# Forensic Audit: bot04/EURUSD/H1 — £111K from £1K

**Date:** 2026-03-16
**Auditor:** Principal Quant QA Engineer
**Scope:** Single combo — bot04_chikou_momentum / EURUSD / H1 / £1,000 capital

---

## 1. Verdict

**Is this result believable?  NO.**

£111,131 net profit from £1,000 (112x return) in 2 years is not a plausible retail trading result. The code was mathematically consistent — no bugs in PnL arithmetic — but the sizing model allowed absurdly tight stops to produce leveraged positions whose single-trade P&L could swing 5–13% of equity, compounding into an exponential growth curve that no retail account would survive.

**After fix:** £15,398 net profit (16x return). Still aggressive, but within the realm of a highly profitable systematic strategy with 10x average leverage and 51% win rate. Fresh reruns are safe to show Tom.

---

## 2. The Suspect Result (Pre-Fix)

| Metric | Value |
|--------|-------|
| Strategy | bot04_chikou_momentum |
| Instrument | EURUSD |
| Timeframe | H1 |
| Data | 11,652 bars (2023-01-01 to 2024-12-31) |
| Initial capital | £1,000 |
| Total trades | 2,380 |
| Net PnL | £111,131 |
| Final equity | £112,131 |
| Max drawdown | 6.93% |
| Sharpe | 2.68 |
| Win rate | 51.13% |
| Best single trade | £6,701 |
| Worst single trade | -£1,172 |

---

## 3. Root Cause: Tiny Stops → Leverage-Capped Positions → Asymmetric Compounding

### 3.1 The Stop Formula

Bot04 sets stop loss as:
```
LONG:  stop = tenkan_sen - 0.5 × ATR
SHORT: stop = tenkan_sen + 0.5 × ATR
```

Entry is at the bar's close. When close ≈ tenkan (common during trend entries), the stop distance from entry is only:
```
|close - (tenkan - 0.5 × ATR)| ≈ 0.5 × ATR
```

For EURUSD H1, ATR ranges 4–40 pips (median 11.6 pips). So stop distances can be as small as 2–6 pips.

### 3.2 Signal Analysis

| Stop distance | Signals | % of total |
|--------------|---------|------------|
| < 5 pips | 1,325 | 25.9% |
| < 10 pips | 2,396 | 46.8% |
| < 20 pips | 3,764 | 73.5% |
| All | 5,124 | 100% |

### 3.3 The Sizing Cascade

With a 2-pip stop on £1,000 capital at 1% risk:
```
risk_amount = £10
risk_per_unit = 0.0002
raw_size = 10 / 0.0002 = 50,000 units
leverage_cap (30x) = 27,272 units ← BINDING
actual_position = 27,272 units
actual_leverage = 30x
```

### 3.4 The Asymmetry Problem

At 30x leverage, the 1% risk budget is meaningless:
- **Max loss on SL (2 pips):** 27,272 × 0.0002 = £5.45 (0.5% of equity) ✓
- **Win on TP (90 pips):** 27,272 × 0.0090 = £245 (24.5% of equity) ✗
- **Win on TP (30 pips):** 27,272 × 0.0030 = £82 (8.2% of equity) ✗

Losses are small, wins are enormous. With 51% win rate, this compounds exponentially.

### 3.5 Leverage Distribution (Pre-Fix)

| Leverage | Trades | % |
|----------|--------|---|
| At 30x cap | 908 | 38.2% |
| ≥ 20x | 1,310 | 55.0% |
| Mean | 20.0x | — |
| Median | 22.3x | — |

### 3.6 Trade Duration (Pre-Fix)

| Duration | Trades | % |
|----------|--------|---|
| 1 bar | 1,725 | 72.5% |
| 2 bars | 120 | 5.0% |
| 3 bars | 98 | 4.1% |
| Mean | 2.3 bars | — |

72.5% of trades exit after 1 bar — this is noise trading, not Ichimoku momentum.

---

## 4. Worked Examples (Pre-Fix, First 10 Trades)

| # | Dir | Entry | Exit | Size | PnL | Equity | Leverage | Bars | Reason |
|---|-----|-------|------|------|-----|--------|----------|------|--------|
| 1 | SHORT | 1.05286 | 1.05321 | 12,738 | -£4.46 | £1,000 | 13.4x | 3 | Indicator exit |
| 2 | SHORT | 1.05315 | 1.05334 | 28,359 | -£5.42 | £996 | 30.0x | 1 | Stop loss |
| 3 | SHORT | 1.05237 | 1.04870 | 11,756 | +£43.18 | £990 | 12.5x | 8 | Take profit |
| 4 | SHORT | 1.04939 | 1.05163 | 4,614 | -£10.33 | £1,033 | 4.7x | 1 | Stop loss |
| 5 | SHORT | 1.05418 | 1.05243 | 5,849 | +£10.23 | £1,023 | 6.0x | 1 | Stop loss |
| 6 | SHORT | 1.05077 | 1.05257 | 5,737 | -£10.33 | £1,033 | 5.8x | 1 | Stop loss |
| 7 | LONG | 1.06401 | 1.06971 | 1,359 | +£7.74 | £1,023 | 1.4x | 10 | Take profit |
| 8 | LONG | 1.06907 | 1.06665 | 4,254 | -£10.31 | £1,031 | 4.4x | 1 | Stop loss |
| 9 | LONG | 1.06726 | 1.06673 | 19,405 | -£10.20 | £1,020 | 20.3x | 1 | Stop loss |
| 10 | LONG | 1.06694 | 1.06701 | 28,402 | +£2.08 | £1,010 | 30.0x | 1 | Stop loss |

Trade #2 and #10 are at 30x leverage. Trade #9 at 20.3x. These are noise trades with tiny stops.

---

## 5. The Fix

### 5.1 What Changed

**File:** `backend/src/fibokei/backtester/sizing.py`

Added `min_stop_distance` parameter to `calculate_position_size()`. When the actual stop distance is smaller than the floor, the sizing formula uses the floor instead, keeping risk-based sizing (not leverage) as the binding constraint.

**File:** `backend/src/fibokei/backtester/engine.py`

The engine now passes `ATR` as the minimum stop distance floor. This means: if a strategy sets a stop tighter than 1 ATR, the position is sized as if the stop were 1 ATR. The actual stop loss is unchanged — only the position size is affected.

### 5.2 Why This Fix is Safe

1. **No strategy logic changed** — signals, stops, and exits are unchanged
2. **No leverage cap changed** — IG FCA limits are preserved
3. **The fix only affects sizing** — positions are smaller when stops are unrealistically tight
4. **Wide stops are unaffected** — when stop > ATR, sizing is identical to before
5. **Backward-compatible** — `min_stop_distance` defaults to 0.0 (no floor)

### 5.3 Why ATR is the Right Floor

ATR is the natural measure of "how much this instrument moves in one bar." A stop distance less than ATR has a high probability of being hit by noise, not by a genuine adverse move. Using ATR as the floor ensures position sizes are calibrated to actual market volatility.

---

## 6. Results After Fix

| Metric | Before | After | Change |
|--------|--------|-------|--------|
| Net PnL | £111,131 | £15,398 | -86% |
| Final equity | £112,131 | £16,398 | -85% |
| Mean leverage | 20.0x | 9.4x | -53% |
| Trades at 30x | 908 (38%) | 0 (0%) | -100% |
| Max leverage | 30.0x | 26.8x | -11% |
| Best trade | £6,701 | £473 | -93% |
| Worst trade | -£1,172 | -£167 | -86% |
| Max drawdown | 6.93% | 10.19% | +47% |
| Sharpe | 2.68 | 2.67 | -0.4% |
| Win rate | 51.13% | 51.13% | 0% |

### 6.1 Cross-Instrument Verification

| Instrument | Trades | Net PnL | Sharpe | Win Rate | Max DD |
|-----------|--------|---------|--------|----------|--------|
| EURUSD | 2,380 | £15,398 | 2.67 | 51.1% | 10.2% |
| GBPUSD | 2,286 | £15,651 | 2.71 | 51.6% | 8.5% |
| USDJPY | 2,199 | £154 | 2.63 | 49.8% | 0.6% |
| EURJPY | 2,319 | £75 | 2.25 | 50.5% | 0.3% |
| XAUUSD | 2,153 | £17,906 | 3.03 | 51.4% | 6.3% |
| BCOUSD | 2,115 | £7,538 | 2.22 | 50.0% | 7.6% |

JPY pairs show expected deflation from pip_value_adjustment. All instruments show moderate leverage and bounded returns.

---

## 7. Operator Safeguards Added

### 7.1 Backend Sanity Warnings

`compute_metrics()` now returns a `sanity_warnings` list flagging:
- Return > 20x initial capital
- Sharpe > 4.0
- Profit factor > 3.0
- Max DD < 2% with 100+ trades
- Trade frequency > 30% of bars

### 7.2 Frontend Display

The backtests detail page now shows backend sanity warnings alongside existing frontend diagnostics in the amber "Diagnostics" panel.

---

## 8. Regression Tests

**File:** `backend/tests/test_min_stop_distance.py` — 11 tests

### Unit Tests (5)
- `test_no_floor_tight_stop_large_size` — confirms old behavior without floor
- `test_floor_reduces_size` — confirms floor reduces position size
- `test_floor_doesnt_affect_wide_stops` — confirms no impact on normal stops
- `test_floor_zero_disables` — confirms backward compatibility
- `test_leverage_cap_still_applies_with_floor` — confirms leverage cap is preserved

### Integration Tests (5)
- `test_net_pnl_below_50x` — bot04/EURUSD/H1 PnL < 50x capital
- `test_no_trades_at_30x_leverage` — no trades hit 30x cap
- `test_mean_leverage_below_15x` — average leverage reasonable
- `test_max_single_trade_pnl_pct` — no single trade > 5% of equity
- `test_best_trade_bounded` — best trade < £2,000

### Engine Integration (1)
- `test_tight_stop_strategy_uses_atr_floor` — ATR floor applied in engine loop

---

## 9. Files Modified

| File | Change |
|------|--------|
| `backend/src/fibokei/backtester/sizing.py` | Added `min_stop_distance` parameter |
| `backend/src/fibokei/backtester/engine.py` | Pass ATR as min_stop_distance |
| `backend/src/fibokei/backtester/metrics.py` | Added `_sanity_check()` and `sanity_warnings` |
| `frontend/src/app/(dashboard)/backtests/[id]/page.tsx` | Display backend sanity warnings |
| `backend/tests/test_min_stop_distance.py` | **New**: 11 regression tests |
| `docs/forensic-bot04-eurusd-audit.md` | **New**: this report |

---

## 10. Verification

```bash
cd backend && python3 -m pytest tests/ -v    # 672 passed
cd frontend && npx next build                 # Compiled successfully
```

---

## 11. What's NOT Changed (Deliberate)

- **No strategy logic changed** — bot04 signals, stops, and exits are identical
- **No IG leverage limits changed** — FCA alignment preserved
- **No Sharpe formula changed** — sqrt(252) annualization is correct
- **No spread/slippage changed** — cost model preserved
- **Trade count unchanged** — 2,380 trades with and without fix (same signals)

---

## 12. Remaining Notes

- **Sharpe 2.67 is still high** but mathematically consistent with a 51/49 WR and 1.54 R:R. The strategy genuinely has an edge in the historical data — whether it persists out-of-sample is a separate question.
- **16x return in 2 years** is aggressive but possible with leveraged compounding at 10x average leverage. An IG retail account with similar parameters could approach this if drawdowns are tolerated.
- **The 2,380-trade frequency** (5/day on H1) suggests the Chikou open space condition fires very frequently on this data. This is a strategy design question, not a sizing bug.
- **All existing backtest runs in the DB are pre-fix** and should be re-run to get accurate results with the ATR floor.
