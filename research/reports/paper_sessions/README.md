# PAPER replay sessions on the migrated store

Produced by `scripts/run_paper_session.py`, which assembles the real runtime
(`fiboki.workers.runtime.build_replay_session`) end to end:

    market data -> market state -> strategy -> sizing -> risk gateway ->
    execution service -> paper venue -> telemetry

over bars read by dataset version out of `var/datastore`. PAPER only: the
execution service is constructed with `ExecutionMode.PAPER` against a
`PaperBroker`, and none of the five live controls was touched.

Both runs replay the **whole** XAUUSD H4 dataset -- 26,837 bars,
2009-03-15 .. 2025-12-31, `ds_af8fc1b3143b3530c3f24ded` -- with a real seed
document bound to its **declared defaults** (`document.bind_defaults()`, no
sweep), a £/$100,000 account, `IG_REALISTIC`, 0.5% risk per trade, and
`PAPER_LIMITS`.

| | donchian_breakout_atr | fib_golden_pocket_pullback |
|---|---:|---:|
| bars replayed | 26,837 | 26,837 |
| signals from the strategy | 2,478 | 568 |
| gateway attempts | 2,478 | 568 |
| gateway blocks | 1,132 (all `total_drawdown`) | 72 (all `total_drawdown`) |
| accepted at the venue | 1,346 | 496 |
| venue rejections | 1,340 `max_per_instrument` | 217 `max_per_instrument`, 1 `broker_reject` |
| closed trades | **5** | **278** |
| exit legs | 5 | 352 |
| open at the end | 1 | 0 |
| gross P&L | −2,468.89 | −4,061.24 |
| costs | 207.82 | 10,643.50 |
| net P&L | **−2,676.71** | **−14,704.74** |
| win rate | 0.0% (0/5) | 26.6% (74/278) |
| closing balance | 97,323.29 | 85,295.26 |

Costs are per component, measured, not asserted:

| | spread | commission | slippage | financing |
|---|---:|---:|---:|---:|
| donchian_breakout_atr | 77.95 | 0.00 | 1.06 | 128.81 |
| fib_golden_pocket_pullback | 7,260.43 | 0.00 | 66.04 | 3,317.03 |

All four formerly-dead risk-context inputs were live for the whole of both runs
(`daily_pnl`, `weekly_pnl`, `correlated_exposure`, `realised_portfolio_vol`), so
the gateway's fail-closed freshness and spread checks had real data to pass on
rather than blocking every order on `unknown`.

## Read the donchian run as a finding, not as a result

Five closed trades in 26,837 bars is not a strategy result. It is the signature
`research/reports/xauusd_h4/README.md` documents for the **superseded backtest
engine**: `donchian_breakout_atr` declares no take-profit at all and relies
entirely on an ATR chandelier trail, and "with the trail unimplemented every
position ran to its hard stop or to the end of the data" -- there it produced six
trades in thirteen years. The backtest engine was fixed and the same document
then produced 583 trades.

The paper replay runtime reproduces the old signature. `build_replay_session`
hands the `PaperBroker` whatever stop and take-profit prices the *signal*
carries; the document-level exit vocabulary (chandelier trail,
`move_stop_to_breakeven_at_r`, `max_bars_in_trade`, `cooldown_bars_after_exit`,
`allow_reversal_on_opposite_signal`) is applied by the backtest engine and not by
the paper venue. So a document whose exits live entirely in that vocabulary runs
its positions to the hard stop or to the end of the replay, one position at a
time, which is also why 1,340 of its accepted orders were refused
`max_per_instrument` and why closing equity (234,199) is a single position that
never closed rather than a P&L.

**This is a backtest/paper parity gap, and it is in `src/fiboki/workers/` and
`src/fiboki/broker/`, not in the data.** `tests/integration/test_paper_backtest_parity.py`
passes because it exercises signals that carry explicit stops and targets.
Until it is closed, a paper number for a trail-managed document is not comparable
to that document's backtest number. The `fib_golden_pocket_pullback` run is the
usable one: it declares take-profits, so the venue has managed exits, and its 278
trades and 352 exit legs are genuine end-to-end measurements.

## These rows are NOT in the workstation

The API has no paper journal. `fiboki.api.platform.Platform` builds its trades
and positions from `fiboki.api.seed.generate` in its constructor, there is no
`FIBOKI_PAPER_JOURNAL`-style setting in `fiboki.api.settings`, and
`Platform.data_source` returns the literal `"seed"`. So Trading -> Portfolio,
Execution, Risk and Exposure are still served from the labelled fixture, and
`/api/health`'s `data_provenance` check stays `degraded` on
`trade_and_position_records`. Nothing here has been relabelled to hide that.
