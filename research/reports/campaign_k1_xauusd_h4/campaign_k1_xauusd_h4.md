# Campaign k1_xauusd_h4

- actor: `script:run_discovery_campaign`
- created: 2026-09-19T09:05:22.924498+00:00
- gate set: `v2.0.0-audit` (`7856ba13a63b5700`)
- datasets: XAUUSD_H4 -> `ds_3c1473cbf51e5a641ef41cbf`

## Trial accounting

- planned in this campaign: **166**
- already in the ledger for the same dataset version(s): **160**
- **true trial count: 326**
- ladder evaluations requested: 222
- engine backtests actually run: 0 (lower when the deterministic evaluation cache was warm)

The deflation threshold is E[max SR] for a search of 326 trials. Using the largest cross-trial Sharpe variance observed in this campaign (5.58337e-05, measured at rung 1's in-sample screen), that threshold is 0.0218, on the same PER-BAR basis as the candidate Sharpes (nothing here is annualised): a Sharpe at or below it is what a search this size is EXPECTED to produce from strategies with no edge at all. No candidate survived as far as rung 5, so the threshold is reported as the bar a survivor WOULD have had to clear, not as a bar anything was measured against.

## What was tried

| strategy | cell | origin | n trials | external N | verdict | died at | deflated SR | threshold |
|---|---|---|---|---|---|---|---|---|
| `donchian_breakout_atr_m3ecf77ae` | XAUUSD H4 | add_filter | 8 | 318 | reject | RUNG 2 WALK_FORWARD | - | 0.008882 |
| `donchian_breakout_atr_mbdfe4742` | XAUUSD H4 | alter_stop_model | 4 | 322 | reject | RUNG 0 SANITY | - | - |
| `donchian_breakout_atr_m3c934a3b` | XAUUSD H4 | alter_target_model | 8 | 318 | reject | RUNG 0 SANITY | - | - |
| `donchian_breakout_atr_m76dbb4f7` | XAUUSD H4 | change_regime_gate | 8 | 318 | reject | RUNG 2 WALK_FORWARD | - | 0.01248 |
| `donchian_breakout_atr_m427fba0e` | XAUUSD H4 | change_session_restriction | 8 | 318 | reject | RUNG 2 WALK_FORWARD | - | 0.02183 |
| `donchian_breakout_atr_m79f18824` | XAUUSD H4 | change_confirmation_rule | 8 | 318 | reject | RUNG 2 WALK_FORWARD | - | 0.008959 |
| `donchian_breakout_atr_mf2553d34` | XAUUSD H4 | simplify | 8 | 318 | reject | RUNG 0 SANITY | - | - |
| `donchian_breakout_atr_m30945c80` | XAUUSD H4 | combine | 2 | 324 | reject | RUNG 0 SANITY | - | - |
| `donchian_breakout_atr_m217dad55` | XAUUSD H4 | combine | 2 | 324 | reject | RUNG 0 SANITY | - | - |
| `fib_golden_pocket_pullback_ma8f9f32b` | XAUUSD H4 | add_filter | 4 | 322 | reject | RUNG 0 SANITY | - | - |
| `fib_golden_pocket_pullback_md15c64ac` | XAUUSD H4 | alter_stop_model | 2 | 324 | reject | RUNG 0 SANITY | - | - |
| `fib_golden_pocket_pullback_mb82cde01` | XAUUSD H4 | alter_target_model | 4 | 322 | reject | RUNG 0 SANITY | - | - |
| `fib_golden_pocket_pullback_mdd2357fa` | XAUUSD H4 | change_regime_gate | 4 | 322 | reject | RUNG 0 SANITY | - | - |
| `fib_golden_pocket_pullback_md3507b8f` | XAUUSD H4 | change_session_restriction | 4 | 322 | reject | RUNG 0 SANITY | - | - |
| `fib_golden_pocket_pullback_m88fd2d1c` | XAUUSD H4 | change_confirmation_rule | 4 | 322 | reject | RUNG 0 SANITY | - | - |
| `fib_golden_pocket_pullback_me01e2d1a` | XAUUSD H4 | simplify | 4 | 322 | reject | RUNG 0 SANITY | - | - |
| `fib_golden_pocket_pullback_m76354e7e` | XAUUSD H4 | combine | 2 | 324 | reject | RUNG 0 SANITY | - | - |
| `fib_golden_pocket_pullback_mb73e28fd` | XAUUSD H4 | combine | 2 | 324 | reject | RUNG 0 SANITY | - | - |
| `ichimoku_kumo_trend_m689694c1` | XAUUSD H4 | add_filter | 8 | 318 | reject | RUNG 0 SANITY | - | - |
| `ichimoku_kumo_trend_m4195c8bf` | XAUUSD H4 | alter_stop_model | 4 | 322 | reject | RUNG 0 SANITY | - | - |
| `ichimoku_kumo_trend_m8fbcd36c` | XAUUSD H4 | alter_target_model | 8 | 318 | reject | RUNG 0 SANITY | - | - |
| `ichimoku_kumo_trend_me73acb6c` | XAUUSD H4 | change_confirmation_rule | 8 | 318 | reject | RUNG 0 SANITY | - | - |
| `ichimoku_kumo_trend_mca231a0f` | XAUUSD H4 | simplify | 8 | 318 | reject | RUNG 0 SANITY | - | - |
| `ichimoku_kumo_trend_m0bd80cab` | XAUUSD H4 | combine | 4 | 322 | reject | RUNG 0 SANITY | - | - |
| `macd_ema_trend_hybrid_ma2bc336b` | XAUUSD H4 | add_filter | 8 | 318 | reject | RUNG 0 SANITY | - | - |
| `macd_ema_trend_hybrid_m03d3ee23` | XAUUSD H4 | alter_stop_model | 4 | 322 | reject | RUNG 0 SANITY | - | - |
| `macd_ema_trend_hybrid_m4b9a1dcd` | XAUUSD H4 | change_regime_gate | 8 | 318 | reject | RUNG 0 SANITY | - | - |
| `macd_ema_trend_hybrid_mb7889927` | XAUUSD H4 | change_confirmation_rule | 8 | 318 | reject | RUNG 0 SANITY | - | - |
| `macd_ema_trend_hybrid_mc45594a0` | XAUUSD H4 | simplify | 8 | 318 | reject | RUNG 0 SANITY | - | - |
| `macd_ema_trend_hybrid_m270351c7` | XAUUSD H4 | combine | 4 | 322 | reject | RUNG 0 SANITY | - | - |

### Why each one stopped

- donchian_breakout_atr_m3ecf77ae on XAUUSD H4: REJECT at RUNG 2 WALK_FORWARD -- walk_forward_efficiency: observed 39.03, required gte 50, short by 10.97
- donchian_breakout_atr_mbdfe4742 on XAUUSD H4: REJECT at RUNG 0 SANITY -- RUNG 0 SANITY: expectancy -1.36071 at declared defaults is not positive; there is no edge to validate.
- donchian_breakout_atr_m3c934a3b on XAUUSD H4: REJECT at RUNG 0 SANITY -- RUNG 0 SANITY: expectancy -3.93769 at declared defaults is not positive; there is no edge to validate.
- donchian_breakout_atr_m76dbb4f7 on XAUUSD H4: REJECT at RUNG 2 WALK_FORWARD -- oos_window_hit_rate: observed 0.5, required gte 0.6, short by 0.1
- donchian_breakout_atr_m427fba0e on XAUUSD H4: REJECT at RUNG 2 WALK_FORWARD -- walk_forward_efficiency: observed 13.73, required gte 50, short by 36.27
- donchian_breakout_atr_m79f18824 on XAUUSD H4: REJECT at RUNG 2 WALK_FORWARD -- oos_window_hit_rate: observed 0.5, required gte 0.6, short by 0.1
- donchian_breakout_atr_mf2553d34 on XAUUSD H4: REJECT at RUNG 0 SANITY -- RUNG 0 SANITY: expectancy -0.892528 at declared defaults is not positive; there is no edge to validate.
- donchian_breakout_atr_m30945c80 on XAUUSD H4: REJECT at RUNG 0 SANITY -- RUNG 0 SANITY: expectancy -9.83642 at declared defaults is not positive; there is no edge to validate.
- donchian_breakout_atr_m217dad55 on XAUUSD H4: REJECT at RUNG 0 SANITY -- RUNG 0 SANITY: expectancy -2.93725 at declared defaults is not positive; there is no edge to validate.
- fib_golden_pocket_pullback_ma8f9f32b on XAUUSD H4: REJECT at RUNG 0 SANITY -- min_trades: observed 231, required gte 400, short by 169
- fib_golden_pocket_pullback_md15c64ac on XAUUSD H4: REJECT at RUNG 0 SANITY -- min_trades: observed 213, required gte 400, short by 187
- fib_golden_pocket_pullback_mb82cde01 on XAUUSD H4: REJECT at RUNG 0 SANITY -- min_trades: observed 218, required gte 400, short by 182
- fib_golden_pocket_pullback_mdd2357fa on XAUUSD H4: REJECT at RUNG 0 SANITY -- min_trades: observed 308, required gte 400, short by 92
- fib_golden_pocket_pullback_md3507b8f on XAUUSD H4: REJECT at RUNG 0 SANITY -- min_trades: observed 375, required gte 400, short by 25
- fib_golden_pocket_pullback_m88fd2d1c on XAUUSD H4: REJECT at RUNG 0 SANITY -- min_trades: observed 49, required gte 400, short by 351
- fib_golden_pocket_pullback_me01e2d1a on XAUUSD H4: REJECT at RUNG 0 SANITY -- min_trades: observed 231, required gte 400, short by 169
- fib_golden_pocket_pullback_m76354e7e on XAUUSD H4: REJECT at RUNG 0 SANITY -- min_trades: observed 216, required gte 400, short by 184
- fib_golden_pocket_pullback_mb73e28fd on XAUUSD H4: REJECT at RUNG 0 SANITY -- min_trades: observed 202, required gte 400, short by 198
- ichimoku_kumo_trend_m689694c1 on XAUUSD H4: REJECT at RUNG 0 SANITY -- min_trades: observed 49, required gte 400, short by 351
- ichimoku_kumo_trend_m4195c8bf on XAUUSD H4: REJECT at RUNG 0 SANITY -- min_trades: observed 48, required gte 400, short by 352
- ichimoku_kumo_trend_m8fbcd36c on XAUUSD H4: REJECT at RUNG 0 SANITY -- min_trades: observed 49, required gte 400, short by 351
- ichimoku_kumo_trend_me73acb6c on XAUUSD H4: REJECT at RUNG 0 SANITY -- min_trades: observed 60, required gte 400, short by 340
- ichimoku_kumo_trend_mca231a0f on XAUUSD H4: REJECT at RUNG 0 SANITY -- min_trades: observed 49, required gte 400, short by 351
- ichimoku_kumo_trend_m0bd80cab on XAUUSD H4: REJECT at RUNG 0 SANITY -- min_trades: observed 49, required gte 400, short by 351
- macd_ema_trend_hybrid_ma2bc336b on XAUUSD H4: REJECT at RUNG 0 SANITY -- min_trades: observed 41, required gte 400, short by 359
- macd_ema_trend_hybrid_m03d3ee23 on XAUUSD H4: REJECT at RUNG 0 SANITY -- min_trades: observed 42, required gte 400, short by 358
- macd_ema_trend_hybrid_m4b9a1dcd on XAUUSD H4: REJECT at RUNG 0 SANITY -- min_trades: observed 32, required gte 400, short by 368
- macd_ema_trend_hybrid_mb7889927 on XAUUSD H4: REJECT at RUNG 0 SANITY -- min_trades: observed 41, required gte 400, short by 359
- macd_ema_trend_hybrid_mc45594a0 on XAUUSD H4: REJECT at RUNG 0 SANITY -- min_trades: observed 120, required gte 400, short by 280
- macd_ema_trend_hybrid_m270351c7 on XAUUSD H4: REJECT at RUNG 0 SANITY -- min_trades: observed 37, required gte 400, short by 363

## What was skipped

- donchian_breakout_atr: mutation_rejected -- the parent declares no filters, so none can be removed
- donchian_breakout_atr: mutation_rejected -- incompatible parents: both declare a parameter 'stop_atr_multiple' with different domains, so one document cannot hold both meanings
- fib_golden_pocket_pullback: mutation_rejected -- the parent declares no filters, so none can be removed
- fib_golden_pocket_pullback: mutation_rejected -- incompatible parents: both declare a parameter 'adx_floor' with different domains, so one document cannot hold both meanings
- fib_golden_pocket_pullback: mutation_rejected -- incompatible parents: both declare a parameter 'rsi_ceiling' with different domains, so one document cannot hold both meanings
- ichimoku_kumo_trend: mutation_rejected -- the parent declares no filters, so none can be removed
- ichimoku_kumo_trend: mutation_rejected -- the regime gate's bounds are parameter REFERENCES, not literals, so there is no width to scale here -- the width is already part of the declared sweep and moving it is a reparameterisation
- ichimoku_kumo_trend: mutation_rejected -- incompatible parents: both declare a parameter 'adx_floor' with different domains, so one document cannot hold both meanings
- ichimoku_kumo_trend: mutation_rejected -- incompatible parents: both declare a parameter 'adx_floor' with different domains, so one document cannot hold both meanings
- macd_ema_trend_hybrid: mutation_rejected -- the parent declares no filters, so none can be removed
- macd_ema_trend_hybrid: mutation_rejected -- incompatible parents: both declare a parameter 'stop_atr_multiple' with different domains, so one document cannot hold both meanings
- macd_ema_trend_hybrid: mutation_rejected -- incompatible parents: both declare a parameter 'adx_floor' with different domains, so one document cannot hold both meanings
- rsi_band_mean_reversion: mutation_rejected -- the parent declares no filters, so none can be removed
- rsi_band_mean_reversion: mutation_rejected -- the regime gate's bounds are parameter REFERENCES, not literals, so there is no width to scale here -- the width is already part of the declared sweep and moving it is a reparameterisation
- rsi_band_mean_reversion: mutation_rejected -- incompatible parents: both declare a parameter 'rsi_floor' with different domains, so one document cannot hold both meanings
- donchian_breakout_atr on XAUUSD H4: non_novel -- seed strategy donchian_breakout_atr was tested in exp_dfa627dd, exp_e8cee19f, exp_f731bce7 and exp_5f6cb9f5; min_trades: observed 6, required gte 400, short by 394; walk_forward_efficiency: observed 38.86, required gte 50, short by 11.14; min_trades: observed 6, required gte 25, short by 19; do not repeat without materially different reasoning.
- fib_golden_pocket_pullback on XAUUSD H4: non_novel -- seed strategy fib_golden_pocket_pullback was tested in exp_77dedeb1, exp_cf5888f0, exp_850708a5 and exp_b6af8e36; min_trades: observed 283, required gte 400, short by 117; min_trades: observed 231, required gte 400, short by 169; RUNG 0 SANITY: expectancy -14.7209 at declared defaults is not positive; there is no edge to validate; do not repeat without materially different reasoning.
- ichimoku_kumo_trend on XAUUSD H4: non_novel -- seed strategy ichimoku_kumo_trend was tested in exp_8abd52f1, exp_640d53ee, exp_2261be34 and exp_bea9b4c9; min_trades: observed 71, required gte 400, short by 329; min_trades: observed 49, required gte 400, short by 351; walk_forward_efficiency: observed -151, required gte 50, short by 201; do not repeat without materially different reasoning.
- macd_ema_trend_hybrid on XAUUSD H4: non_novel -- seed strategy macd_ema_trend_hybrid was tested in exp_4e9ec625, exp_35aab4e7, exp_530278cb and exp_a314ffb3; min_trades: observed 72, required gte 400, short by 328; min_trades: observed 41, required gte 400, short by 359; walk_forward_efficiency: observed 42.36, required gte 50, short by 7.639; do not repeat without materially different reasoning.
- rsi_band_mean_reversion on XAUUSD H4: out_of_universe -- rsi_band_mean_reversion declares universe ['EURUSD', 'GBPUSD', 'USDJPY', 'AUDUSD', 'USDCAD', 'USDCHF', 'NZDUSD', 'EURGBP', 'AUDNZD', 'EURCHF'] and timeframes ['H1', 'H4']; running it on XAUUSD H4 would validate a strategy nobody wrote
- donchian_breakout_atr_m4d42994e on XAUUSD H4: out_of_universe -- donchian_breakout_atr_m4d42994e declares universe ['EURUSD', 'GBPUSD', 'USDJPY', 'AUDUSD', 'USDCAD', 'USDCHF', 'NZDUSD'] and timeframes ['H4']; running it on XAUUSD H4 would validate a strategy nobody wrote
- ichimoku_kumo_trend_ma4c92c38 on XAUUSD H4: non_novel -- restrict dealing to new_york: the US session carries the macro releases this family reacts to was tested in exp_8abd52f1, exp_640d53ee, exp_2261be34 and exp_bea9b4c9; min_trades: observed 71, required gte 400, short by 329; min_trades: observed 49, required gte 400, short by 351; walk_forward_efficiency: observed -151, required gte 50, short by 201; do not repeat without materially different reasoning.
- ichimoku_kumo_trend_m67c7da5a on XAUUSD H4: out_of_universe -- ichimoku_kumo_trend_m67c7da5a declares universe ['EURUSD', 'GBPUSD', 'USDJPY', 'AUDUSD', 'USDCAD', 'USDCHF', 'NZDUSD'] and timeframes ['H4']; running it on XAUUSD H4 would validate a strategy nobody wrote
- macd_ema_trend_hybrid_m3728054d on XAUUSD H4: non_novel -- alter targets to scale_1r_3r: bank half at 1R to raise the hit rate, let half run to 3R was tested in exp_4e9ec625, exp_35aab4e7, exp_530278cb and exp_a314ffb3; min_trades: observed 72, required gte 400, short by 328; min_trades: observed 41, required gte 400, short by 359; walk_forward_efficiency: observed 42.36, required gte 50, short by 7.639; do not repeat without materially different reasoning.
- macd_ema_trend_hybrid_m03f1d536 on XAUUSD H4: non_novel -- restrict dealing to overlap: the London/New York overlap is the tightest-spread window of the day was tested in exp_4e9ec625, exp_35aab4e7, exp_530278cb and exp_a314ffb3; min_trades: observed 72, required gte 400, short by 328; min_trades: observed 41, required gte 400, short by 359; walk_forward_efficiency: observed 42.36, required gte 50, short by 7.639; do not repeat without materially different reasoning.
- macd_ema_trend_hybrid_mbd5c916c on XAUUSD H4: out_of_universe -- macd_ema_trend_hybrid_mbd5c916c declares universe ['EURUSD', 'GBPUSD', 'USDJPY', 'AUDUSD', 'USDCAD', 'USDCHF', 'NZDUSD'] and timeframes ['H4']; running it on XAUUSD H4 would validate a strategy nobody wrote
- rsi_band_mean_reversion_md48b52cb on XAUUSD H4: out_of_universe -- rsi_band_mean_reversion_md48b52cb declares universe ['EURUSD', 'GBPUSD', 'USDJPY', 'AUDUSD', 'USDCAD', 'USDCHF', 'NZDUSD', 'EURGBP', 'AUDNZD', 'EURCHF'] and timeframes ['H1', 'H4']; running it on XAUUSD H4 would validate a strategy nobody wrote
- rsi_band_mean_reversion_mdbb3266a on XAUUSD H4: out_of_universe -- rsi_band_mean_reversion_mdbb3266a declares universe ['EURUSD', 'GBPUSD', 'USDJPY', 'AUDUSD', 'USDCAD', 'USDCHF', 'NZDUSD', 'EURGBP', 'AUDNZD', 'EURCHF'] and timeframes ['H1', 'H4']; running it on XAUUSD H4 would validate a strategy nobody wrote
- rsi_band_mean_reversion_m6e0465f0 on XAUUSD H4: out_of_universe -- rsi_band_mean_reversion_m6e0465f0 declares universe ['EURUSD', 'GBPUSD', 'USDJPY', 'AUDUSD', 'USDCAD', 'USDCHF', 'NZDUSD', 'EURGBP', 'AUDNZD', 'EURCHF'] and timeframes ['H1', 'H4']; running it on XAUUSD H4 would validate a strategy nobody wrote
- rsi_band_mean_reversion_me1d6d436 on XAUUSD H4: out_of_universe -- rsi_band_mean_reversion_me1d6d436 declares universe ['EURUSD', 'GBPUSD', 'USDJPY', 'AUDUSD', 'USDCAD', 'USDCHF', 'NZDUSD', 'EURGBP', 'AUDNZD', 'EURCHF'] and timeframes ['H1', 'H4']; running it on XAUUSD H4 would validate a strategy nobody wrote
- rsi_band_mean_reversion_m42a36d28 on XAUUSD H4: out_of_universe -- rsi_band_mean_reversion_m42a36d28 declares universe ['EURUSD', 'GBPUSD', 'USDJPY', 'AUDUSD', 'USDCAD', 'USDCHF', 'NZDUSD', 'EURGBP', 'AUDNZD', 'EURCHF'] and timeframes ['H1', 'H4']; running it on XAUUSD H4 would validate a strategy nobody wrote
- rsi_band_mean_reversion_m7c32d514 on XAUUSD H4: out_of_universe -- rsi_band_mean_reversion_m7c32d514 declares universe ['EURUSD', 'GBPUSD', 'USDJPY', 'AUDUSD', 'USDCAD', 'USDCHF', 'NZDUSD', 'EURGBP', 'AUDNZD', 'EURCHF'] and timeframes ['H1', 'H4']; running it on XAUUSD H4 would validate a strategy nobody wrote
- rsi_band_mean_reversion_m6113daf1 on XAUUSD H4: out_of_universe -- rsi_band_mean_reversion_m6113daf1 declares universe ['EURUSD', 'GBPUSD', 'USDJPY', 'AUDUSD', 'USDCAD', 'USDCHF', 'NZDUSD'] and timeframes ['H4']; running it on XAUUSD H4 would validate a strategy nobody wrote
- rsi_band_mean_reversion_md0807760 on XAUUSD H4: out_of_universe -- rsi_band_mean_reversion_md0807760 declares universe ['EURUSD', 'GBPUSD', 'USDJPY', 'AUDUSD', 'USDCAD', 'USDCHF', 'NZDUSD'] and timeframes ['H4']; running it on XAUUSD H4 would validate a strategy nobody wrote
- rsi_band_mean_reversion_m29180d06 on XAUUSD H4: out_of_universe -- rsi_band_mean_reversion_m29180d06 declares universe ['EURUSD', 'GBPUSD', 'USDJPY', 'AUDUSD', 'USDCAD', 'USDCHF', 'NZDUSD'] and timeframes ['H4']; running it on XAUUSD H4 would validate a strategy nobody wrote

## Mutations refused before any compute

- `remove_filter` on donchian_breakout_atr: the parent declares no filters, so none can be removed
- `combine` on donchian_breakout_atr, macd_ema_trend_hybrid: incompatible parents: both declare a parameter 'stop_atr_multiple' with different domains, so one document cannot hold both meanings
- `remove_filter` on fib_golden_pocket_pullback: the parent declares no filters, so none can be removed
- `combine` on fib_golden_pocket_pullback, ichimoku_kumo_trend: incompatible parents: both declare a parameter 'adx_floor' with different domains, so one document cannot hold both meanings
- `combine` on fib_golden_pocket_pullback, rsi_band_mean_reversion: incompatible parents: both declare a parameter 'rsi_ceiling' with different domains, so one document cannot hold both meanings
- `remove_filter` on ichimoku_kumo_trend: the parent declares no filters, so none can be removed
- `change_regime_gate` on ichimoku_kumo_trend: the regime gate's bounds are parameter REFERENCES, not literals, so there is no width to scale here -- the width is already part of the declared sweep and moving it is a reparameterisation
- `combine` on ichimoku_kumo_trend, fib_golden_pocket_pullback: incompatible parents: both declare a parameter 'adx_floor' with different domains, so one document cannot hold both meanings
- `combine` on ichimoku_kumo_trend, macd_ema_trend_hybrid: incompatible parents: both declare a parameter 'adx_floor' with different domains, so one document cannot hold both meanings
- `remove_filter` on macd_ema_trend_hybrid: the parent declares no filters, so none can be removed
- `combine` on macd_ema_trend_hybrid, donchian_breakout_atr: incompatible parents: both declare a parameter 'stop_atr_multiple' with different domains, so one document cannot hold both meanings
- `combine` on macd_ema_trend_hybrid, ichimoku_kumo_trend: incompatible parents: both declare a parameter 'adx_floor' with different domains, so one document cannot hold both meanings
- `remove_filter` on rsi_band_mean_reversion: the parent declares no filters, so none can be removed
- `change_regime_gate` on rsi_band_mean_reversion: the regime gate's bounds are parameter REFERENCES, not literals, so there is no width to scale here -- the width is already part of the declared sweep and moving it is a reparameterisation
- `combine` on rsi_band_mean_reversion, fib_golden_pocket_pullback: incompatible parents: both declare a parameter 'rsi_floor' with different domains, so one document cannot hold both meanings

## Rejections by rung

- RUNG 0 SANITY: 26
- RUNG 2 WALK_FORWARD: 4

## Holdout

```
{
  "consumptions": [],
  "segments": [
    {
      "dataset_version_id": "ds_3c1473cbf51e5a641ef41cbf",
      "data_start": "2009-03-15T21:00:00+00:00",
      "data_end": "2025-12-31T21:00:00+00:00",
      "holdout_start": "2022-08-22T21:00:00+00:00",
      "holdout_fraction": 0.2,
      "label": "XAUUSD H4",
      "holdout_window": {
        "name": "holdout::ds_3c1473cbf51e5a641ef41cbf",
        "start": "2022-08-22T21:00:00+00:00",
        "end": "2025-12-31T21:00:00+00:00"
      },
      "research_window": {
        "name": "research::ds_3c1473cbf51e5a641ef41cbf",
        "start": "2009-03-15T21:00:00+00:00",
        "end": "2022-08-22T21:00:00+00:00"
      }
    }
  ],
  "n_consumed": 0,
  "unconsumed": true,
  "note": "No candidate reached rung 6, so the holdout segment is untouched and remains available for a future campaign."
}
```

## What this does and does not demonstrate

This campaign carried 30 candidate cell(s) through the validation ladder over 1 instrument(s) (XAUUSD) on 1 timeframe(s) (H4), against gate set v2.0.0-audit. The true trial count for the whole search is 326 (166 planned in this campaign plus 160 already in the ledger for the same dataset version(s)), and that is the number the deflation used -- not the size of any one strategy's own parameter sweep. NOTHING SURVIVED. That is the expected outcome and it is a real result: it says these rule families, on this data, under these costs, do not clear a bar set for a search of this size. It does NOT say the underlying effects do not exist, that another instrument would behave the same way, or that a different cost model would give the same answer.
