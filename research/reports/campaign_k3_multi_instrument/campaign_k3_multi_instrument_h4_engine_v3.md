# Campaign k3_multi_instrument_h4_engine_v3

- actor: `script:run_discovery_campaign`
- created: 2026-09-29T13:13:27.572892+00:00
- gate set: `v2.0.0-audit` (`fe7daa4c71e886b1`)
- datasets: AUDJPY_H4 -> `ds_f4090c2697088bcedb423849`, AUDUSD_H4 -> `ds_ba66154d681cb623c3780947`, DE40_H4 -> `ds_b81ab5fe112100c4094cfb73`, EURGBP_H4 -> `ds_0bafc32b4ed7b8065261e7a4`, EURJPY_H4 -> `ds_c62e333d89200eecf29e9a12`, EURUSD_H4 -> `ds_d8739e54df9f12ecf0ce71c8`, GBPJPY_H4 -> `ds_9f7d5da5c796bd86da1011e8`, GBPUSD_H4 -> `ds_b34988ed16240d36d2b9248c`, NZDUSD_H4 -> `ds_af544dc37dc887fdb98c4e62`, UK100_H4 -> `ds_e5ec1300d41332ab9b36595a`, US500_H4 -> `ds_f6269df5956668a8fd72e6a1`, USDCAD_H4 -> `ds_de6aae350c45f9f5025c7f56`, USDCHF_H4 -> `ds_5a2b99b30031d45d3546c1ca`, USDJPY_H4 -> `ds_300921ee00603a414fc03e98`, XAGUSD_H4 -> `ds_0f1ab110d7cf2003134b2b39`, XAUUSD_H4 -> `ds_af8fc1b3143b3530c3f24ded`

## Trial accounting

- planned in this campaign: **3350**
- already spent on the same bars before this campaign began: **4026**
- **true trial count: 7376**
- ladder evaluations requested: 1130
- engine backtests actually run: 1128 (lower when the deterministic evaluation cache was warm)

The deflation threshold is E[max SR] for a search of 7376 trials. Using the largest cross-trial Sharpe variance observed in this campaign (6.19302e-05, measured at rung 5, where it is applied), that threshold is 0.0298, on the same PER-BAR basis as the candidate Sharpes (nothing here is annualised): a Sharpe at or below it is what a search this size is EXPECTED to produce from strategies with no edge at all. 

## What was tried

| strategy | cell | origin | n trials | external N | verdict | died at | deflated SR | threshold |
|---|---|---|---|---|---|---|---|---|
| `donchian_breakout_atr` | AUDUSD H4 | seed | 8 | 7368 | reject | RUNG 0 SANITY | - | - |
| `donchian_breakout_atr` | DE40 H4 | seed | 8 | 7368 | reject | RUNG 0 SANITY | - | - |
| `donchian_breakout_atr` | EURJPY H4 | seed | 8 | 7368 | reject | RUNG 0 SANITY | - | - |
| `donchian_breakout_atr` | EURUSD H4 | seed | 8 | 7368 | reject | RUNG 0 SANITY | - | - |
| `donchian_breakout_atr` | GBPJPY H4 | seed | 8 | 7368 | reject | RUNG 2 WALK_FORWARD | - | 0.01284 |
| `donchian_breakout_atr` | GBPUSD H4 | seed | 8 | 7368 | reject | RUNG 0 SANITY | - | - |
| `donchian_breakout_atr` | NZDUSD H4 | seed | 8 | 7368 | reject | RUNG 0 SANITY | - | - |
| `donchian_breakout_atr` | US500 H4 | seed | 8 | 7368 | reject | RUNG 0 SANITY | - | - |
| `donchian_breakout_atr` | USDCAD H4 | seed | 8 | 7368 | reject | RUNG 0 SANITY | - | - |
| `donchian_breakout_atr` | USDCHF H4 | seed | 8 | 7368 | reject | RUNG 0 SANITY | - | - |
| `donchian_breakout_atr` | USDJPY H4 | seed | 8 | 7368 | reject | RUNG 0 SANITY | - | - |
| `donchian_breakout_atr` | XAUUSD H4 | seed | 8 | 7368 | reject | RUNG 2 WALK_FORWARD | - | 0.01139 |
| `fib_golden_pocket_pullback` | AUDUSD H4 | seed | 8 | 7368 | reject | RUNG 0 SANITY | - | - |
| `fib_golden_pocket_pullback` | DE40 H4 | seed | 8 | 7368 | reject | RUNG 0 SANITY | - | - |
| `fib_golden_pocket_pullback` | EURJPY H4 | seed | 8 | 7368 | reject | RUNG 0 SANITY | - | - |
| `fib_golden_pocket_pullback` | EURUSD H4 | seed | 8 | 7368 | reject | RUNG 0 SANITY | - | - |
| `fib_golden_pocket_pullback` | GBPJPY H4 | seed | 8 | 7368 | reject | RUNG 0 SANITY | - | - |
| `fib_golden_pocket_pullback` | GBPUSD H4 | seed | 8 | 7368 | reject | RUNG 0 SANITY | - | - |
| `fib_golden_pocket_pullback` | NZDUSD H4 | seed | 8 | 7368 | reject | RUNG 0 SANITY | - | - |
| `fib_golden_pocket_pullback` | US500 H4 | seed | 8 | 7368 | reject | RUNG 0 SANITY | - | - |
| `fib_golden_pocket_pullback` | USDCAD H4 | seed | 8 | 7368 | reject | RUNG 0 SANITY | - | - |
| `fib_golden_pocket_pullback` | USDCHF H4 | seed | 8 | 7368 | reject | RUNG 0 SANITY | - | - |
| `fib_golden_pocket_pullback` | USDJPY H4 | seed | 8 | 7368 | reject | RUNG 0 SANITY | - | - |
| `fib_golden_pocket_pullback` | XAUUSD H4 | seed | 8 | 7368 | reject | RUNG 0 SANITY | - | - |
| `ichimoku_kumo_trend` | AUDUSD H4 | seed | 8 | 7368 | reject | RUNG 0 SANITY | - | - |
| `ichimoku_kumo_trend` | DE40 H4 | seed | 8 | 7368 | reject | RUNG 0 SANITY | - | - |
| `ichimoku_kumo_trend` | EURJPY H4 | seed | 8 | 7368 | reject | RUNG 0 SANITY | - | - |
| `ichimoku_kumo_trend` | EURUSD H4 | seed | 8 | 7368 | reject | RUNG 0 SANITY | - | - |
| `ichimoku_kumo_trend` | GBPJPY H4 | seed | 8 | 7368 | reject | RUNG 0 SANITY | - | - |
| `ichimoku_kumo_trend` | GBPUSD H4 | seed | 8 | 7368 | reject | RUNG 0 SANITY | - | - |
| `ichimoku_kumo_trend` | NZDUSD H4 | seed | 8 | 7368 | reject | RUNG 0 SANITY | - | - |
| `ichimoku_kumo_trend` | US500 H4 | seed | 8 | 7368 | reject | RUNG 0 SANITY | - | - |
| `ichimoku_kumo_trend` | USDCAD H4 | seed | 8 | 7368 | reject | RUNG 0 SANITY | - | - |
| `ichimoku_kumo_trend` | USDCHF H4 | seed | 8 | 7368 | reject | RUNG 0 SANITY | - | - |
| `ichimoku_kumo_trend` | USDJPY H4 | seed | 8 | 7368 | reject | RUNG 0 SANITY | - | - |
| `ichimoku_kumo_trend` | XAUUSD H4 | seed | 8 | 7368 | reject | RUNG 0 SANITY | - | - |
| `macd_ema_trend_hybrid` | AUDUSD H4 | seed | 8 | 7368 | reject | RUNG 0 SANITY | - | - |
| `macd_ema_trend_hybrid` | DE40 H4 | seed | 8 | 7368 | reject | RUNG 0 SANITY | - | - |
| `macd_ema_trend_hybrid` | EURJPY H4 | seed | 8 | 7368 | reject | RUNG 0 SANITY | - | - |
| `macd_ema_trend_hybrid` | EURUSD H4 | seed | 8 | 7368 | reject | RUNG 0 SANITY | - | - |
| `macd_ema_trend_hybrid` | GBPJPY H4 | seed | 8 | 7368 | reject | RUNG 0 SANITY | - | - |
| `macd_ema_trend_hybrid` | GBPUSD H4 | seed | 8 | 7368 | reject | RUNG 0 SANITY | - | - |
| `macd_ema_trend_hybrid` | NZDUSD H4 | seed | 8 | 7368 | reject | RUNG 0 SANITY | - | - |
| `macd_ema_trend_hybrid` | US500 H4 | seed | 8 | 7368 | reject | RUNG 0 SANITY | - | - |
| `macd_ema_trend_hybrid` | USDCAD H4 | seed | 8 | 7368 | reject | RUNG 0 SANITY | - | - |
| `macd_ema_trend_hybrid` | USDCHF H4 | seed | 8 | 7368 | reject | RUNG 0 SANITY | - | - |
| `macd_ema_trend_hybrid` | USDJPY H4 | seed | 8 | 7368 | reject | RUNG 0 SANITY | - | - |
| `macd_ema_trend_hybrid` | XAUUSD H4 | seed | 8 | 7368 | reject | RUNG 0 SANITY | - | - |
| `rsi_band_mean_reversion` | AUDUSD H4 | seed | 8 | 7368 | reject | RUNG 0 SANITY | - | - |
| `rsi_band_mean_reversion` | EURGBP H4 | seed | 8 | 7368 | reject | RUNG 0 SANITY | - | - |
| `rsi_band_mean_reversion` | EURUSD H4 | seed | 8 | 7368 | reject | RUNG 0 SANITY | - | - |
| `rsi_band_mean_reversion` | GBPUSD H4 | seed | 8 | 7368 | reject | RUNG 0 SANITY | - | - |
| `rsi_band_mean_reversion` | NZDUSD H4 | seed | 8 | 7368 | reject | RUNG 0 SANITY | - | - |
| `rsi_band_mean_reversion` | USDCAD H4 | seed | 8 | 7368 | reject | RUNG 0 SANITY | - | - |
| `rsi_band_mean_reversion` | USDCHF H4 | seed | 8 | 7368 | reject | RUNG 0 SANITY | - | - |
| `rsi_band_mean_reversion` | USDJPY H4 | seed | 8 | 7368 | reject | RUNG 0 SANITY | - | - |
| `donchian_breakout_atr_m3ecf77ae` | AUDUSD H4 | add_filter | 8 | 7368 | reject | RUNG 0 SANITY | - | - |
| `donchian_breakout_atr_m3ecf77ae` | DE40 H4 | add_filter | 8 | 7368 | reject | RUNG 0 SANITY | - | - |
| `donchian_breakout_atr_m3ecf77ae` | EURJPY H4 | add_filter | 8 | 7368 | reject | RUNG 0 SANITY | - | - |
| `donchian_breakout_atr_m3ecf77ae` | EURUSD H4 | add_filter | 8 | 7368 | reject | RUNG 0 SANITY | - | - |
| `donchian_breakout_atr_m3ecf77ae` | GBPJPY H4 | add_filter | 8 | 7368 | reject | RUNG 2 WALK_FORWARD | - | 0.01284 |
| `donchian_breakout_atr_m3ecf77ae` | GBPUSD H4 | add_filter | 8 | 7368 | reject | RUNG 0 SANITY | - | - |
| `donchian_breakout_atr_m3ecf77ae` | NZDUSD H4 | add_filter | 8 | 7368 | reject | RUNG 0 SANITY | - | - |
| `donchian_breakout_atr_m3ecf77ae` | US500 H4 | add_filter | 8 | 7368 | reject | RUNG 0 SANITY | - | - |
| `donchian_breakout_atr_m3ecf77ae` | USDCAD H4 | add_filter | 8 | 7368 | reject | RUNG 0 SANITY | - | - |
| `donchian_breakout_atr_m3ecf77ae` | USDCHF H4 | add_filter | 8 | 7368 | reject | RUNG 0 SANITY | - | - |
| `donchian_breakout_atr_m3ecf77ae` | USDJPY H4 | add_filter | 8 | 7368 | reject | RUNG 0 SANITY | - | - |
| `donchian_breakout_atr_m3ecf77ae` | XAUUSD H4 | add_filter | 8 | 7368 | reject | RUNG 2 WALK_FORWARD | - | 0.01139 |
| `donchian_breakout_atr_mbdfe4742` | AUDUSD H4 | alter_stop_model | 4 | 7372 | reject | RUNG 0 SANITY | - | - |
| `donchian_breakout_atr_mbdfe4742` | DE40 H4 | alter_stop_model | 4 | 7372 | reject | RUNG 2 WALK_FORWARD | - | 0.03756 |
| `donchian_breakout_atr_mbdfe4742` | EURJPY H4 | alter_stop_model | 4 | 7372 | reject | RUNG 2 WALK_FORWARD | - | 0.0232 |
| `donchian_breakout_atr_mbdfe4742` | EURUSD H4 | alter_stop_model | 4 | 7372 | reject | RUNG 0 SANITY | - | - |
| `donchian_breakout_atr_mbdfe4742` | GBPJPY H4 | alter_stop_model | 4 | 7372 | reject | RUNG 2 WALK_FORWARD | - | 0.006071 |
| `donchian_breakout_atr_mbdfe4742` | GBPUSD H4 | alter_stop_model | 4 | 7372 | reject | RUNG 0 SANITY | - | - |
| `donchian_breakout_atr_mbdfe4742` | NZDUSD H4 | alter_stop_model | 4 | 7372 | reject | RUNG 0 SANITY | - | - |
| `donchian_breakout_atr_mbdfe4742` | US500 H4 | alter_stop_model | 4 | 7372 | reject | RUNG 0 SANITY | - | - |
| `donchian_breakout_atr_mbdfe4742` | USDCAD H4 | alter_stop_model | 4 | 7372 | reject | RUNG 0 SANITY | - | - |
| `donchian_breakout_atr_mbdfe4742` | USDCHF H4 | alter_stop_model | 4 | 7372 | reject | RUNG 0 SANITY | - | - |
| `donchian_breakout_atr_mbdfe4742` | USDJPY H4 | alter_stop_model | 4 | 7372 | reject | RUNG 0 SANITY | - | - |
| `donchian_breakout_atr_mbdfe4742` | XAUUSD H4 | alter_stop_model | 4 | 7372 | reject | RUNG 0 SANITY | - | - |
| `donchian_breakout_atr_m3c934a3b` | AUDUSD H4 | alter_target_model | 8 | 7368 | reject | RUNG 0 SANITY | - | - |
| `donchian_breakout_atr_m3c934a3b` | DE40 H4 | alter_target_model | 8 | 7368 | reject | RUNG 0 SANITY | - | - |
| `donchian_breakout_atr_m3c934a3b` | EURJPY H4 | alter_target_model | 8 | 7368 | reject | RUNG 0 SANITY | - | - |
| `donchian_breakout_atr_m3c934a3b` | EURUSD H4 | alter_target_model | 8 | 7368 | reject | RUNG 0 SANITY | - | - |
| `donchian_breakout_atr_m3c934a3b` | GBPJPY H4 | alter_target_model | 8 | 7368 | reject | RUNG 2 WALK_FORWARD | - | 0.02703 |
| `donchian_breakout_atr_m3c934a3b` | GBPUSD H4 | alter_target_model | 8 | 7368 | reject | RUNG 0 SANITY | - | - |
| `donchian_breakout_atr_m3c934a3b` | NZDUSD H4 | alter_target_model | 8 | 7368 | reject | RUNG 0 SANITY | - | - |
| `donchian_breakout_atr_m3c934a3b` | US500 H4 | alter_target_model | 8 | 7368 | reject | RUNG 0 SANITY | - | - |
| `donchian_breakout_atr_m3c934a3b` | USDCAD H4 | alter_target_model | 8 | 7368 | reject | RUNG 0 SANITY | - | - |
| `donchian_breakout_atr_m3c934a3b` | USDCHF H4 | alter_target_model | 8 | 7368 | reject | RUNG 0 SANITY | - | - |
| `donchian_breakout_atr_m3c934a3b` | USDJPY H4 | alter_target_model | 8 | 7368 | reject | RUNG 0 SANITY | - | - |
| `donchian_breakout_atr_m3c934a3b` | XAUUSD H4 | alter_target_model | 8 | 7368 | reject | RUNG 0 SANITY | - | - |
| `donchian_breakout_atr_m76dbb4f7` | AUDUSD H4 | change_regime_gate | 8 | 7368 | reject | RUNG 0 SANITY | - | - |
| `donchian_breakout_atr_m76dbb4f7` | DE40 H4 | change_regime_gate | 8 | 7368 | reject | RUNG 0 SANITY | - | - |
| `donchian_breakout_atr_m76dbb4f7` | EURJPY H4 | change_regime_gate | 8 | 7368 | reject | RUNG 0 SANITY | - | - |
| `donchian_breakout_atr_m76dbb4f7` | EURUSD H4 | change_regime_gate | 8 | 7368 | reject | RUNG 0 SANITY | - | - |
| `donchian_breakout_atr_m76dbb4f7` | GBPJPY H4 | change_regime_gate | 8 | 7368 | reject | RUNG 0 SANITY | - | - |
| `donchian_breakout_atr_m76dbb4f7` | GBPUSD H4 | change_regime_gate | 8 | 7368 | reject | RUNG 0 SANITY | - | - |
| `donchian_breakout_atr_m76dbb4f7` | NZDUSD H4 | change_regime_gate | 8 | 7368 | reject | RUNG 0 SANITY | - | - |
| `donchian_breakout_atr_m76dbb4f7` | US500 H4 | change_regime_gate | 8 | 7368 | reject | RUNG 0 SANITY | - | - |
| `donchian_breakout_atr_m76dbb4f7` | USDCAD H4 | change_regime_gate | 8 | 7368 | reject | RUNG 0 SANITY | - | - |
| `donchian_breakout_atr_m76dbb4f7` | USDCHF H4 | change_regime_gate | 8 | 7368 | reject | RUNG 0 SANITY | - | - |
| `donchian_breakout_atr_m76dbb4f7` | USDJPY H4 | change_regime_gate | 8 | 7368 | reject | RUNG 0 SANITY | - | - |
| `donchian_breakout_atr_m76dbb4f7` | XAUUSD H4 | change_regime_gate | 8 | 7368 | reject | RUNG 2 WALK_FORWARD | - | 0.01808 |
| `donchian_breakout_atr_m427fba0e` | AUDUSD H4 | change_session_restriction | 8 | 7368 | reject | RUNG 0 SANITY | - | - |
| `donchian_breakout_atr_m427fba0e` | DE40 H4 | change_session_restriction | 8 | 7368 | reject | RUNG 0 SANITY | - | - |
| `donchian_breakout_atr_m427fba0e` | EURJPY H4 | change_session_restriction | 8 | 7368 | reject | RUNG 0 SANITY | - | - |
| `donchian_breakout_atr_m427fba0e` | EURUSD H4 | change_session_restriction | 8 | 7368 | reject | RUNG 0 SANITY | - | - |
| `donchian_breakout_atr_m427fba0e` | GBPJPY H4 | change_session_restriction | 8 | 7368 | reject | RUNG 0 SANITY | - | - |
| `donchian_breakout_atr_m427fba0e` | GBPUSD H4 | change_session_restriction | 8 | 7368 | reject | RUNG 0 SANITY | - | - |
| `donchian_breakout_atr_m427fba0e` | NZDUSD H4 | change_session_restriction | 8 | 7368 | reject | RUNG 0 SANITY | - | - |
| `donchian_breakout_atr_m427fba0e` | US500 H4 | change_session_restriction | 8 | 7368 | reject | RUNG 0 SANITY | - | - |
| `donchian_breakout_atr_m427fba0e` | USDCAD H4 | change_session_restriction | 8 | 7368 | reject | RUNG 0 SANITY | - | - |
| `donchian_breakout_atr_m427fba0e` | USDCHF H4 | change_session_restriction | 8 | 7368 | reject | RUNG 0 SANITY | - | - |
| `donchian_breakout_atr_m427fba0e` | USDJPY H4 | change_session_restriction | 8 | 7368 | reject | RUNG 0 SANITY | - | - |
| `donchian_breakout_atr_m427fba0e` | XAUUSD H4 | change_session_restriction | 8 | 7368 | reject | RUNG 4 ROBUSTNESS | - | 0.02993 |
| `donchian_breakout_atr_m79f18824` | AUDUSD H4 | change_confirmation_rule | 8 | 7368 | reject | RUNG 0 SANITY | - | - |
| `donchian_breakout_atr_m79f18824` | DE40 H4 | change_confirmation_rule | 8 | 7368 | reject | RUNG 0 SANITY | - | - |
| `donchian_breakout_atr_m79f18824` | EURJPY H4 | change_confirmation_rule | 8 | 7368 | reject | RUNG 0 SANITY | - | - |
| `donchian_breakout_atr_m79f18824` | EURUSD H4 | change_confirmation_rule | 8 | 7368 | reject | RUNG 0 SANITY | - | - |
| `donchian_breakout_atr_m79f18824` | GBPJPY H4 | change_confirmation_rule | 8 | 7368 | reject | RUNG 2 WALK_FORWARD | - | 0.01272 |
| `donchian_breakout_atr_m79f18824` | GBPUSD H4 | change_confirmation_rule | 8 | 7368 | reject | RUNG 0 SANITY | - | - |
| `donchian_breakout_atr_m79f18824` | NZDUSD H4 | change_confirmation_rule | 8 | 7368 | reject | RUNG 0 SANITY | - | - |
| `donchian_breakout_atr_m79f18824` | US500 H4 | change_confirmation_rule | 8 | 7368 | reject | RUNG 0 SANITY | - | - |
| `donchian_breakout_atr_m79f18824` | USDCAD H4 | change_confirmation_rule | 8 | 7368 | reject | RUNG 0 SANITY | - | - |
| `donchian_breakout_atr_m79f18824` | USDCHF H4 | change_confirmation_rule | 8 | 7368 | reject | RUNG 0 SANITY | - | - |
| `donchian_breakout_atr_m79f18824` | USDJPY H4 | change_confirmation_rule | 8 | 7368 | reject | RUNG 0 SANITY | - | - |
| `donchian_breakout_atr_m79f18824` | XAUUSD H4 | change_confirmation_rule | 8 | 7368 | reject | RUNG 2 WALK_FORWARD | - | 0.01154 |
| `donchian_breakout_atr_mf2553d34` | AUDUSD H4 | simplify | 8 | 7368 | reject | RUNG 0 SANITY | - | - |
| `donchian_breakout_atr_mf2553d34` | DE40 H4 | simplify | 8 | 7368 | reject | RUNG 0 SANITY | - | - |
| `donchian_breakout_atr_mf2553d34` | EURJPY H4 | simplify | 8 | 7368 | reject | RUNG 0 SANITY | - | - |
| `donchian_breakout_atr_mf2553d34` | EURUSD H4 | simplify | 8 | 7368 | reject | RUNG 0 SANITY | - | - |
| `donchian_breakout_atr_mf2553d34` | GBPJPY H4 | simplify | 8 | 7368 | reject | RUNG 2 WALK_FORWARD | - | 0.01397 |
| `donchian_breakout_atr_mf2553d34` | GBPUSD H4 | simplify | 8 | 7368 | reject | RUNG 0 SANITY | - | - |
| `donchian_breakout_atr_mf2553d34` | NZDUSD H4 | simplify | 8 | 7368 | reject | RUNG 0 SANITY | - | - |
| `donchian_breakout_atr_mf2553d34` | US500 H4 | simplify | 8 | 7368 | reject | RUNG 0 SANITY | - | - |
| `donchian_breakout_atr_mf2553d34` | USDCAD H4 | simplify | 8 | 7368 | reject | RUNG 0 SANITY | - | - |
| `donchian_breakout_atr_mf2553d34` | USDCHF H4 | simplify | 8 | 7368 | reject | RUNG 0 SANITY | - | - |
| `donchian_breakout_atr_mf2553d34` | USDJPY H4 | simplify | 8 | 7368 | reject | RUNG 0 SANITY | - | - |
| `donchian_breakout_atr_mf2553d34` | XAUUSD H4 | simplify | 8 | 7368 | reject | RUNG 0 SANITY | - | - |
| `donchian_breakout_atr_m30945c80` | AUDUSD H4 | combine | 2 | 7374 | reject | RUNG 0 SANITY | - | - |
| `donchian_breakout_atr_m30945c80` | DE40 H4 | combine | 2 | 7374 | reject | RUNG 0 SANITY | - | - |
| `donchian_breakout_atr_m30945c80` | EURJPY H4 | combine | 2 | 7374 | reject | RUNG 0 SANITY | - | - |
| `donchian_breakout_atr_m30945c80` | EURUSD H4 | combine | 2 | 7374 | reject | RUNG 1 IN_SAMPLE_SCREEN | - | 0.0224 |
| `donchian_breakout_atr_m30945c80` | GBPJPY H4 | combine | 2 | 7374 | reject | RUNG 1 IN_SAMPLE_SCREEN | - | 0.002259 |
| `donchian_breakout_atr_m30945c80` | GBPUSD H4 | combine | 2 | 7374 | reject | RUNG 0 SANITY | - | - |
| `donchian_breakout_atr_m30945c80` | NZDUSD H4 | combine | 2 | 7374 | reject | RUNG 0 SANITY | - | - |
| `donchian_breakout_atr_m30945c80` | US500 H4 | combine | 2 | 7374 | reject | RUNG 0 SANITY | - | - |
| `donchian_breakout_atr_m30945c80` | USDCAD H4 | combine | 2 | 7374 | reject | RUNG 0 SANITY | - | - |
| `donchian_breakout_atr_m30945c80` | USDCHF H4 | combine | 2 | 7374 | reject | RUNG 0 SANITY | - | - |
| `donchian_breakout_atr_m30945c80` | USDJPY H4 | combine | 2 | 7374 | reject | RUNG 0 SANITY | - | - |
| `donchian_breakout_atr_m30945c80` | XAUUSD H4 | combine | 2 | 7374 | reject | RUNG 0 SANITY | - | - |
| `donchian_breakout_atr_m217dad55` | AUDUSD H4 | combine | 2 | 7374 | reject | RUNG 0 SANITY | - | - |
| `donchian_breakout_atr_m217dad55` | DE40 H4 | combine | 2 | 7374 | reject | RUNG 0 SANITY | - | - |
| `donchian_breakout_atr_m217dad55` | EURJPY H4 | combine | 2 | 7374 | reject | RUNG 0 SANITY | - | - |
| `donchian_breakout_atr_m217dad55` | EURUSD H4 | combine | 2 | 7374 | reject | RUNG 0 SANITY | - | - |
| `donchian_breakout_atr_m217dad55` | GBPJPY H4 | combine | 2 | 7374 | reject | RUNG 5 DEFLATION | 0.001595 | 0.02979 |
| `donchian_breakout_atr_m217dad55` | GBPUSD H4 | combine | 2 | 7374 | reject | RUNG 0 SANITY | - | - |
| `donchian_breakout_atr_m217dad55` | NZDUSD H4 | combine | 2 | 7374 | reject | RUNG 0 SANITY | - | - |
| `donchian_breakout_atr_m217dad55` | US500 H4 | combine | 2 | 7374 | reject | RUNG 0 SANITY | - | - |
| `donchian_breakout_atr_m217dad55` | USDCAD H4 | combine | 2 | 7374 | reject | RUNG 0 SANITY | - | - |
| `donchian_breakout_atr_m217dad55` | USDCHF H4 | combine | 2 | 7374 | reject | RUNG 0 SANITY | - | - |
| `donchian_breakout_atr_m217dad55` | USDJPY H4 | combine | 2 | 7374 | reject | RUNG 0 SANITY | - | - |
| `donchian_breakout_atr_m217dad55` | XAUUSD H4 | combine | 2 | 7374 | reject | RUNG 0 SANITY | - | - |
| `donchian_breakout_atr_m4d42994e` | AUDUSD H4 | combine | 2 | 7374 | reject | RUNG 0 SANITY | - | - |
| `donchian_breakout_atr_m4d42994e` | EURUSD H4 | combine | 2 | 7374 | reject | RUNG 0 SANITY | - | - |
| `donchian_breakout_atr_m4d42994e` | GBPUSD H4 | combine | 2 | 7374 | reject | RUNG 0 SANITY | - | - |
| `donchian_breakout_atr_m4d42994e` | NZDUSD H4 | combine | 2 | 7374 | reject | RUNG 0 SANITY | - | - |
| `donchian_breakout_atr_m4d42994e` | USDCAD H4 | combine | 2 | 7374 | reject | RUNG 0 SANITY | - | - |
| `donchian_breakout_atr_m4d42994e` | USDCHF H4 | combine | 2 | 7374 | reject | RUNG 0 SANITY | - | - |
| `donchian_breakout_atr_m4d42994e` | USDJPY H4 | combine | 2 | 7374 | reject | RUNG 0 SANITY | - | - |
| `fib_golden_pocket_pullback_ma8f9f32b` | AUDUSD H4 | add_filter | 4 | 7372 | reject | RUNG 0 SANITY | - | - |
| `fib_golden_pocket_pullback_ma8f9f32b` | DE40 H4 | add_filter | 4 | 7372 | reject | RUNG 0 SANITY | - | - |
| `fib_golden_pocket_pullback_ma8f9f32b` | EURJPY H4 | add_filter | 4 | 7372 | reject | RUNG 0 SANITY | - | - |
| `fib_golden_pocket_pullback_ma8f9f32b` | EURUSD H4 | add_filter | 4 | 7372 | reject | RUNG 0 SANITY | - | - |
| `fib_golden_pocket_pullback_ma8f9f32b` | GBPJPY H4 | add_filter | 4 | 7372 | reject | RUNG 0 SANITY | - | - |
| `fib_golden_pocket_pullback_ma8f9f32b` | GBPUSD H4 | add_filter | 4 | 7372 | reject | RUNG 0 SANITY | - | - |
| `fib_golden_pocket_pullback_ma8f9f32b` | NZDUSD H4 | add_filter | 4 | 7372 | reject | RUNG 0 SANITY | - | - |
| `fib_golden_pocket_pullback_ma8f9f32b` | US500 H4 | add_filter | 4 | 7372 | reject | RUNG 0 SANITY | - | - |
| `fib_golden_pocket_pullback_ma8f9f32b` | USDCAD H4 | add_filter | 4 | 7372 | reject | RUNG 0 SANITY | - | - |
| `fib_golden_pocket_pullback_ma8f9f32b` | USDCHF H4 | add_filter | 4 | 7372 | reject | RUNG 0 SANITY | - | - |
| `fib_golden_pocket_pullback_ma8f9f32b` | USDJPY H4 | add_filter | 4 | 7372 | reject | RUNG 0 SANITY | - | - |
| `fib_golden_pocket_pullback_ma8f9f32b` | XAUUSD H4 | add_filter | 4 | 7372 | reject | RUNG 0 SANITY | - | - |
| `fib_golden_pocket_pullback_md15c64ac` | AUDUSD H4 | alter_stop_model | 2 | 7374 | reject | RUNG 0 SANITY | - | - |
| `fib_golden_pocket_pullback_md15c64ac` | DE40 H4 | alter_stop_model | 2 | 7374 | reject | RUNG 0 SANITY | - | - |
| `fib_golden_pocket_pullback_md15c64ac` | EURJPY H4 | alter_stop_model | 2 | 7374 | reject | RUNG 0 SANITY | - | - |
| `fib_golden_pocket_pullback_md15c64ac` | EURUSD H4 | alter_stop_model | 2 | 7374 | reject | RUNG 0 SANITY | - | - |
| `fib_golden_pocket_pullback_md15c64ac` | GBPJPY H4 | alter_stop_model | 2 | 7374 | reject | RUNG 0 SANITY | - | - |
| `fib_golden_pocket_pullback_md15c64ac` | GBPUSD H4 | alter_stop_model | 2 | 7374 | reject | RUNG 0 SANITY | - | - |
| `fib_golden_pocket_pullback_md15c64ac` | NZDUSD H4 | alter_stop_model | 2 | 7374 | reject | RUNG 0 SANITY | - | - |
| `fib_golden_pocket_pullback_md15c64ac` | US500 H4 | alter_stop_model | 2 | 7374 | reject | RUNG 0 SANITY | - | - |
| `fib_golden_pocket_pullback_md15c64ac` | USDCAD H4 | alter_stop_model | 2 | 7374 | reject | RUNG 0 SANITY | - | - |
| `fib_golden_pocket_pullback_md15c64ac` | USDCHF H4 | alter_stop_model | 2 | 7374 | reject | RUNG 0 SANITY | - | - |
| `fib_golden_pocket_pullback_md15c64ac` | USDJPY H4 | alter_stop_model | 2 | 7374 | reject | RUNG 0 SANITY | - | - |
| `fib_golden_pocket_pullback_md15c64ac` | XAUUSD H4 | alter_stop_model | 2 | 7374 | reject | RUNG 0 SANITY | - | - |
| `fib_golden_pocket_pullback_mb82cde01` | AUDUSD H4 | alter_target_model | 4 | 7372 | reject | RUNG 0 SANITY | - | - |
| `fib_golden_pocket_pullback_mb82cde01` | DE40 H4 | alter_target_model | 4 | 7372 | reject | RUNG 0 SANITY | - | - |
| `fib_golden_pocket_pullback_mb82cde01` | EURJPY H4 | alter_target_model | 4 | 7372 | reject | RUNG 0 SANITY | - | - |
| `fib_golden_pocket_pullback_mb82cde01` | EURUSD H4 | alter_target_model | 4 | 7372 | reject | RUNG 0 SANITY | - | - |
| `fib_golden_pocket_pullback_mb82cde01` | GBPJPY H4 | alter_target_model | 4 | 7372 | reject | RUNG 0 SANITY | - | - |
| `fib_golden_pocket_pullback_mb82cde01` | GBPUSD H4 | alter_target_model | 4 | 7372 | reject | RUNG 0 SANITY | - | - |
| `fib_golden_pocket_pullback_mb82cde01` | NZDUSD H4 | alter_target_model | 4 | 7372 | reject | RUNG 0 SANITY | - | - |
| `fib_golden_pocket_pullback_mb82cde01` | US500 H4 | alter_target_model | 4 | 7372 | reject | RUNG 0 SANITY | - | - |
| `fib_golden_pocket_pullback_mb82cde01` | USDCAD H4 | alter_target_model | 4 | 7372 | reject | RUNG 0 SANITY | - | - |
| `fib_golden_pocket_pullback_mb82cde01` | USDCHF H4 | alter_target_model | 4 | 7372 | reject | RUNG 0 SANITY | - | - |
| `fib_golden_pocket_pullback_mb82cde01` | USDJPY H4 | alter_target_model | 4 | 7372 | reject | RUNG 0 SANITY | - | - |
| `fib_golden_pocket_pullback_mb82cde01` | XAUUSD H4 | alter_target_model | 4 | 7372 | reject | RUNG 0 SANITY | - | - |
| `fib_golden_pocket_pullback_mdd2357fa` | AUDUSD H4 | change_regime_gate | 4 | 7372 | reject | RUNG 0 SANITY | - | - |
| `fib_golden_pocket_pullback_mdd2357fa` | DE40 H4 | change_regime_gate | 4 | 7372 | reject | RUNG 0 SANITY | - | - |
| `fib_golden_pocket_pullback_mdd2357fa` | EURJPY H4 | change_regime_gate | 4 | 7372 | reject | RUNG 0 SANITY | - | - |
| `fib_golden_pocket_pullback_mdd2357fa` | EURUSD H4 | change_regime_gate | 4 | 7372 | reject | RUNG 0 SANITY | - | - |
| `fib_golden_pocket_pullback_mdd2357fa` | GBPJPY H4 | change_regime_gate | 4 | 7372 | reject | RUNG 0 SANITY | - | - |
| `fib_golden_pocket_pullback_mdd2357fa` | GBPUSD H4 | change_regime_gate | 4 | 7372 | reject | RUNG 0 SANITY | - | - |
| `fib_golden_pocket_pullback_mdd2357fa` | NZDUSD H4 | change_regime_gate | 4 | 7372 | reject | RUNG 0 SANITY | - | - |
| `fib_golden_pocket_pullback_mdd2357fa` | US500 H4 | change_regime_gate | 4 | 7372 | reject | RUNG 0 SANITY | - | - |
| `fib_golden_pocket_pullback_mdd2357fa` | USDCAD H4 | change_regime_gate | 4 | 7372 | reject | RUNG 0 SANITY | - | - |
| `fib_golden_pocket_pullback_mdd2357fa` | USDCHF H4 | change_regime_gate | 4 | 7372 | reject | RUNG 0 SANITY | - | - |
| `fib_golden_pocket_pullback_mdd2357fa` | USDJPY H4 | change_regime_gate | 4 | 7372 | reject | RUNG 0 SANITY | - | - |
| `fib_golden_pocket_pullback_mdd2357fa` | XAUUSD H4 | change_regime_gate | 4 | 7372 | reject | RUNG 0 SANITY | - | - |
| `fib_golden_pocket_pullback_md3507b8f` | AUDUSD H4 | change_session_restriction | 4 | 7372 | reject | RUNG 0 SANITY | - | - |
| `fib_golden_pocket_pullback_md3507b8f` | DE40 H4 | change_session_restriction | 4 | 7372 | reject | RUNG 0 SANITY | - | - |
| `fib_golden_pocket_pullback_md3507b8f` | EURJPY H4 | change_session_restriction | 4 | 7372 | reject | RUNG 0 SANITY | - | - |
| `fib_golden_pocket_pullback_md3507b8f` | EURUSD H4 | change_session_restriction | 4 | 7372 | reject | RUNG 0 SANITY | - | - |
| `fib_golden_pocket_pullback_md3507b8f` | GBPJPY H4 | change_session_restriction | 4 | 7372 | reject | RUNG 0 SANITY | - | - |
| `fib_golden_pocket_pullback_md3507b8f` | GBPUSD H4 | change_session_restriction | 4 | 7372 | reject | RUNG 0 SANITY | - | - |
| `fib_golden_pocket_pullback_md3507b8f` | NZDUSD H4 | change_session_restriction | 4 | 7372 | reject | RUNG 0 SANITY | - | - |
| `fib_golden_pocket_pullback_md3507b8f` | US500 H4 | change_session_restriction | 4 | 7372 | reject | RUNG 0 SANITY | - | - |
| `fib_golden_pocket_pullback_md3507b8f` | USDCAD H4 | change_session_restriction | 4 | 7372 | reject | RUNG 0 SANITY | - | - |
| `fib_golden_pocket_pullback_md3507b8f` | USDCHF H4 | change_session_restriction | 4 | 7372 | reject | RUNG 0 SANITY | - | - |
| `fib_golden_pocket_pullback_md3507b8f` | USDJPY H4 | change_session_restriction | 4 | 7372 | reject | RUNG 0 SANITY | - | - |
| `fib_golden_pocket_pullback_md3507b8f` | XAUUSD H4 | change_session_restriction | 4 | 7372 | reject | RUNG 0 SANITY | - | - |
| `fib_golden_pocket_pullback_m88fd2d1c` | AUDUSD H4 | change_confirmation_rule | 4 | 7372 | reject | RUNG 0 SANITY | - | - |
| `fib_golden_pocket_pullback_m88fd2d1c` | DE40 H4 | change_confirmation_rule | 4 | 7372 | reject | RUNG 0 SANITY | - | - |
| `fib_golden_pocket_pullback_m88fd2d1c` | EURJPY H4 | change_confirmation_rule | 4 | 7372 | reject | RUNG 0 SANITY | - | - |
| `fib_golden_pocket_pullback_m88fd2d1c` | EURUSD H4 | change_confirmation_rule | 4 | 7372 | reject | RUNG 0 SANITY | - | - |
| `fib_golden_pocket_pullback_m88fd2d1c` | GBPJPY H4 | change_confirmation_rule | 4 | 7372 | reject | RUNG 0 SANITY | - | - |
| `fib_golden_pocket_pullback_m88fd2d1c` | GBPUSD H4 | change_confirmation_rule | 4 | 7372 | reject | RUNG 0 SANITY | - | - |
| `fib_golden_pocket_pullback_m88fd2d1c` | NZDUSD H4 | change_confirmation_rule | 4 | 7372 | reject | RUNG 0 SANITY | - | - |
| `fib_golden_pocket_pullback_m88fd2d1c` | US500 H4 | change_confirmation_rule | 4 | 7372 | reject | RUNG 0 SANITY | - | - |
| `fib_golden_pocket_pullback_m88fd2d1c` | USDCAD H4 | change_confirmation_rule | 4 | 7372 | reject | RUNG 0 SANITY | - | - |
| `fib_golden_pocket_pullback_m88fd2d1c` | USDCHF H4 | change_confirmation_rule | 4 | 7372 | reject | RUNG 0 SANITY | - | - |
| `fib_golden_pocket_pullback_m88fd2d1c` | USDJPY H4 | change_confirmation_rule | 4 | 7372 | reject | RUNG 0 SANITY | - | - |
| `fib_golden_pocket_pullback_m88fd2d1c` | XAUUSD H4 | change_confirmation_rule | 4 | 7372 | reject | RUNG 0 SANITY | - | - |
| `fib_golden_pocket_pullback_me01e2d1a` | AUDUSD H4 | simplify | 4 | 7372 | reject | RUNG 0 SANITY | - | - |
| `fib_golden_pocket_pullback_me01e2d1a` | DE40 H4 | simplify | 4 | 7372 | reject | RUNG 0 SANITY | - | - |
| `fib_golden_pocket_pullback_me01e2d1a` | EURJPY H4 | simplify | 4 | 7372 | reject | RUNG 0 SANITY | - | - |
| `fib_golden_pocket_pullback_me01e2d1a` | EURUSD H4 | simplify | 4 | 7372 | reject | RUNG 0 SANITY | - | - |
| `fib_golden_pocket_pullback_me01e2d1a` | GBPJPY H4 | simplify | 4 | 7372 | reject | RUNG 0 SANITY | - | - |
| `fib_golden_pocket_pullback_me01e2d1a` | GBPUSD H4 | simplify | 4 | 7372 | reject | RUNG 0 SANITY | - | - |
| `fib_golden_pocket_pullback_me01e2d1a` | NZDUSD H4 | simplify | 4 | 7372 | reject | RUNG 0 SANITY | - | - |
| `fib_golden_pocket_pullback_me01e2d1a` | US500 H4 | simplify | 4 | 7372 | reject | RUNG 0 SANITY | - | - |
| `fib_golden_pocket_pullback_me01e2d1a` | USDCAD H4 | simplify | 4 | 7372 | reject | RUNG 0 SANITY | - | - |
| `fib_golden_pocket_pullback_me01e2d1a` | USDCHF H4 | simplify | 4 | 7372 | reject | RUNG 0 SANITY | - | - |
| `fib_golden_pocket_pullback_me01e2d1a` | USDJPY H4 | simplify | 4 | 7372 | reject | RUNG 0 SANITY | - | - |
| `fib_golden_pocket_pullback_me01e2d1a` | XAUUSD H4 | simplify | 4 | 7372 | reject | RUNG 0 SANITY | - | - |
| `fib_golden_pocket_pullback_m76354e7e` | AUDUSD H4 | combine | 2 | 7374 | reject | RUNG 0 SANITY | - | - |
| `fib_golden_pocket_pullback_m76354e7e` | DE40 H4 | combine | 2 | 7374 | reject | RUNG 0 SANITY | - | - |
| `fib_golden_pocket_pullback_m76354e7e` | EURJPY H4 | combine | 2 | 7374 | reject | RUNG 0 SANITY | - | - |
| `fib_golden_pocket_pullback_m76354e7e` | EURUSD H4 | combine | 2 | 7374 | reject | RUNG 0 SANITY | - | - |
| `fib_golden_pocket_pullback_m76354e7e` | GBPJPY H4 | combine | 2 | 7374 | reject | RUNG 0 SANITY | - | - |
| `fib_golden_pocket_pullback_m76354e7e` | GBPUSD H4 | combine | 2 | 7374 | reject | RUNG 0 SANITY | - | - |
| `fib_golden_pocket_pullback_m76354e7e` | NZDUSD H4 | combine | 2 | 7374 | reject | RUNG 0 SANITY | - | - |
| `fib_golden_pocket_pullback_m76354e7e` | US500 H4 | combine | 2 | 7374 | reject | RUNG 0 SANITY | - | - |
| `fib_golden_pocket_pullback_m76354e7e` | USDCAD H4 | combine | 2 | 7374 | reject | RUNG 0 SANITY | - | - |
| `fib_golden_pocket_pullback_m76354e7e` | USDCHF H4 | combine | 2 | 7374 | reject | RUNG 0 SANITY | - | - |
| `fib_golden_pocket_pullback_m76354e7e` | USDJPY H4 | combine | 2 | 7374 | reject | RUNG 0 SANITY | - | - |
| `fib_golden_pocket_pullback_m76354e7e` | XAUUSD H4 | combine | 2 | 7374 | reject | RUNG 0 SANITY | - | - |
| `fib_golden_pocket_pullback_mb73e28fd` | AUDUSD H4 | combine | 2 | 7374 | reject | RUNG 0 SANITY | - | - |
| `fib_golden_pocket_pullback_mb73e28fd` | DE40 H4 | combine | 2 | 7374 | reject | RUNG 0 SANITY | - | - |
| `fib_golden_pocket_pullback_mb73e28fd` | EURJPY H4 | combine | 2 | 7374 | reject | RUNG 0 SANITY | - | - |
| `fib_golden_pocket_pullback_mb73e28fd` | EURUSD H4 | combine | 2 | 7374 | reject | RUNG 0 SANITY | - | - |
| `fib_golden_pocket_pullback_mb73e28fd` | GBPJPY H4 | combine | 2 | 7374 | reject | RUNG 0 SANITY | - | - |
| `fib_golden_pocket_pullback_mb73e28fd` | GBPUSD H4 | combine | 2 | 7374 | reject | RUNG 0 SANITY | - | - |
| `fib_golden_pocket_pullback_mb73e28fd` | NZDUSD H4 | combine | 2 | 7374 | reject | RUNG 0 SANITY | - | - |
| `fib_golden_pocket_pullback_mb73e28fd` | US500 H4 | combine | 2 | 7374 | reject | RUNG 0 SANITY | - | - |
| `fib_golden_pocket_pullback_mb73e28fd` | USDCAD H4 | combine | 2 | 7374 | reject | RUNG 0 SANITY | - | - |
| `fib_golden_pocket_pullback_mb73e28fd` | USDCHF H4 | combine | 2 | 7374 | reject | RUNG 0 SANITY | - | - |
| `fib_golden_pocket_pullback_mb73e28fd` | USDJPY H4 | combine | 2 | 7374 | reject | RUNG 0 SANITY | - | - |
| `fib_golden_pocket_pullback_mb73e28fd` | XAUUSD H4 | combine | 2 | 7374 | reject | RUNG 0 SANITY | - | - |
| `ichimoku_kumo_trend_m689694c1` | AUDUSD H4 | add_filter | 8 | 7368 | reject | RUNG 0 SANITY | - | - |
| `ichimoku_kumo_trend_m689694c1` | DE40 H4 | add_filter | 8 | 7368 | reject | RUNG 0 SANITY | - | - |
| `ichimoku_kumo_trend_m689694c1` | EURJPY H4 | add_filter | 8 | 7368 | reject | RUNG 0 SANITY | - | - |
| `ichimoku_kumo_trend_m689694c1` | EURUSD H4 | add_filter | 8 | 7368 | reject | RUNG 0 SANITY | - | - |
| `ichimoku_kumo_trend_m689694c1` | GBPJPY H4 | add_filter | 8 | 7368 | reject | RUNG 0 SANITY | - | - |
| `ichimoku_kumo_trend_m689694c1` | GBPUSD H4 | add_filter | 8 | 7368 | reject | RUNG 0 SANITY | - | - |
| `ichimoku_kumo_trend_m689694c1` | NZDUSD H4 | add_filter | 8 | 7368 | reject | RUNG 0 SANITY | - | - |
| `ichimoku_kumo_trend_m689694c1` | US500 H4 | add_filter | 8 | 7368 | reject | RUNG 0 SANITY | - | - |
| `ichimoku_kumo_trend_m689694c1` | USDCAD H4 | add_filter | 8 | 7368 | reject | RUNG 0 SANITY | - | - |
| `ichimoku_kumo_trend_m689694c1` | USDCHF H4 | add_filter | 8 | 7368 | reject | RUNG 0 SANITY | - | - |
| `ichimoku_kumo_trend_m689694c1` | USDJPY H4 | add_filter | 8 | 7368 | reject | RUNG 0 SANITY | - | - |
| `ichimoku_kumo_trend_m689694c1` | XAUUSD H4 | add_filter | 8 | 7368 | reject | RUNG 0 SANITY | - | - |
| `ichimoku_kumo_trend_m4195c8bf` | AUDUSD H4 | alter_stop_model | 4 | 7372 | reject | RUNG 0 SANITY | - | - |
| `ichimoku_kumo_trend_m4195c8bf` | DE40 H4 | alter_stop_model | 4 | 7372 | reject | RUNG 0 SANITY | - | - |
| `ichimoku_kumo_trend_m4195c8bf` | EURJPY H4 | alter_stop_model | 4 | 7372 | reject | RUNG 0 SANITY | - | - |
| `ichimoku_kumo_trend_m4195c8bf` | EURUSD H4 | alter_stop_model | 4 | 7372 | reject | RUNG 0 SANITY | - | - |
| `ichimoku_kumo_trend_m4195c8bf` | GBPJPY H4 | alter_stop_model | 4 | 7372 | reject | RUNG 0 SANITY | - | - |
| `ichimoku_kumo_trend_m4195c8bf` | GBPUSD H4 | alter_stop_model | 4 | 7372 | reject | RUNG 0 SANITY | - | - |
| `ichimoku_kumo_trend_m4195c8bf` | NZDUSD H4 | alter_stop_model | 4 | 7372 | reject | RUNG 0 SANITY | - | - |
| `ichimoku_kumo_trend_m4195c8bf` | US500 H4 | alter_stop_model | 4 | 7372 | reject | RUNG 0 SANITY | - | - |
| `ichimoku_kumo_trend_m4195c8bf` | USDCAD H4 | alter_stop_model | 4 | 7372 | reject | RUNG 0 SANITY | - | - |
| `ichimoku_kumo_trend_m4195c8bf` | USDCHF H4 | alter_stop_model | 4 | 7372 | reject | RUNG 0 SANITY | - | - |
| `ichimoku_kumo_trend_m4195c8bf` | USDJPY H4 | alter_stop_model | 4 | 7372 | reject | RUNG 0 SANITY | - | - |
| `ichimoku_kumo_trend_m4195c8bf` | XAUUSD H4 | alter_stop_model | 4 | 7372 | reject | RUNG 0 SANITY | - | - |
| `ichimoku_kumo_trend_m8fbcd36c` | AUDUSD H4 | alter_target_model | 8 | 7368 | reject | RUNG 0 SANITY | - | - |
| `ichimoku_kumo_trend_m8fbcd36c` | DE40 H4 | alter_target_model | 8 | 7368 | reject | RUNG 0 SANITY | - | - |
| `ichimoku_kumo_trend_m8fbcd36c` | EURJPY H4 | alter_target_model | 8 | 7368 | reject | RUNG 0 SANITY | - | - |
| `ichimoku_kumo_trend_m8fbcd36c` | EURUSD H4 | alter_target_model | 8 | 7368 | reject | RUNG 0 SANITY | - | - |
| `ichimoku_kumo_trend_m8fbcd36c` | GBPJPY H4 | alter_target_model | 8 | 7368 | reject | RUNG 0 SANITY | - | - |
| `ichimoku_kumo_trend_m8fbcd36c` | GBPUSD H4 | alter_target_model | 8 | 7368 | reject | RUNG 0 SANITY | - | - |
| `ichimoku_kumo_trend_m8fbcd36c` | NZDUSD H4 | alter_target_model | 8 | 7368 | reject | RUNG 0 SANITY | - | - |
| `ichimoku_kumo_trend_m8fbcd36c` | US500 H4 | alter_target_model | 8 | 7368 | reject | RUNG 0 SANITY | - | - |
| `ichimoku_kumo_trend_m8fbcd36c` | USDCAD H4 | alter_target_model | 8 | 7368 | reject | RUNG 0 SANITY | - | - |
| `ichimoku_kumo_trend_m8fbcd36c` | USDCHF H4 | alter_target_model | 8 | 7368 | reject | RUNG 0 SANITY | - | - |
| `ichimoku_kumo_trend_m8fbcd36c` | USDJPY H4 | alter_target_model | 8 | 7368 | reject | RUNG 0 SANITY | - | - |
| `ichimoku_kumo_trend_m8fbcd36c` | XAUUSD H4 | alter_target_model | 8 | 7368 | reject | RUNG 0 SANITY | - | - |
| `ichimoku_kumo_trend_ma4c92c38` | AUDUSD H4 | change_session_restriction | 8 | 7368 | reject | RUNG 0 SANITY | - | - |
| `ichimoku_kumo_trend_ma4c92c38` | DE40 H4 | change_session_restriction | 8 | 7368 | reject | RUNG 0 SANITY | - | - |
| `ichimoku_kumo_trend_ma4c92c38` | EURJPY H4 | change_session_restriction | 8 | 7368 | reject | RUNG 0 SANITY | - | - |
| `ichimoku_kumo_trend_ma4c92c38` | EURUSD H4 | change_session_restriction | 8 | 7368 | reject | RUNG 0 SANITY | - | - |
| `ichimoku_kumo_trend_ma4c92c38` | GBPJPY H4 | change_session_restriction | 8 | 7368 | reject | RUNG 0 SANITY | - | - |
| `ichimoku_kumo_trend_ma4c92c38` | GBPUSD H4 | change_session_restriction | 8 | 7368 | reject | RUNG 0 SANITY | - | - |
| `ichimoku_kumo_trend_ma4c92c38` | NZDUSD H4 | change_session_restriction | 8 | 7368 | reject | RUNG 0 SANITY | - | - |
| `ichimoku_kumo_trend_ma4c92c38` | US500 H4 | change_session_restriction | 8 | 7368 | reject | RUNG 0 SANITY | - | - |
| `ichimoku_kumo_trend_ma4c92c38` | USDCAD H4 | change_session_restriction | 8 | 7368 | reject | RUNG 0 SANITY | - | - |
| `ichimoku_kumo_trend_ma4c92c38` | USDCHF H4 | change_session_restriction | 8 | 7368 | reject | RUNG 0 SANITY | - | - |
| `ichimoku_kumo_trend_ma4c92c38` | USDJPY H4 | change_session_restriction | 8 | 7368 | reject | RUNG 0 SANITY | - | - |
| `ichimoku_kumo_trend_ma4c92c38` | XAUUSD H4 | change_session_restriction | 8 | 7368 | reject | RUNG 0 SANITY | - | - |
| `ichimoku_kumo_trend_me73acb6c` | AUDUSD H4 | change_confirmation_rule | 8 | 7368 | reject | RUNG 0 SANITY | - | - |
| `ichimoku_kumo_trend_me73acb6c` | DE40 H4 | change_confirmation_rule | 8 | 7368 | reject | RUNG 0 SANITY | - | - |
| `ichimoku_kumo_trend_me73acb6c` | EURJPY H4 | change_confirmation_rule | 8 | 7368 | reject | RUNG 0 SANITY | - | - |
| `ichimoku_kumo_trend_me73acb6c` | EURUSD H4 | change_confirmation_rule | 8 | 7368 | reject | RUNG 0 SANITY | - | - |
| `ichimoku_kumo_trend_me73acb6c` | GBPJPY H4 | change_confirmation_rule | 8 | 7368 | reject | RUNG 0 SANITY | - | - |
| `ichimoku_kumo_trend_me73acb6c` | GBPUSD H4 | change_confirmation_rule | 8 | 7368 | reject | RUNG 0 SANITY | - | - |
| `ichimoku_kumo_trend_me73acb6c` | NZDUSD H4 | change_confirmation_rule | 8 | 7368 | reject | RUNG 0 SANITY | - | - |
| `ichimoku_kumo_trend_me73acb6c` | US500 H4 | change_confirmation_rule | 8 | 7368 | reject | RUNG 0 SANITY | - | - |
| `ichimoku_kumo_trend_me73acb6c` | USDCAD H4 | change_confirmation_rule | 8 | 7368 | reject | RUNG 0 SANITY | - | - |
| `ichimoku_kumo_trend_me73acb6c` | USDCHF H4 | change_confirmation_rule | 8 | 7368 | reject | RUNG 0 SANITY | - | - |
| `ichimoku_kumo_trend_me73acb6c` | USDJPY H4 | change_confirmation_rule | 8 | 7368 | reject | RUNG 0 SANITY | - | - |
| `ichimoku_kumo_trend_me73acb6c` | XAUUSD H4 | change_confirmation_rule | 8 | 7368 | reject | RUNG 0 SANITY | - | - |
| `ichimoku_kumo_trend_mca231a0f` | AUDUSD H4 | simplify | 8 | 7368 | reject | RUNG 0 SANITY | - | - |
| `ichimoku_kumo_trend_mca231a0f` | DE40 H4 | simplify | 8 | 7368 | reject | RUNG 0 SANITY | - | - |
| `ichimoku_kumo_trend_mca231a0f` | EURJPY H4 | simplify | 8 | 7368 | reject | RUNG 0 SANITY | - | - |
| `ichimoku_kumo_trend_mca231a0f` | EURUSD H4 | simplify | 8 | 7368 | reject | RUNG 0 SANITY | - | - |
| `ichimoku_kumo_trend_mca231a0f` | GBPJPY H4 | simplify | 8 | 7368 | reject | RUNG 0 SANITY | - | - |
| `ichimoku_kumo_trend_mca231a0f` | GBPUSD H4 | simplify | 8 | 7368 | reject | RUNG 0 SANITY | - | - |
| `ichimoku_kumo_trend_mca231a0f` | NZDUSD H4 | simplify | 8 | 7368 | reject | RUNG 0 SANITY | - | - |
| `ichimoku_kumo_trend_mca231a0f` | US500 H4 | simplify | 8 | 7368 | reject | RUNG 0 SANITY | - | - |
| `ichimoku_kumo_trend_mca231a0f` | USDCAD H4 | simplify | 8 | 7368 | reject | RUNG 0 SANITY | - | - |
| `ichimoku_kumo_trend_mca231a0f` | USDCHF H4 | simplify | 8 | 7368 | reject | RUNG 0 SANITY | - | - |
| `ichimoku_kumo_trend_mca231a0f` | USDJPY H4 | simplify | 8 | 7368 | reject | RUNG 0 SANITY | - | - |
| `ichimoku_kumo_trend_mca231a0f` | XAUUSD H4 | simplify | 8 | 7368 | reject | RUNG 0 SANITY | - | - |
| `ichimoku_kumo_trend_m0bd80cab` | AUDUSD H4 | combine | 4 | 7372 | reject | RUNG 0 SANITY | - | - |
| `ichimoku_kumo_trend_m0bd80cab` | DE40 H4 | combine | 4 | 7372 | reject | RUNG 0 SANITY | - | - |
| `ichimoku_kumo_trend_m0bd80cab` | EURJPY H4 | combine | 4 | 7372 | reject | RUNG 0 SANITY | - | - |
| `ichimoku_kumo_trend_m0bd80cab` | EURUSD H4 | combine | 4 | 7372 | reject | RUNG 0 SANITY | - | - |
| `ichimoku_kumo_trend_m0bd80cab` | GBPJPY H4 | combine | 4 | 7372 | reject | RUNG 0 SANITY | - | - |
| `ichimoku_kumo_trend_m0bd80cab` | GBPUSD H4 | combine | 4 | 7372 | reject | RUNG 0 SANITY | - | - |
| `ichimoku_kumo_trend_m0bd80cab` | NZDUSD H4 | combine | 4 | 7372 | reject | RUNG 0 SANITY | - | - |
| `ichimoku_kumo_trend_m0bd80cab` | US500 H4 | combine | 4 | 7372 | reject | RUNG 0 SANITY | - | - |
| `ichimoku_kumo_trend_m0bd80cab` | USDCAD H4 | combine | 4 | 7372 | reject | RUNG 0 SANITY | - | - |
| `ichimoku_kumo_trend_m0bd80cab` | USDCHF H4 | combine | 4 | 7372 | reject | RUNG 0 SANITY | - | - |
| `ichimoku_kumo_trend_m0bd80cab` | USDJPY H4 | combine | 4 | 7372 | reject | RUNG 0 SANITY | - | - |
| `ichimoku_kumo_trend_m0bd80cab` | XAUUSD H4 | combine | 4 | 7372 | reject | RUNG 0 SANITY | - | - |
| `ichimoku_kumo_trend_m67c7da5a` | AUDUSD H4 | combine | 4 | 7372 | reject | RUNG 0 SANITY | - | - |
| `ichimoku_kumo_trend_m67c7da5a` | EURUSD H4 | combine | 4 | 7372 | reject | RUNG 0 SANITY | - | - |
| `ichimoku_kumo_trend_m67c7da5a` | GBPUSD H4 | combine | 4 | 7372 | reject | RUNG 0 SANITY | - | - |
| `ichimoku_kumo_trend_m67c7da5a` | NZDUSD H4 | combine | 4 | 7372 | reject | RUNG 0 SANITY | - | - |
| `ichimoku_kumo_trend_m67c7da5a` | USDCAD H4 | combine | 4 | 7372 | reject | RUNG 0 SANITY | - | - |
| `ichimoku_kumo_trend_m67c7da5a` | USDCHF H4 | combine | 4 | 7372 | reject | RUNG 0 SANITY | - | - |
| `ichimoku_kumo_trend_m67c7da5a` | USDJPY H4 | combine | 4 | 7372 | reject | RUNG 0 SANITY | - | - |
| `macd_ema_trend_hybrid_ma2bc336b` | AUDUSD H4 | add_filter | 8 | 7368 | reject | RUNG 0 SANITY | - | - |
| `macd_ema_trend_hybrid_ma2bc336b` | DE40 H4 | add_filter | 8 | 7368 | reject | RUNG 0 SANITY | - | - |
| `macd_ema_trend_hybrid_ma2bc336b` | EURJPY H4 | add_filter | 8 | 7368 | reject | RUNG 0 SANITY | - | - |
| `macd_ema_trend_hybrid_ma2bc336b` | EURUSD H4 | add_filter | 8 | 7368 | reject | RUNG 0 SANITY | - | - |
| `macd_ema_trend_hybrid_ma2bc336b` | GBPJPY H4 | add_filter | 8 | 7368 | reject | RUNG 0 SANITY | - | - |
| `macd_ema_trend_hybrid_ma2bc336b` | GBPUSD H4 | add_filter | 8 | 7368 | reject | RUNG 0 SANITY | - | - |
| `macd_ema_trend_hybrid_ma2bc336b` | NZDUSD H4 | add_filter | 8 | 7368 | reject | RUNG 0 SANITY | - | - |
| `macd_ema_trend_hybrid_ma2bc336b` | US500 H4 | add_filter | 8 | 7368 | reject | RUNG 0 SANITY | - | - |
| `macd_ema_trend_hybrid_ma2bc336b` | USDCAD H4 | add_filter | 8 | 7368 | reject | RUNG 0 SANITY | - | - |
| `macd_ema_trend_hybrid_ma2bc336b` | USDCHF H4 | add_filter | 8 | 7368 | reject | RUNG 0 SANITY | - | - |
| `macd_ema_trend_hybrid_ma2bc336b` | USDJPY H4 | add_filter | 8 | 7368 | reject | RUNG 0 SANITY | - | - |
| `macd_ema_trend_hybrid_ma2bc336b` | XAUUSD H4 | add_filter | 8 | 7368 | reject | RUNG 0 SANITY | - | - |
| `macd_ema_trend_hybrid_m03d3ee23` | AUDUSD H4 | alter_stop_model | 4 | 7372 | reject | RUNG 0 SANITY | - | - |
| `macd_ema_trend_hybrid_m03d3ee23` | DE40 H4 | alter_stop_model | 4 | 7372 | reject | RUNG 0 SANITY | - | - |
| `macd_ema_trend_hybrid_m03d3ee23` | EURJPY H4 | alter_stop_model | 4 | 7372 | reject | RUNG 0 SANITY | - | - |
| `macd_ema_trend_hybrid_m03d3ee23` | EURUSD H4 | alter_stop_model | 4 | 7372 | reject | RUNG 0 SANITY | - | - |
| `macd_ema_trend_hybrid_m03d3ee23` | GBPJPY H4 | alter_stop_model | 4 | 7372 | reject | RUNG 0 SANITY | - | - |
| `macd_ema_trend_hybrid_m03d3ee23` | GBPUSD H4 | alter_stop_model | 4 | 7372 | reject | RUNG 0 SANITY | - | - |
| `macd_ema_trend_hybrid_m03d3ee23` | NZDUSD H4 | alter_stop_model | 4 | 7372 | reject | RUNG 0 SANITY | - | - |
| `macd_ema_trend_hybrid_m03d3ee23` | US500 H4 | alter_stop_model | 4 | 7372 | reject | RUNG 0 SANITY | - | - |
| `macd_ema_trend_hybrid_m03d3ee23` | USDCAD H4 | alter_stop_model | 4 | 7372 | reject | RUNG 0 SANITY | - | - |
| `macd_ema_trend_hybrid_m03d3ee23` | USDCHF H4 | alter_stop_model | 4 | 7372 | reject | RUNG 0 SANITY | - | - |
| `macd_ema_trend_hybrid_m03d3ee23` | USDJPY H4 | alter_stop_model | 4 | 7372 | reject | RUNG 0 SANITY | - | - |
| `macd_ema_trend_hybrid_m03d3ee23` | XAUUSD H4 | alter_stop_model | 4 | 7372 | reject | RUNG 0 SANITY | - | - |
| `macd_ema_trend_hybrid_m3728054d` | AUDUSD H4 | alter_target_model | 8 | 7368 | reject | RUNG 0 SANITY | - | - |
| `macd_ema_trend_hybrid_m3728054d` | DE40 H4 | alter_target_model | 8 | 7368 | reject | RUNG 0 SANITY | - | - |
| `macd_ema_trend_hybrid_m3728054d` | EURJPY H4 | alter_target_model | 8 | 7368 | reject | RUNG 0 SANITY | - | - |
| `macd_ema_trend_hybrid_m3728054d` | EURUSD H4 | alter_target_model | 8 | 7368 | reject | RUNG 0 SANITY | - | - |
| `macd_ema_trend_hybrid_m3728054d` | GBPJPY H4 | alter_target_model | 8 | 7368 | reject | RUNG 0 SANITY | - | - |
| `macd_ema_trend_hybrid_m3728054d` | GBPUSD H4 | alter_target_model | 8 | 7368 | reject | RUNG 0 SANITY | - | - |
| `macd_ema_trend_hybrid_m3728054d` | NZDUSD H4 | alter_target_model | 8 | 7368 | reject | RUNG 0 SANITY | - | - |
| `macd_ema_trend_hybrid_m3728054d` | US500 H4 | alter_target_model | 8 | 7368 | reject | RUNG 0 SANITY | - | - |
| `macd_ema_trend_hybrid_m3728054d` | USDCAD H4 | alter_target_model | 8 | 7368 | reject | RUNG 0 SANITY | - | - |
| `macd_ema_trend_hybrid_m3728054d` | USDCHF H4 | alter_target_model | 8 | 7368 | reject | RUNG 0 SANITY | - | - |
| `macd_ema_trend_hybrid_m3728054d` | USDJPY H4 | alter_target_model | 8 | 7368 | reject | RUNG 0 SANITY | - | - |
| `macd_ema_trend_hybrid_m3728054d` | XAUUSD H4 | alter_target_model | 8 | 7368 | reject | RUNG 0 SANITY | - | - |
| `macd_ema_trend_hybrid_m4b9a1dcd` | AUDUSD H4 | change_regime_gate | 8 | 7368 | reject | RUNG 0 SANITY | - | - |
| `macd_ema_trend_hybrid_m4b9a1dcd` | DE40 H4 | change_regime_gate | 8 | 7368 | reject | RUNG 0 SANITY | - | - |
| `macd_ema_trend_hybrid_m4b9a1dcd` | EURJPY H4 | change_regime_gate | 8 | 7368 | reject | RUNG 0 SANITY | - | - |
| `macd_ema_trend_hybrid_m4b9a1dcd` | EURUSD H4 | change_regime_gate | 8 | 7368 | reject | RUNG 0 SANITY | - | - |
| `macd_ema_trend_hybrid_m4b9a1dcd` | GBPJPY H4 | change_regime_gate | 8 | 7368 | reject | RUNG 0 SANITY | - | - |
| `macd_ema_trend_hybrid_m4b9a1dcd` | GBPUSD H4 | change_regime_gate | 8 | 7368 | reject | RUNG 0 SANITY | - | - |
| `macd_ema_trend_hybrid_m4b9a1dcd` | NZDUSD H4 | change_regime_gate | 8 | 7368 | reject | RUNG 0 SANITY | - | - |
| `macd_ema_trend_hybrid_m4b9a1dcd` | US500 H4 | change_regime_gate | 8 | 7368 | reject | RUNG 0 SANITY | - | - |
| `macd_ema_trend_hybrid_m4b9a1dcd` | USDCAD H4 | change_regime_gate | 8 | 7368 | reject | RUNG 0 SANITY | - | - |
| `macd_ema_trend_hybrid_m4b9a1dcd` | USDCHF H4 | change_regime_gate | 8 | 7368 | reject | RUNG 0 SANITY | - | - |
| `macd_ema_trend_hybrid_m4b9a1dcd` | USDJPY H4 | change_regime_gate | 8 | 7368 | reject | RUNG 0 SANITY | - | - |
| `macd_ema_trend_hybrid_m4b9a1dcd` | XAUUSD H4 | change_regime_gate | 8 | 7368 | reject | RUNG 0 SANITY | - | - |
| `macd_ema_trend_hybrid_m03f1d536` | AUDUSD H4 | change_session_restriction | 8 | 7368 | reject | RUNG 0 SANITY | - | - |
| `macd_ema_trend_hybrid_m03f1d536` | DE40 H4 | change_session_restriction | 8 | 7368 | reject | RUNG 0 SANITY | - | - |
| `macd_ema_trend_hybrid_m03f1d536` | EURJPY H4 | change_session_restriction | 8 | 7368 | reject | RUNG 0 SANITY | - | - |
| `macd_ema_trend_hybrid_m03f1d536` | EURUSD H4 | change_session_restriction | 8 | 7368 | reject | RUNG 0 SANITY | - | - |
| `macd_ema_trend_hybrid_m03f1d536` | GBPJPY H4 | change_session_restriction | 8 | 7368 | reject | RUNG 0 SANITY | - | - |
| `macd_ema_trend_hybrid_m03f1d536` | GBPUSD H4 | change_session_restriction | 8 | 7368 | reject | RUNG 0 SANITY | - | - |
| `macd_ema_trend_hybrid_m03f1d536` | NZDUSD H4 | change_session_restriction | 8 | 7368 | reject | RUNG 0 SANITY | - | - |
| `macd_ema_trend_hybrid_m03f1d536` | US500 H4 | change_session_restriction | 8 | 7368 | reject | RUNG 0 SANITY | - | - |
| `macd_ema_trend_hybrid_m03f1d536` | USDCAD H4 | change_session_restriction | 8 | 7368 | reject | RUNG 0 SANITY | - | - |
| `macd_ema_trend_hybrid_m03f1d536` | USDCHF H4 | change_session_restriction | 8 | 7368 | reject | RUNG 0 SANITY | - | - |
| `macd_ema_trend_hybrid_m03f1d536` | USDJPY H4 | change_session_restriction | 8 | 7368 | reject | RUNG 0 SANITY | - | - |
| `macd_ema_trend_hybrid_m03f1d536` | XAUUSD H4 | change_session_restriction | 8 | 7368 | reject | RUNG 0 SANITY | - | - |
| `macd_ema_trend_hybrid_mb7889927` | AUDUSD H4 | change_confirmation_rule | 8 | 7368 | reject | RUNG 0 SANITY | - | - |
| `macd_ema_trend_hybrid_mb7889927` | DE40 H4 | change_confirmation_rule | 8 | 7368 | reject | RUNG 0 SANITY | - | - |
| `macd_ema_trend_hybrid_mb7889927` | EURJPY H4 | change_confirmation_rule | 8 | 7368 | reject | RUNG 0 SANITY | - | - |
| `macd_ema_trend_hybrid_mb7889927` | EURUSD H4 | change_confirmation_rule | 8 | 7368 | reject | RUNG 0 SANITY | - | - |
| `macd_ema_trend_hybrid_mb7889927` | GBPJPY H4 | change_confirmation_rule | 8 | 7368 | reject | RUNG 0 SANITY | - | - |
| `macd_ema_trend_hybrid_mb7889927` | GBPUSD H4 | change_confirmation_rule | 8 | 7368 | reject | RUNG 0 SANITY | - | - |
| `macd_ema_trend_hybrid_mb7889927` | NZDUSD H4 | change_confirmation_rule | 8 | 7368 | reject | RUNG 0 SANITY | - | - |
| `macd_ema_trend_hybrid_mb7889927` | US500 H4 | change_confirmation_rule | 8 | 7368 | reject | RUNG 0 SANITY | - | - |
| `macd_ema_trend_hybrid_mb7889927` | USDCAD H4 | change_confirmation_rule | 8 | 7368 | reject | RUNG 0 SANITY | - | - |
| `macd_ema_trend_hybrid_mb7889927` | USDCHF H4 | change_confirmation_rule | 8 | 7368 | reject | RUNG 0 SANITY | - | - |
| `macd_ema_trend_hybrid_mb7889927` | USDJPY H4 | change_confirmation_rule | 8 | 7368 | reject | RUNG 0 SANITY | - | - |
| `macd_ema_trend_hybrid_mb7889927` | XAUUSD H4 | change_confirmation_rule | 8 | 7368 | reject | RUNG 0 SANITY | - | - |
| `macd_ema_trend_hybrid_mc45594a0` | AUDUSD H4 | simplify | 8 | 7368 | reject | RUNG 0 SANITY | - | - |
| `macd_ema_trend_hybrid_mc45594a0` | DE40 H4 | simplify | 8 | 7368 | reject | RUNG 0 SANITY | - | - |
| `macd_ema_trend_hybrid_mc45594a0` | EURJPY H4 | simplify | 8 | 7368 | reject | RUNG 0 SANITY | - | - |
| `macd_ema_trend_hybrid_mc45594a0` | EURUSD H4 | simplify | 8 | 7368 | reject | RUNG 0 SANITY | - | - |
| `macd_ema_trend_hybrid_mc45594a0` | GBPJPY H4 | simplify | 8 | 7368 | reject | RUNG 0 SANITY | - | - |
| `macd_ema_trend_hybrid_mc45594a0` | GBPUSD H4 | simplify | 8 | 7368 | reject | RUNG 0 SANITY | - | - |
| `macd_ema_trend_hybrid_mc45594a0` | NZDUSD H4 | simplify | 8 | 7368 | reject | RUNG 0 SANITY | - | - |
| `macd_ema_trend_hybrid_mc45594a0` | US500 H4 | simplify | 8 | 7368 | reject | RUNG 0 SANITY | - | - |
| `macd_ema_trend_hybrid_mc45594a0` | USDCAD H4 | simplify | 8 | 7368 | reject | RUNG 0 SANITY | - | - |
| `macd_ema_trend_hybrid_mc45594a0` | USDCHF H4 | simplify | 8 | 7368 | reject | RUNG 0 SANITY | - | - |
| `macd_ema_trend_hybrid_mc45594a0` | USDJPY H4 | simplify | 8 | 7368 | reject | RUNG 0 SANITY | - | - |
| `macd_ema_trend_hybrid_mc45594a0` | XAUUSD H4 | simplify | 8 | 7368 | reject | RUNG 0 SANITY | - | - |
| `macd_ema_trend_hybrid_m270351c7` | AUDUSD H4 | combine | 4 | 7372 | reject | RUNG 0 SANITY | - | - |
| `macd_ema_trend_hybrid_m270351c7` | DE40 H4 | combine | 4 | 7372 | reject | RUNG 0 SANITY | - | - |
| `macd_ema_trend_hybrid_m270351c7` | EURJPY H4 | combine | 4 | 7372 | reject | RUNG 0 SANITY | - | - |
| `macd_ema_trend_hybrid_m270351c7` | EURUSD H4 | combine | 4 | 7372 | reject | RUNG 0 SANITY | - | - |
| `macd_ema_trend_hybrid_m270351c7` | GBPJPY H4 | combine | 4 | 7372 | reject | RUNG 0 SANITY | - | - |
| `macd_ema_trend_hybrid_m270351c7` | GBPUSD H4 | combine | 4 | 7372 | reject | RUNG 0 SANITY | - | - |
| `macd_ema_trend_hybrid_m270351c7` | NZDUSD H4 | combine | 4 | 7372 | reject | RUNG 0 SANITY | - | - |
| `macd_ema_trend_hybrid_m270351c7` | US500 H4 | combine | 4 | 7372 | reject | RUNG 0 SANITY | - | - |
| `macd_ema_trend_hybrid_m270351c7` | USDCAD H4 | combine | 4 | 7372 | reject | RUNG 0 SANITY | - | - |
| `macd_ema_trend_hybrid_m270351c7` | USDCHF H4 | combine | 4 | 7372 | reject | RUNG 0 SANITY | - | - |
| `macd_ema_trend_hybrid_m270351c7` | USDJPY H4 | combine | 4 | 7372 | reject | RUNG 0 SANITY | - | - |
| `macd_ema_trend_hybrid_m270351c7` | XAUUSD H4 | combine | 4 | 7372 | reject | RUNG 0 SANITY | - | - |
| `macd_ema_trend_hybrid_mbd5c916c` | AUDUSD H4 | combine | 4 | 7372 | reject | RUNG 0 SANITY | - | - |
| `macd_ema_trend_hybrid_mbd5c916c` | EURUSD H4 | combine | 4 | 7372 | reject | RUNG 0 SANITY | - | - |
| `macd_ema_trend_hybrid_mbd5c916c` | GBPUSD H4 | combine | 4 | 7372 | reject | RUNG 0 SANITY | - | - |
| `macd_ema_trend_hybrid_mbd5c916c` | NZDUSD H4 | combine | 4 | 7372 | reject | RUNG 0 SANITY | - | - |
| `macd_ema_trend_hybrid_mbd5c916c` | USDCAD H4 | combine | 4 | 7372 | reject | RUNG 0 SANITY | - | - |
| `macd_ema_trend_hybrid_mbd5c916c` | USDCHF H4 | combine | 4 | 7372 | reject | RUNG 0 SANITY | - | - |
| `macd_ema_trend_hybrid_mbd5c916c` | USDJPY H4 | combine | 4 | 7372 | reject | RUNG 0 SANITY | - | - |
| `rsi_band_mean_reversion_md48b52cb` | AUDUSD H4 | add_filter | 8 | 7368 | reject | RUNG 0 SANITY | - | - |
| `rsi_band_mean_reversion_md48b52cb` | EURGBP H4 | add_filter | 8 | 7368 | reject | RUNG 0 SANITY | - | - |
| `rsi_band_mean_reversion_md48b52cb` | EURUSD H4 | add_filter | 8 | 7368 | reject | RUNG 0 SANITY | - | - |
| `rsi_band_mean_reversion_md48b52cb` | GBPUSD H4 | add_filter | 8 | 7368 | reject | RUNG 0 SANITY | - | - |
| `rsi_band_mean_reversion_md48b52cb` | NZDUSD H4 | add_filter | 8 | 7368 | reject | RUNG 0 SANITY | - | - |
| `rsi_band_mean_reversion_md48b52cb` | USDCAD H4 | add_filter | 8 | 7368 | reject | RUNG 0 SANITY | - | - |
| `rsi_band_mean_reversion_md48b52cb` | USDCHF H4 | add_filter | 8 | 7368 | reject | RUNG 0 SANITY | - | - |
| `rsi_band_mean_reversion_md48b52cb` | USDJPY H4 | add_filter | 8 | 7368 | reject | RUNG 0 SANITY | - | - |
| `rsi_band_mean_reversion_mdbb3266a` | AUDUSD H4 | alter_stop_model | 8 | 7368 | reject | RUNG 0 SANITY | - | - |
| `rsi_band_mean_reversion_mdbb3266a` | EURGBP H4 | alter_stop_model | 8 | 7368 | reject | RUNG 0 SANITY | - | - |
| `rsi_band_mean_reversion_mdbb3266a` | EURUSD H4 | alter_stop_model | 8 | 7368 | reject | RUNG 0 SANITY | - | - |
| `rsi_band_mean_reversion_mdbb3266a` | GBPUSD H4 | alter_stop_model | 8 | 7368 | reject | RUNG 0 SANITY | - | - |
| `rsi_band_mean_reversion_mdbb3266a` | NZDUSD H4 | alter_stop_model | 8 | 7368 | reject | RUNG 0 SANITY | - | - |
| `rsi_band_mean_reversion_mdbb3266a` | USDCAD H4 | alter_stop_model | 8 | 7368 | reject | RUNG 0 SANITY | - | - |
| `rsi_band_mean_reversion_mdbb3266a` | USDCHF H4 | alter_stop_model | 8 | 7368 | reject | RUNG 0 SANITY | - | - |
| `rsi_band_mean_reversion_mdbb3266a` | USDJPY H4 | alter_stop_model | 8 | 7368 | reject | RUNG 0 SANITY | - | - |
| `rsi_band_mean_reversion_m6e0465f0` | AUDUSD H4 | alter_target_model | 8 | 7368 | reject | RUNG 0 SANITY | - | - |
| `rsi_band_mean_reversion_m6e0465f0` | EURGBP H4 | alter_target_model | 8 | 7368 | reject | RUNG 0 SANITY | - | - |
| `rsi_band_mean_reversion_m6e0465f0` | EURUSD H4 | alter_target_model | 8 | 7368 | reject | RUNG 0 SANITY | - | - |
| `rsi_band_mean_reversion_m6e0465f0` | GBPUSD H4 | alter_target_model | 8 | 7368 | reject | RUNG 0 SANITY | - | - |
| `rsi_band_mean_reversion_m6e0465f0` | NZDUSD H4 | alter_target_model | 8 | 7368 | reject | RUNG 0 SANITY | - | - |
| `rsi_band_mean_reversion_m6e0465f0` | USDCAD H4 | alter_target_model | 8 | 7368 | reject | RUNG 0 SANITY | - | - |
| `rsi_band_mean_reversion_m6e0465f0` | USDCHF H4 | alter_target_model | 8 | 7368 | reject | RUNG 0 SANITY | - | - |
| `rsi_band_mean_reversion_m6e0465f0` | USDJPY H4 | alter_target_model | 8 | 7368 | reject | RUNG 0 SANITY | - | - |
| `rsi_band_mean_reversion_me1d6d436` | AUDUSD H4 | change_session_restriction | 8 | 7368 | reject | RUNG 0 SANITY | - | - |
| `rsi_band_mean_reversion_me1d6d436` | EURGBP H4 | change_session_restriction | 8 | 7368 | reject | RUNG 0 SANITY | - | - |
| `rsi_band_mean_reversion_me1d6d436` | EURUSD H4 | change_session_restriction | 8 | 7368 | reject | RUNG 0 SANITY | - | - |
| `rsi_band_mean_reversion_me1d6d436` | GBPUSD H4 | change_session_restriction | 8 | 7368 | reject | RUNG 0 SANITY | - | - |
| `rsi_band_mean_reversion_me1d6d436` | NZDUSD H4 | change_session_restriction | 8 | 7368 | reject | RUNG 0 SANITY | - | - |
| `rsi_band_mean_reversion_me1d6d436` | USDCAD H4 | change_session_restriction | 8 | 7368 | reject | RUNG 0 SANITY | - | - |
| `rsi_band_mean_reversion_me1d6d436` | USDCHF H4 | change_session_restriction | 8 | 7368 | reject | RUNG 0 SANITY | - | - |
| `rsi_band_mean_reversion_me1d6d436` | USDJPY H4 | change_session_restriction | 8 | 7368 | reject | RUNG 0 SANITY | - | - |
| `rsi_band_mean_reversion_m42a36d28` | AUDUSD H4 | change_confirmation_rule | 8 | 7368 | reject | RUNG 0 SANITY | - | - |
| `rsi_band_mean_reversion_m42a36d28` | EURGBP H4 | change_confirmation_rule | 8 | 7368 | reject | RUNG 0 SANITY | - | - |
| `rsi_band_mean_reversion_m42a36d28` | EURUSD H4 | change_confirmation_rule | 8 | 7368 | reject | RUNG 0 SANITY | - | - |
| `rsi_band_mean_reversion_m42a36d28` | GBPUSD H4 | change_confirmation_rule | 8 | 7368 | reject | RUNG 0 SANITY | - | - |
| `rsi_band_mean_reversion_m42a36d28` | NZDUSD H4 | change_confirmation_rule | 8 | 7368 | reject | RUNG 0 SANITY | - | - |
| `rsi_band_mean_reversion_m42a36d28` | USDCAD H4 | change_confirmation_rule | 8 | 7368 | reject | RUNG 0 SANITY | - | - |
| `rsi_band_mean_reversion_m42a36d28` | USDCHF H4 | change_confirmation_rule | 8 | 7368 | reject | RUNG 0 SANITY | - | - |
| `rsi_band_mean_reversion_m42a36d28` | USDJPY H4 | change_confirmation_rule | 8 | 7368 | reject | RUNG 0 SANITY | - | - |
| `rsi_band_mean_reversion_m7c32d514` | AUDUSD H4 | simplify | 8 | 7368 | reject | RUNG 0 SANITY | - | - |
| `rsi_band_mean_reversion_m7c32d514` | EURGBP H4 | simplify | 8 | 7368 | reject | RUNG 0 SANITY | - | - |
| `rsi_band_mean_reversion_m7c32d514` | EURUSD H4 | simplify | 8 | 7368 | reject | RUNG 0 SANITY | - | - |
| `rsi_band_mean_reversion_m7c32d514` | GBPUSD H4 | simplify | 8 | 7368 | reject | RUNG 0 SANITY | - | - |
| `rsi_band_mean_reversion_m7c32d514` | NZDUSD H4 | simplify | 8 | 7368 | reject | RUNG 0 SANITY | - | - |
| `rsi_band_mean_reversion_m7c32d514` | USDCAD H4 | simplify | 8 | 7368 | reject | RUNG 0 SANITY | - | - |
| `rsi_band_mean_reversion_m7c32d514` | USDCHF H4 | simplify | 8 | 7368 | reject | RUNG 0 SANITY | - | - |
| `rsi_band_mean_reversion_m7c32d514` | USDJPY H4 | simplify | 8 | 7368 | reject | RUNG 0 SANITY | - | - |
| `rsi_band_mean_reversion_m6113daf1` | AUDUSD H4 | combine | 8 | 7368 | reject | RUNG 0 SANITY | - | - |
| `rsi_band_mean_reversion_m6113daf1` | EURUSD H4 | combine | 8 | 7368 | reject | RUNG 0 SANITY | - | - |
| `rsi_band_mean_reversion_m6113daf1` | GBPUSD H4 | combine | 8 | 7368 | reject | RUNG 0 SANITY | - | - |
| `rsi_band_mean_reversion_m6113daf1` | NZDUSD H4 | combine | 8 | 7368 | reject | RUNG 0 SANITY | - | - |
| `rsi_band_mean_reversion_m6113daf1` | USDCAD H4 | combine | 8 | 7368 | reject | RUNG 0 SANITY | - | - |
| `rsi_band_mean_reversion_m6113daf1` | USDCHF H4 | combine | 8 | 7368 | reject | RUNG 0 SANITY | - | - |
| `rsi_band_mean_reversion_m6113daf1` | USDJPY H4 | combine | 8 | 7368 | reject | RUNG 0 SANITY | - | - |
| `rsi_band_mean_reversion_md0807760` | AUDUSD H4 | combine | 8 | 7368 | reject | RUNG 0 SANITY | - | - |
| `rsi_band_mean_reversion_md0807760` | EURUSD H4 | combine | 8 | 7368 | reject | RUNG 0 SANITY | - | - |
| `rsi_band_mean_reversion_md0807760` | GBPUSD H4 | combine | 8 | 7368 | reject | RUNG 0 SANITY | - | - |
| `rsi_band_mean_reversion_md0807760` | NZDUSD H4 | combine | 8 | 7368 | reject | RUNG 0 SANITY | - | - |
| `rsi_band_mean_reversion_md0807760` | USDCAD H4 | combine | 8 | 7368 | reject | RUNG 0 SANITY | - | - |
| `rsi_band_mean_reversion_md0807760` | USDCHF H4 | combine | 8 | 7368 | reject | RUNG 0 SANITY | - | - |
| `rsi_band_mean_reversion_md0807760` | USDJPY H4 | combine | 8 | 7368 | reject | RUNG 0 SANITY | - | - |
| `rsi_band_mean_reversion_m29180d06` | AUDUSD H4 | combine | 8 | 7368 | reject | RUNG 0 SANITY | - | - |
| `rsi_band_mean_reversion_m29180d06` | EURUSD H4 | combine | 8 | 7368 | reject | RUNG 0 SANITY | - | - |
| `rsi_band_mean_reversion_m29180d06` | GBPUSD H4 | combine | 8 | 7368 | reject | RUNG 0 SANITY | - | - |
| `rsi_band_mean_reversion_m29180d06` | NZDUSD H4 | combine | 8 | 7368 | reject | RUNG 0 SANITY | - | - |
| `rsi_band_mean_reversion_m29180d06` | USDCAD H4 | combine | 8 | 7368 | reject | RUNG 0 SANITY | - | - |
| `rsi_band_mean_reversion_m29180d06` | USDCHF H4 | combine | 8 | 7368 | reject | RUNG 0 SANITY | - | - |
| `rsi_band_mean_reversion_m29180d06` | USDJPY H4 | combine | 8 | 7368 | reject | RUNG 0 SANITY | - | - |

### Why each one stopped

- donchian_breakout_atr on AUDUSD H4: REJECT at RUNG 0 SANITY -- RUNG 0 SANITY: expectancy -1.60618 at declared defaults is not positive; there is no edge to validate.
- donchian_breakout_atr on DE40 H4: REJECT at RUNG 0 SANITY -- min_trades: observed 358, required gte 400, short by 42
- donchian_breakout_atr on EURJPY H4: REJECT at RUNG 0 SANITY -- RUNG 0 SANITY: expectancy -0.127253 at declared defaults is not positive; there is no edge to validate.
- donchian_breakout_atr on EURUSD H4: REJECT at RUNG 0 SANITY -- RUNG 0 SANITY: expectancy -0.876489 at declared defaults is not positive; there is no edge to validate.
- donchian_breakout_atr on GBPJPY H4: REJECT at RUNG 2 WALK_FORWARD -- walk_forward_efficiency: observed -7.381, required gte 50, short by 57.38
- donchian_breakout_atr on GBPUSD H4: REJECT at RUNG 0 SANITY -- RUNG 0 SANITY: expectancy -1.11875 at declared defaults is not positive; there is no edge to validate.
- donchian_breakout_atr on NZDUSD H4: REJECT at RUNG 0 SANITY -- RUNG 0 SANITY: expectancy -0.988952 at declared defaults is not positive; there is no edge to validate.
- donchian_breakout_atr on US500 H4: REJECT at RUNG 0 SANITY -- RUNG 0 SANITY: expectancy -1.16526 at declared defaults is not positive; there is no edge to validate.
- donchian_breakout_atr on USDCAD H4: REJECT at RUNG 0 SANITY -- RUNG 0 SANITY: expectancy -0.270177 at declared defaults is not positive; there is no edge to validate.
- donchian_breakout_atr on USDCHF H4: REJECT at RUNG 0 SANITY -- RUNG 0 SANITY: expectancy -0.868505 at declared defaults is not positive; there is no edge to validate.
- donchian_breakout_atr on USDJPY H4: REJECT at RUNG 0 SANITY -- RUNG 0 SANITY: expectancy -0.820287 at declared defaults is not positive; there is no edge to validate.
- donchian_breakout_atr on XAUUSD H4: REJECT at RUNG 2 WALK_FORWARD -- oos_window_hit_rate: observed 0.25, required gte 0.6, short by 0.35
- fib_golden_pocket_pullback on AUDUSD H4: REJECT at RUNG 0 SANITY -- min_trades: observed 278, required gte 400, short by 122
- fib_golden_pocket_pullback on DE40 H4: REJECT at RUNG 0 SANITY -- min_trades: observed 193, required gte 400, short by 207
- fib_golden_pocket_pullback on EURJPY H4: REJECT at RUNG 0 SANITY -- min_trades: observed 273, required gte 400, short by 127
- fib_golden_pocket_pullback on EURUSD H4: REJECT at RUNG 0 SANITY -- min_trades: observed 261, required gte 400, short by 139
- fib_golden_pocket_pullback on GBPJPY H4: REJECT at RUNG 0 SANITY -- min_trades: observed 279, required gte 400, short by 121
- fib_golden_pocket_pullback on GBPUSD H4: REJECT at RUNG 0 SANITY -- min_trades: observed 272, required gte 400, short by 128
- fib_golden_pocket_pullback on NZDUSD H4: REJECT at RUNG 0 SANITY -- min_trades: observed 257, required gte 400, short by 143
- fib_golden_pocket_pullback on US500 H4: REJECT at RUNG 0 SANITY -- min_trades: observed 250, required gte 400, short by 150
- fib_golden_pocket_pullback on USDCAD H4: REJECT at RUNG 0 SANITY -- min_trades: observed 295, required gte 400, short by 105
- fib_golden_pocket_pullback on USDCHF H4: REJECT at RUNG 0 SANITY -- min_trades: observed 293, required gte 400, short by 107
- fib_golden_pocket_pullback on USDJPY H4: REJECT at RUNG 0 SANITY -- min_trades: observed 274, required gte 400, short by 126
- fib_golden_pocket_pullback on XAUUSD H4: REJECT at RUNG 0 SANITY -- min_trades: observed 230, required gte 400, short by 170
- ichimoku_kumo_trend on AUDUSD H4: REJECT at RUNG 0 SANITY -- min_trades: observed 60, required gte 400, short by 340
- ichimoku_kumo_trend on DE40 H4: REJECT at RUNG 0 SANITY -- min_trades: observed 47, required gte 400, short by 353
- ichimoku_kumo_trend on EURJPY H4: REJECT at RUNG 0 SANITY -- min_trades: observed 69, required gte 400, short by 331
- ichimoku_kumo_trend on EURUSD H4: REJECT at RUNG 0 SANITY -- min_trades: observed 59, required gte 400, short by 341
- ichimoku_kumo_trend on GBPJPY H4: REJECT at RUNG 0 SANITY -- min_trades: observed 57, required gte 400, short by 343
- ichimoku_kumo_trend on GBPUSD H4: REJECT at RUNG 0 SANITY -- min_trades: observed 53, required gte 400, short by 347
- ichimoku_kumo_trend on NZDUSD H4: REJECT at RUNG 0 SANITY -- min_trades: observed 66, required gte 400, short by 334
- ichimoku_kumo_trend on US500 H4: REJECT at RUNG 0 SANITY -- min_trades: observed 43, required gte 400, short by 357
- ichimoku_kumo_trend on USDCAD H4: REJECT at RUNG 0 SANITY -- min_trades: observed 56, required gte 400, short by 344
- ichimoku_kumo_trend on USDCHF H4: REJECT at RUNG 0 SANITY -- min_trades: observed 63, required gte 400, short by 337
- ichimoku_kumo_trend on USDJPY H4: REJECT at RUNG 0 SANITY -- min_trades: observed 79, required gte 400, short by 321
- ichimoku_kumo_trend on XAUUSD H4: REJECT at RUNG 0 SANITY -- min_trades: observed 49, required gte 400, short by 351
- macd_ema_trend_hybrid on AUDUSD H4: REJECT at RUNG 0 SANITY -- min_trades: observed 49, required gte 400, short by 351
- macd_ema_trend_hybrid on DE40 H4: REJECT at RUNG 0 SANITY -- min_trades: observed 31, required gte 400, short by 369
- macd_ema_trend_hybrid on EURJPY H4: REJECT at RUNG 0 SANITY -- min_trades: observed 44, required gte 400, short by 356
- macd_ema_trend_hybrid on EURUSD H4: REJECT at RUNG 0 SANITY -- min_trades: observed 53, required gte 400, short by 347
- macd_ema_trend_hybrid on GBPJPY H4: REJECT at RUNG 0 SANITY -- min_trades: observed 53, required gte 400, short by 347
- macd_ema_trend_hybrid on GBPUSD H4: REJECT at RUNG 0 SANITY -- min_trades: observed 68, required gte 400, short by 332
- macd_ema_trend_hybrid on NZDUSD H4: REJECT at RUNG 0 SANITY -- min_trades: observed 61, required gte 400, short by 339
- macd_ema_trend_hybrid on US500 H4: REJECT at RUNG 0 SANITY -- min_trades: observed 55, required gte 400, short by 345
- macd_ema_trend_hybrid on USDCAD H4: REJECT at RUNG 0 SANITY -- min_trades: observed 60, required gte 400, short by 340
- macd_ema_trend_hybrid on USDCHF H4: REJECT at RUNG 0 SANITY -- min_trades: observed 55, required gte 400, short by 345
- macd_ema_trend_hybrid on USDJPY H4: REJECT at RUNG 0 SANITY -- min_trades: observed 44, required gte 400, short by 356
- macd_ema_trend_hybrid on XAUUSD H4: REJECT at RUNG 0 SANITY -- min_trades: observed 41, required gte 400, short by 359
- rsi_band_mean_reversion on AUDUSD H4: REJECT at RUNG 0 SANITY -- min_trades: observed 193, required gte 400, short by 207
- rsi_band_mean_reversion on EURGBP H4: REJECT at RUNG 0 SANITY -- min_trades: observed 173, required gte 400, short by 227
- rsi_band_mean_reversion on EURUSD H4: REJECT at RUNG 0 SANITY -- min_trades: observed 135, required gte 400, short by 265
- rsi_band_mean_reversion on GBPUSD H4: REJECT at RUNG 0 SANITY -- min_trades: observed 170, required gte 400, short by 230
- rsi_band_mean_reversion on NZDUSD H4: REJECT at RUNG 0 SANITY -- min_trades: observed 182, required gte 400, short by 218
- rsi_band_mean_reversion on USDCAD H4: REJECT at RUNG 0 SANITY -- min_trades: observed 137, required gte 400, short by 263
- rsi_band_mean_reversion on USDCHF H4: REJECT at RUNG 0 SANITY -- min_trades: observed 155, required gte 400, short by 245
- rsi_band_mean_reversion on USDJPY H4: REJECT at RUNG 0 SANITY -- min_trades: observed 157, required gte 400, short by 243
- donchian_breakout_atr_m3ecf77ae on AUDUSD H4: REJECT at RUNG 0 SANITY -- RUNG 0 SANITY: expectancy -1.60618 at declared defaults is not positive; there is no edge to validate.
- donchian_breakout_atr_m3ecf77ae on DE40 H4: REJECT at RUNG 0 SANITY -- min_trades: observed 358, required gte 400, short by 42
- donchian_breakout_atr_m3ecf77ae on EURJPY H4: REJECT at RUNG 0 SANITY -- RUNG 0 SANITY: expectancy -0.127253 at declared defaults is not positive; there is no edge to validate.
- donchian_breakout_atr_m3ecf77ae on EURUSD H4: REJECT at RUNG 0 SANITY -- RUNG 0 SANITY: expectancy -0.876489 at declared defaults is not positive; there is no edge to validate.
- donchian_breakout_atr_m3ecf77ae on GBPJPY H4: REJECT at RUNG 2 WALK_FORWARD -- walk_forward_efficiency: observed -7.381, required gte 50, short by 57.38
- donchian_breakout_atr_m3ecf77ae on GBPUSD H4: REJECT at RUNG 0 SANITY -- RUNG 0 SANITY: expectancy -1.11875 at declared defaults is not positive; there is no edge to validate.
- donchian_breakout_atr_m3ecf77ae on NZDUSD H4: REJECT at RUNG 0 SANITY -- RUNG 0 SANITY: expectancy -0.988952 at declared defaults is not positive; there is no edge to validate.
- donchian_breakout_atr_m3ecf77ae on US500 H4: REJECT at RUNG 0 SANITY -- RUNG 0 SANITY: expectancy -1.16526 at declared defaults is not positive; there is no edge to validate.
- donchian_breakout_atr_m3ecf77ae on USDCAD H4: REJECT at RUNG 0 SANITY -- RUNG 0 SANITY: expectancy -0.270177 at declared defaults is not positive; there is no edge to validate.
- donchian_breakout_atr_m3ecf77ae on USDCHF H4: REJECT at RUNG 0 SANITY -- RUNG 0 SANITY: expectancy -0.868505 at declared defaults is not positive; there is no edge to validate.
- donchian_breakout_atr_m3ecf77ae on USDJPY H4: REJECT at RUNG 0 SANITY -- RUNG 0 SANITY: expectancy -0.820287 at declared defaults is not positive; there is no edge to validate.
- donchian_breakout_atr_m3ecf77ae on XAUUSD H4: REJECT at RUNG 2 WALK_FORWARD -- oos_window_hit_rate: observed 0.25, required gte 0.6, short by 0.35
- donchian_breakout_atr_mbdfe4742 on AUDUSD H4: REJECT at RUNG 0 SANITY -- RUNG 0 SANITY: expectancy -1.32239 at declared defaults is not positive; there is no edge to validate.
- donchian_breakout_atr_mbdfe4742 on DE40 H4: REJECT at RUNG 2 WALK_FORWARD -- walk_forward_efficiency: observed 17.08, required gte 50, short by 32.92
- donchian_breakout_atr_mbdfe4742 on EURJPY H4: REJECT at RUNG 2 WALK_FORWARD -- walk_forward_efficiency: observed -9.31, required gte 50, short by 59.31
- donchian_breakout_atr_mbdfe4742 on EURUSD H4: REJECT at RUNG 0 SANITY -- RUNG 0 SANITY: expectancy -0.587968 at declared defaults is not positive; there is no edge to validate.
- donchian_breakout_atr_mbdfe4742 on GBPJPY H4: REJECT at RUNG 2 WALK_FORWARD -- walk_forward_efficiency: observed -20.06, required gte 50, short by 70.06
- donchian_breakout_atr_mbdfe4742 on GBPUSD H4: REJECT at RUNG 0 SANITY -- RUNG 0 SANITY: expectancy -0.714063 at declared defaults is not positive; there is no edge to validate.
- donchian_breakout_atr_mbdfe4742 on NZDUSD H4: REJECT at RUNG 0 SANITY -- RUNG 0 SANITY: expectancy -1.03238 at declared defaults is not positive; there is no edge to validate.
- donchian_breakout_atr_mbdfe4742 on US500 H4: REJECT at RUNG 0 SANITY -- RUNG 0 SANITY: expectancy -1.05527 at declared defaults is not positive; there is no edge to validate.
- donchian_breakout_atr_mbdfe4742 on USDCAD H4: REJECT at RUNG 0 SANITY -- RUNG 0 SANITY: expectancy -0.467442 at declared defaults is not positive; there is no edge to validate.
- donchian_breakout_atr_mbdfe4742 on USDCHF H4: REJECT at RUNG 0 SANITY -- RUNG 0 SANITY: expectancy -0.839418 at declared defaults is not positive; there is no edge to validate.
- donchian_breakout_atr_mbdfe4742 on USDJPY H4: REJECT at RUNG 0 SANITY -- RUNG 0 SANITY: expectancy -0.90431 at declared defaults is not positive; there is no edge to validate.
- donchian_breakout_atr_mbdfe4742 on XAUUSD H4: REJECT at RUNG 0 SANITY -- RUNG 0 SANITY: expectancy -0.0365832 at declared defaults is not positive; there is no edge to validate.
- donchian_breakout_atr_m3c934a3b on AUDUSD H4: REJECT at RUNG 0 SANITY -- RUNG 0 SANITY: expectancy -1.63661 at declared defaults is not positive; there is no edge to validate.
- donchian_breakout_atr_m3c934a3b on DE40 H4: REJECT at RUNG 0 SANITY -- min_trades: observed 380, required gte 400, short by 20
- donchian_breakout_atr_m3c934a3b on EURJPY H4: REJECT at RUNG 0 SANITY -- RUNG 0 SANITY: expectancy -0.167418 at declared defaults is not positive; there is no edge to validate.
- donchian_breakout_atr_m3c934a3b on EURUSD H4: REJECT at RUNG 0 SANITY -- RUNG 0 SANITY: expectancy -0.744554 at declared defaults is not positive; there is no edge to validate.
- donchian_breakout_atr_m3c934a3b on GBPJPY H4: REJECT at RUNG 2 WALK_FORWARD -- walk_forward_efficiency: observed 42.53, required gte 50, short by 7.471
- donchian_breakout_atr_m3c934a3b on GBPUSD H4: REJECT at RUNG 0 SANITY -- RUNG 0 SANITY: expectancy -0.911907 at declared defaults is not positive; there is no edge to validate.
- donchian_breakout_atr_m3c934a3b on NZDUSD H4: REJECT at RUNG 0 SANITY -- RUNG 0 SANITY: expectancy -0.615493 at declared defaults is not positive; there is no edge to validate.
- donchian_breakout_atr_m3c934a3b on US500 H4: REJECT at RUNG 0 SANITY -- RUNG 0 SANITY: expectancy -1.02426 at declared defaults is not positive; there is no edge to validate.
- donchian_breakout_atr_m3c934a3b on USDCAD H4: REJECT at RUNG 0 SANITY -- RUNG 0 SANITY: expectancy -0.165885 at declared defaults is not positive; there is no edge to validate.
- donchian_breakout_atr_m3c934a3b on USDCHF H4: REJECT at RUNG 0 SANITY -- RUNG 0 SANITY: expectancy -0.447282 at declared defaults is not positive; there is no edge to validate.
- donchian_breakout_atr_m3c934a3b on USDJPY H4: REJECT at RUNG 0 SANITY -- RUNG 0 SANITY: expectancy -0.866054 at declared defaults is not positive; there is no edge to validate.
- donchian_breakout_atr_m3c934a3b on XAUUSD H4: REJECT at RUNG 0 SANITY -- RUNG 0 SANITY: expectancy -0.634884 at declared defaults is not positive; there is no edge to validate.
- donchian_breakout_atr_m76dbb4f7 on AUDUSD H4: REJECT at RUNG 0 SANITY -- RUNG 0 SANITY: expectancy -0.732281 at declared defaults is not positive; there is no edge to validate.
- donchian_breakout_atr_m76dbb4f7 on DE40 H4: REJECT at RUNG 0 SANITY -- min_trades: observed 262, required gte 400, short by 138
- donchian_breakout_atr_m76dbb4f7 on EURJPY H4: REJECT at RUNG 0 SANITY -- RUNG 0 SANITY: expectancy -0.805452 at declared defaults is not positive; there is no edge to validate.
- donchian_breakout_atr_m76dbb4f7 on EURUSD H4: REJECT at RUNG 0 SANITY -- RUNG 0 SANITY: expectancy -0.99483 at declared defaults is not positive; there is no edge to validate.
- donchian_breakout_atr_m76dbb4f7 on GBPJPY H4: REJECT at RUNG 0 SANITY -- RUNG 0 SANITY: expectancy -0.0476694 at declared defaults is not positive; there is no edge to validate.
- donchian_breakout_atr_m76dbb4f7 on GBPUSD H4: REJECT at RUNG 0 SANITY -- RUNG 0 SANITY: expectancy -0.844291 at declared defaults is not positive; there is no edge to validate.
- donchian_breakout_atr_m76dbb4f7 on NZDUSD H4: REJECT at RUNG 0 SANITY -- RUNG 0 SANITY: expectancy -1.36594 at declared defaults is not positive; there is no edge to validate.
- donchian_breakout_atr_m76dbb4f7 on US500 H4: REJECT at RUNG 0 SANITY -- RUNG 0 SANITY: expectancy -1.1682 at declared defaults is not positive; there is no edge to validate.
- donchian_breakout_atr_m76dbb4f7 on USDCAD H4: REJECT at RUNG 0 SANITY -- RUNG 0 SANITY: expectancy -0.994088 at declared defaults is not positive; there is no edge to validate.
- donchian_breakout_atr_m76dbb4f7 on USDCHF H4: REJECT at RUNG 0 SANITY -- RUNG 0 SANITY: expectancy -1.0276 at declared defaults is not positive; there is no edge to validate.
- donchian_breakout_atr_m76dbb4f7 on USDJPY H4: REJECT at RUNG 0 SANITY -- RUNG 0 SANITY: expectancy -1.13915 at declared defaults is not positive; there is no edge to validate.
- donchian_breakout_atr_m76dbb4f7 on XAUUSD H4: REJECT at RUNG 2 WALK_FORWARD -- walk_forward_efficiency: observed nan, required gte 50
- donchian_breakout_atr_m427fba0e on AUDUSD H4: REJECT at RUNG 0 SANITY -- RUNG 0 SANITY: expectancy -1.01575 at declared defaults is not positive; there is no edge to validate.
- donchian_breakout_atr_m427fba0e on DE40 H4: REJECT at RUNG 0 SANITY -- min_trades: observed 208, required gte 400, short by 192
- donchian_breakout_atr_m427fba0e on EURJPY H4: REJECT at RUNG 0 SANITY -- min_trades: observed 370, required gte 400, short by 30
- donchian_breakout_atr_m427fba0e on EURUSD H4: REJECT at RUNG 0 SANITY -- min_trades: observed 367, required gte 400, short by 33
- donchian_breakout_atr_m427fba0e on GBPJPY H4: REJECT at RUNG 0 SANITY -- min_trades: observed 391, required gte 400, short by 9
- donchian_breakout_atr_m427fba0e on GBPUSD H4: REJECT at RUNG 0 SANITY -- min_trades: observed 386, required gte 400, short by 14
- donchian_breakout_atr_m427fba0e on NZDUSD H4: REJECT at RUNG 0 SANITY -- RUNG 0 SANITY: expectancy -1.51589 at declared defaults is not positive; there is no edge to validate.
- donchian_breakout_atr_m427fba0e on US500 H4: REJECT at RUNG 0 SANITY -- min_trades: observed 382, required gte 400, short by 18
- donchian_breakout_atr_m427fba0e on USDCAD H4: REJECT at RUNG 0 SANITY -- min_trades: observed 379, required gte 400, short by 21
- donchian_breakout_atr_m427fba0e on USDCHF H4: REJECT at RUNG 0 SANITY -- min_trades: observed 383, required gte 400, short by 17
- donchian_breakout_atr_m427fba0e on USDJPY H4: REJECT at RUNG 0 SANITY -- min_trades: observed 340, required gte 400, short by 60
- donchian_breakout_atr_m427fba0e on XAUUSD H4: REJECT at RUNG 4 ROBUSTNESS -- parameter_plateau: observed inf, required lte 1.25
- donchian_breakout_atr_m79f18824 on AUDUSD H4: REJECT at RUNG 0 SANITY -- RUNG 0 SANITY: expectancy -1.60618 at declared defaults is not positive; there is no edge to validate.
- donchian_breakout_atr_m79f18824 on DE40 H4: REJECT at RUNG 0 SANITY -- min_trades: observed 358, required gte 400, short by 42
- donchian_breakout_atr_m79f18824 on EURJPY H4: REJECT at RUNG 0 SANITY -- RUNG 0 SANITY: expectancy -0.127253 at declared defaults is not positive; there is no edge to validate.
- donchian_breakout_atr_m79f18824 on EURUSD H4: REJECT at RUNG 0 SANITY -- RUNG 0 SANITY: expectancy -0.876489 at declared defaults is not positive; there is no edge to validate.
- donchian_breakout_atr_m79f18824 on GBPJPY H4: REJECT at RUNG 2 WALK_FORWARD -- walk_forward_efficiency: observed -6.292, required gte 50, short by 56.29
- donchian_breakout_atr_m79f18824 on GBPUSD H4: REJECT at RUNG 0 SANITY -- RUNG 0 SANITY: expectancy -1.11875 at declared defaults is not positive; there is no edge to validate.
- donchian_breakout_atr_m79f18824 on NZDUSD H4: REJECT at RUNG 0 SANITY -- RUNG 0 SANITY: expectancy -0.988952 at declared defaults is not positive; there is no edge to validate.
- donchian_breakout_atr_m79f18824 on US500 H4: REJECT at RUNG 0 SANITY -- RUNG 0 SANITY: expectancy -1.16526 at declared defaults is not positive; there is no edge to validate.
- donchian_breakout_atr_m79f18824 on USDCAD H4: REJECT at RUNG 0 SANITY -- RUNG 0 SANITY: expectancy -0.270177 at declared defaults is not positive; there is no edge to validate.
- donchian_breakout_atr_m79f18824 on USDCHF H4: REJECT at RUNG 0 SANITY -- RUNG 0 SANITY: expectancy -0.868505 at declared defaults is not positive; there is no edge to validate.
- donchian_breakout_atr_m79f18824 on USDJPY H4: REJECT at RUNG 0 SANITY -- RUNG 0 SANITY: expectancy -0.820287 at declared defaults is not positive; there is no edge to validate.
- donchian_breakout_atr_m79f18824 on XAUUSD H4: REJECT at RUNG 2 WALK_FORWARD -- oos_window_hit_rate: observed 0.25, required gte 0.6, short by 0.35
- donchian_breakout_atr_mf2553d34 on AUDUSD H4: REJECT at RUNG 0 SANITY -- RUNG 0 SANITY: expectancy -1.60625 at declared defaults is not positive; there is no edge to validate.
- donchian_breakout_atr_mf2553d34 on DE40 H4: REJECT at RUNG 0 SANITY -- RUNG 0 SANITY: expectancy -0.32285 at declared defaults is not positive; there is no edge to validate.
- donchian_breakout_atr_mf2553d34 on EURJPY H4: REJECT at RUNG 0 SANITY -- RUNG 0 SANITY: expectancy -0.150925 at declared defaults is not positive; there is no edge to validate.
- donchian_breakout_atr_mf2553d34 on EURUSD H4: REJECT at RUNG 0 SANITY -- RUNG 0 SANITY: expectancy -1.14951 at declared defaults is not positive; there is no edge to validate.
- donchian_breakout_atr_mf2553d34 on GBPJPY H4: REJECT at RUNG 2 WALK_FORWARD -- walk_forward_efficiency: observed -18.48, required gte 50, short by 68.48
- donchian_breakout_atr_mf2553d34 on GBPUSD H4: REJECT at RUNG 0 SANITY -- RUNG 0 SANITY: expectancy -1.08977 at declared defaults is not positive; there is no edge to validate.
- donchian_breakout_atr_mf2553d34 on NZDUSD H4: REJECT at RUNG 0 SANITY -- RUNG 0 SANITY: expectancy -0.892722 at declared defaults is not positive; there is no edge to validate.
- donchian_breakout_atr_mf2553d34 on US500 H4: REJECT at RUNG 0 SANITY -- RUNG 0 SANITY: expectancy -1.30608 at declared defaults is not positive; there is no edge to validate.
- donchian_breakout_atr_mf2553d34 on USDCAD H4: REJECT at RUNG 0 SANITY -- RUNG 0 SANITY: expectancy -0.251849 at declared defaults is not positive; there is no edge to validate.
- donchian_breakout_atr_mf2553d34 on USDCHF H4: REJECT at RUNG 0 SANITY -- RUNG 0 SANITY: expectancy -0.474063 at declared defaults is not positive; there is no edge to validate.
- donchian_breakout_atr_mf2553d34 on USDJPY H4: REJECT at RUNG 0 SANITY -- RUNG 0 SANITY: expectancy -0.892337 at declared defaults is not positive; there is no edge to validate.
- donchian_breakout_atr_mf2553d34 on XAUUSD H4: REJECT at RUNG 0 SANITY -- RUNG 0 SANITY: expectancy -0.00400073 at declared defaults is not positive; there is no edge to validate.
- donchian_breakout_atr_m30945c80 on AUDUSD H4: REJECT at RUNG 0 SANITY -- RUNG 0 SANITY: expectancy -1.06375 at declared defaults is not positive; there is no edge to validate.
- donchian_breakout_atr_m30945c80 on DE40 H4: REJECT at RUNG 0 SANITY -- min_trades: observed 328, required gte 400, short by 72
- donchian_breakout_atr_m30945c80 on EURJPY H4: REJECT at RUNG 0 SANITY -- RUNG 0 SANITY: expectancy -1.44155 at declared defaults is not positive; there is no edge to validate.
- donchian_breakout_atr_m30945c80 on EURUSD H4: REJECT at RUNG 1 IN_SAMPLE_SCREEN -- RUNG 1 IN_SAMPLE_SCREEN: no parameterisation inside the domains the document declares is positive in sample on sharpe; there is nothing for the later rungs to test.
- donchian_breakout_atr_m30945c80 on GBPJPY H4: REJECT at RUNG 1 IN_SAMPLE_SCREEN -- RUNG 1 IN_SAMPLE_SCREEN: no parameterisation inside the domains the document declares is positive in sample on sharpe; there is nothing for the later rungs to test.
- donchian_breakout_atr_m30945c80 on GBPUSD H4: REJECT at RUNG 0 SANITY -- RUNG 0 SANITY: expectancy -1.82054 at declared defaults is not positive; there is no edge to validate.
- donchian_breakout_atr_m30945c80 on NZDUSD H4: REJECT at RUNG 0 SANITY -- min_trades: observed 347, required gte 400, short by 53
- donchian_breakout_atr_m30945c80 on US500 H4: REJECT at RUNG 0 SANITY -- RUNG 0 SANITY: expectancy -1.59801 at declared defaults is not positive; there is no edge to validate.
- donchian_breakout_atr_m30945c80 on USDCAD H4: REJECT at RUNG 0 SANITY -- RUNG 0 SANITY: expectancy -0.949037 at declared defaults is not positive; there is no edge to validate.
- donchian_breakout_atr_m30945c80 on USDCHF H4: REJECT at RUNG 0 SANITY -- RUNG 0 SANITY: expectancy -2.1008 at declared defaults is not positive; there is no edge to validate.
- donchian_breakout_atr_m30945c80 on USDJPY H4: REJECT at RUNG 0 SANITY -- RUNG 0 SANITY: expectancy -1.27183 at declared defaults is not positive; there is no edge to validate.
- donchian_breakout_atr_m30945c80 on XAUUSD H4: REJECT at RUNG 0 SANITY -- RUNG 0 SANITY: expectancy -1.39978 at declared defaults is not positive; there is no edge to validate.
- donchian_breakout_atr_m217dad55 on AUDUSD H4: REJECT at RUNG 0 SANITY -- RUNG 0 SANITY: expectancy -1.37811 at declared defaults is not positive; there is no edge to validate.
- donchian_breakout_atr_m217dad55 on DE40 H4: REJECT at RUNG 0 SANITY -- min_trades: observed 226, required gte 400, short by 174
- donchian_breakout_atr_m217dad55 on EURJPY H4: REJECT at RUNG 0 SANITY -- RUNG 0 SANITY: expectancy -0.025867 at declared defaults is not positive; there is no edge to validate.
- donchian_breakout_atr_m217dad55 on EURUSD H4: REJECT at RUNG 0 SANITY -- RUNG 0 SANITY: expectancy -0.939387 at declared defaults is not positive; there is no edge to validate.
- donchian_breakout_atr_m217dad55 on GBPJPY H4: REJECT at RUNG 5 DEFLATION -- deflated_sharpe: observed 0.001595, required gt 0.95, short by 0.9484
- donchian_breakout_atr_m217dad55 on GBPUSD H4: REJECT at RUNG 0 SANITY -- RUNG 0 SANITY: expectancy -0.595776 at declared defaults is not positive; there is no edge to validate.
- donchian_breakout_atr_m217dad55 on NZDUSD H4: REJECT at RUNG 0 SANITY -- RUNG 0 SANITY: expectancy -1.17934 at declared defaults is not positive; there is no edge to validate.
- donchian_breakout_atr_m217dad55 on US500 H4: REJECT at RUNG 0 SANITY -- RUNG 0 SANITY: expectancy -1.35017 at declared defaults is not positive; there is no edge to validate.
- donchian_breakout_atr_m217dad55 on USDCAD H4: REJECT at RUNG 0 SANITY -- RUNG 0 SANITY: expectancy -0.442095 at declared defaults is not positive; there is no edge to validate.
- donchian_breakout_atr_m217dad55 on USDCHF H4: REJECT at RUNG 0 SANITY -- RUNG 0 SANITY: expectancy -0.641693 at declared defaults is not positive; there is no edge to validate.
- donchian_breakout_atr_m217dad55 on USDJPY H4: REJECT at RUNG 0 SANITY -- RUNG 0 SANITY: expectancy -0.982335 at declared defaults is not positive; there is no edge to validate.
- donchian_breakout_atr_m217dad55 on XAUUSD H4: REJECT at RUNG 0 SANITY -- RUNG 0 SANITY: expectancy -0.0904837 at declared defaults is not positive; there is no edge to validate.
- donchian_breakout_atr_m4d42994e on AUDUSD H4: REJECT at RUNG 0 SANITY -- RUNG 0 SANITY: expectancy -1.05859 at declared defaults is not positive; there is no edge to validate.
- donchian_breakout_atr_m4d42994e on EURUSD H4: REJECT at RUNG 0 SANITY -- RUNG 0 SANITY: expectancy -0.482876 at declared defaults is not positive; there is no edge to validate.
- donchian_breakout_atr_m4d42994e on GBPUSD H4: REJECT at RUNG 0 SANITY -- RUNG 0 SANITY: expectancy -1.83338 at declared defaults is not positive; there is no edge to validate.
- donchian_breakout_atr_m4d42994e on NZDUSD H4: REJECT at RUNG 0 SANITY -- RUNG 0 SANITY: expectancy -0.928092 at declared defaults is not positive; there is no edge to validate.
- donchian_breakout_atr_m4d42994e on USDCAD H4: REJECT at RUNG 0 SANITY -- RUNG 0 SANITY: expectancy -0.870538 at declared defaults is not positive; there is no edge to validate.
- donchian_breakout_atr_m4d42994e on USDCHF H4: REJECT at RUNG 0 SANITY -- RUNG 0 SANITY: expectancy -1.13979 at declared defaults is not positive; there is no edge to validate.
- donchian_breakout_atr_m4d42994e on USDJPY H4: REJECT at RUNG 0 SANITY -- RUNG 0 SANITY: expectancy -1.04136 at declared defaults is not positive; there is no edge to validate.
- fib_golden_pocket_pullback_ma8f9f32b on AUDUSD H4: REJECT at RUNG 0 SANITY -- min_trades: observed 278, required gte 400, short by 122
- fib_golden_pocket_pullback_ma8f9f32b on DE40 H4: REJECT at RUNG 0 SANITY -- min_trades: observed 193, required gte 400, short by 207
- fib_golden_pocket_pullback_ma8f9f32b on EURJPY H4: REJECT at RUNG 0 SANITY -- min_trades: observed 273, required gte 400, short by 127
- fib_golden_pocket_pullback_ma8f9f32b on EURUSD H4: REJECT at RUNG 0 SANITY -- min_trades: observed 261, required gte 400, short by 139
- fib_golden_pocket_pullback_ma8f9f32b on GBPJPY H4: REJECT at RUNG 0 SANITY -- min_trades: observed 279, required gte 400, short by 121
- fib_golden_pocket_pullback_ma8f9f32b on GBPUSD H4: REJECT at RUNG 0 SANITY -- min_trades: observed 272, required gte 400, short by 128
- fib_golden_pocket_pullback_ma8f9f32b on NZDUSD H4: REJECT at RUNG 0 SANITY -- min_trades: observed 257, required gte 400, short by 143
- fib_golden_pocket_pullback_ma8f9f32b on US500 H4: REJECT at RUNG 0 SANITY -- min_trades: observed 250, required gte 400, short by 150
- fib_golden_pocket_pullback_ma8f9f32b on USDCAD H4: REJECT at RUNG 0 SANITY -- min_trades: observed 295, required gte 400, short by 105
- fib_golden_pocket_pullback_ma8f9f32b on USDCHF H4: REJECT at RUNG 0 SANITY -- min_trades: observed 293, required gte 400, short by 107
- fib_golden_pocket_pullback_ma8f9f32b on USDJPY H4: REJECT at RUNG 0 SANITY -- min_trades: observed 274, required gte 400, short by 126
- fib_golden_pocket_pullback_ma8f9f32b on XAUUSD H4: REJECT at RUNG 0 SANITY -- min_trades: observed 230, required gte 400, short by 170
- fib_golden_pocket_pullback_md15c64ac on AUDUSD H4: REJECT at RUNG 0 SANITY -- min_trades: observed 260, required gte 400, short by 140
- fib_golden_pocket_pullback_md15c64ac on DE40 H4: REJECT at RUNG 0 SANITY -- min_trades: observed 207, required gte 400, short by 193
- fib_golden_pocket_pullback_md15c64ac on EURJPY H4: REJECT at RUNG 0 SANITY -- min_trades: observed 235, required gte 400, short by 165
- fib_golden_pocket_pullback_md15c64ac on EURUSD H4: REJECT at RUNG 0 SANITY -- min_trades: observed 220, required gte 400, short by 180
- fib_golden_pocket_pullback_md15c64ac on GBPJPY H4: REJECT at RUNG 0 SANITY -- min_trades: observed 252, required gte 400, short by 148
- fib_golden_pocket_pullback_md15c64ac on GBPUSD H4: REJECT at RUNG 0 SANITY -- min_trades: observed 237, required gte 400, short by 163
- fib_golden_pocket_pullback_md15c64ac on NZDUSD H4: REJECT at RUNG 0 SANITY -- min_trades: observed 234, required gte 400, short by 166
- fib_golden_pocket_pullback_md15c64ac on US500 H4: REJECT at RUNG 0 SANITY -- min_trades: observed 228, required gte 400, short by 172
- fib_golden_pocket_pullback_md15c64ac on USDCAD H4: REJECT at RUNG 0 SANITY -- min_trades: observed 248, required gte 400, short by 152
- fib_golden_pocket_pullback_md15c64ac on USDCHF H4: REJECT at RUNG 0 SANITY -- min_trades: observed 239, required gte 400, short by 161
- fib_golden_pocket_pullback_md15c64ac on USDJPY H4: REJECT at RUNG 0 SANITY -- min_trades: observed 236, required gte 400, short by 164
- fib_golden_pocket_pullback_md15c64ac on XAUUSD H4: REJECT at RUNG 0 SANITY -- min_trades: observed 214, required gte 400, short by 186
- fib_golden_pocket_pullback_mb82cde01 on AUDUSD H4: REJECT at RUNG 0 SANITY -- min_trades: observed 270, required gte 400, short by 130
- fib_golden_pocket_pullback_mb82cde01 on DE40 H4: REJECT at RUNG 0 SANITY -- min_trades: observed 179, required gte 400, short by 221
- fib_golden_pocket_pullback_mb82cde01 on EURJPY H4: REJECT at RUNG 0 SANITY -- min_trades: observed 268, required gte 400, short by 132
- fib_golden_pocket_pullback_mb82cde01 on EURUSD H4: REJECT at RUNG 0 SANITY -- min_trades: observed 253, required gte 400, short by 147
- fib_golden_pocket_pullback_mb82cde01 on GBPJPY H4: REJECT at RUNG 0 SANITY -- min_trades: observed 274, required gte 400, short by 126
- fib_golden_pocket_pullback_mb82cde01 on GBPUSD H4: REJECT at RUNG 0 SANITY -- min_trades: observed 262, required gte 400, short by 138
- fib_golden_pocket_pullback_mb82cde01 on NZDUSD H4: REJECT at RUNG 0 SANITY -- min_trades: observed 244, required gte 400, short by 156
- fib_golden_pocket_pullback_mb82cde01 on US500 H4: REJECT at RUNG 0 SANITY -- min_trades: observed 236, required gte 400, short by 164
- fib_golden_pocket_pullback_mb82cde01 on USDCAD H4: REJECT at RUNG 0 SANITY -- min_trades: observed 277, required gte 400, short by 123
- fib_golden_pocket_pullback_mb82cde01 on USDCHF H4: REJECT at RUNG 0 SANITY -- min_trades: observed 283, required gte 400, short by 117
- fib_golden_pocket_pullback_mb82cde01 on USDJPY H4: REJECT at RUNG 0 SANITY -- min_trades: observed 259, required gte 400, short by 141
- fib_golden_pocket_pullback_mb82cde01 on XAUUSD H4: REJECT at RUNG 0 SANITY -- min_trades: observed 218, required gte 400, short by 182
- fib_golden_pocket_pullback_mdd2357fa on AUDUSD H4: REJECT at RUNG 0 SANITY -- RUNG 0 SANITY: expectancy -0.708947 at declared defaults is not positive; there is no edge to validate.
- fib_golden_pocket_pullback_mdd2357fa on DE40 H4: REJECT at RUNG 0 SANITY -- min_trades: observed 271, required gte 400, short by 129
- fib_golden_pocket_pullback_mdd2357fa on EURJPY H4: REJECT at RUNG 0 SANITY -- RUNG 0 SANITY: expectancy -1.29393 at declared defaults is not positive; there is no edge to validate.
- fib_golden_pocket_pullback_mdd2357fa on EURUSD H4: REJECT at RUNG 0 SANITY -- min_trades: observed 388, required gte 400, short by 12
- fib_golden_pocket_pullback_mdd2357fa on GBPJPY H4: REJECT at RUNG 0 SANITY -- min_trades: observed 389, required gte 400, short by 11
- fib_golden_pocket_pullback_mdd2357fa on GBPUSD H4: REJECT at RUNG 0 SANITY -- min_trades: observed 377, required gte 400, short by 23
- fib_golden_pocket_pullback_mdd2357fa on NZDUSD H4: REJECT at RUNG 0 SANITY -- min_trades: observed 381, required gte 400, short by 19
- fib_golden_pocket_pullback_mdd2357fa on US500 H4: REJECT at RUNG 0 SANITY -- min_trades: observed 333, required gte 400, short by 67
- fib_golden_pocket_pullback_mdd2357fa on USDCAD H4: REJECT at RUNG 0 SANITY -- RUNG 0 SANITY: expectancy -0.910511 at declared defaults is not positive; there is no edge to validate.
- fib_golden_pocket_pullback_mdd2357fa on USDCHF H4: REJECT at RUNG 0 SANITY -- RUNG 0 SANITY: expectancy -0.934842 at declared defaults is not positive; there is no edge to validate.
- fib_golden_pocket_pullback_mdd2357fa on USDJPY H4: REJECT at RUNG 0 SANITY -- min_trades: observed 387, required gte 400, short by 13
- fib_golden_pocket_pullback_mdd2357fa on XAUUSD H4: REJECT at RUNG 0 SANITY -- min_trades: observed 308, required gte 400, short by 92
- fib_golden_pocket_pullback_md3507b8f on AUDUSD H4: REJECT at RUNG 0 SANITY -- RUNG 0 SANITY: expectancy -0.848867 at declared defaults is not positive; there is no edge to validate.
- fib_golden_pocket_pullback_md3507b8f on DE40 H4: REJECT at RUNG 0 SANITY -- min_trades: observed 249, required gte 400, short by 151
- fib_golden_pocket_pullback_md3507b8f on EURJPY H4: REJECT at RUNG 0 SANITY -- RUNG 0 SANITY: expectancy -0.79112 at declared defaults is not positive; there is no edge to validate.
- fib_golden_pocket_pullback_md3507b8f on EURUSD H4: REJECT at RUNG 0 SANITY -- RUNG 0 SANITY: expectancy -0.83356 at declared defaults is not positive; there is no edge to validate.
- fib_golden_pocket_pullback_md3507b8f on GBPJPY H4: REJECT at RUNG 0 SANITY -- RUNG 0 SANITY: expectancy -1.51138 at declared defaults is not positive; there is no edge to validate.
- fib_golden_pocket_pullback_md3507b8f on GBPUSD H4: REJECT at RUNG 0 SANITY -- RUNG 0 SANITY: expectancy -1.3099 at declared defaults is not positive; there is no edge to validate.
- fib_golden_pocket_pullback_md3507b8f on NZDUSD H4: REJECT at RUNG 0 SANITY -- RUNG 0 SANITY: expectancy -1.21268 at declared defaults is not positive; there is no edge to validate.
- fib_golden_pocket_pullback_md3507b8f on US500 H4: REJECT at RUNG 0 SANITY -- min_trades: observed 368, required gte 400, short by 32
- fib_golden_pocket_pullback_md3507b8f on USDCAD H4: REJECT at RUNG 0 SANITY -- RUNG 0 SANITY: expectancy -0.653795 at declared defaults is not positive; there is no edge to validate.
- fib_golden_pocket_pullback_md3507b8f on USDCHF H4: REJECT at RUNG 0 SANITY -- RUNG 0 SANITY: expectancy -1.17594 at declared defaults is not positive; there is no edge to validate.
- fib_golden_pocket_pullback_md3507b8f on USDJPY H4: REJECT at RUNG 0 SANITY -- min_trades: observed 341, required gte 400, short by 59
- fib_golden_pocket_pullback_md3507b8f on XAUUSD H4: REJECT at RUNG 0 SANITY -- min_trades: observed 375, required gte 400, short by 25
- fib_golden_pocket_pullback_m88fd2d1c on AUDUSD H4: REJECT at RUNG 0 SANITY -- min_trades: observed 62, required gte 400, short by 338
- fib_golden_pocket_pullback_m88fd2d1c on DE40 H4: REJECT at RUNG 0 SANITY -- min_trades: observed 52, required gte 400, short by 348
- fib_golden_pocket_pullback_m88fd2d1c on EURJPY H4: REJECT at RUNG 0 SANITY -- min_trades: observed 54, required gte 400, short by 346
- fib_golden_pocket_pullback_m88fd2d1c on EURUSD H4: REJECT at RUNG 0 SANITY -- min_trades: observed 61, required gte 400, short by 339
- fib_golden_pocket_pullback_m88fd2d1c on GBPJPY H4: REJECT at RUNG 0 SANITY -- min_trades: observed 70, required gte 400, short by 330
- fib_golden_pocket_pullback_m88fd2d1c on GBPUSD H4: REJECT at RUNG 0 SANITY -- min_trades: observed 64, required gte 400, short by 336
- fib_golden_pocket_pullback_m88fd2d1c on NZDUSD H4: REJECT at RUNG 0 SANITY -- min_trades: observed 61, required gte 400, short by 339
- fib_golden_pocket_pullback_m88fd2d1c on US500 H4: REJECT at RUNG 0 SANITY -- min_trades: observed 66, required gte 400, short by 334
- fib_golden_pocket_pullback_m88fd2d1c on USDCAD H4: REJECT at RUNG 0 SANITY -- min_trades: observed 68, required gte 400, short by 332
- fib_golden_pocket_pullback_m88fd2d1c on USDCHF H4: REJECT at RUNG 0 SANITY -- min_trades: observed 68, required gte 400, short by 332
- fib_golden_pocket_pullback_m88fd2d1c on USDJPY H4: REJECT at RUNG 0 SANITY -- min_trades: observed 60, required gte 400, short by 340
- fib_golden_pocket_pullback_m88fd2d1c on XAUUSD H4: REJECT at RUNG 0 SANITY -- min_trades: observed 49, required gte 400, short by 351
- fib_golden_pocket_pullback_me01e2d1a on AUDUSD H4: REJECT at RUNG 0 SANITY -- min_trades: observed 278, required gte 400, short by 122
- fib_golden_pocket_pullback_me01e2d1a on DE40 H4: REJECT at RUNG 0 SANITY -- min_trades: observed 193, required gte 400, short by 207
- fib_golden_pocket_pullback_me01e2d1a on EURJPY H4: REJECT at RUNG 0 SANITY -- min_trades: observed 273, required gte 400, short by 127
- fib_golden_pocket_pullback_me01e2d1a on EURUSD H4: REJECT at RUNG 0 SANITY -- min_trades: observed 261, required gte 400, short by 139
- fib_golden_pocket_pullback_me01e2d1a on GBPJPY H4: REJECT at RUNG 0 SANITY -- min_trades: observed 279, required gte 400, short by 121
- fib_golden_pocket_pullback_me01e2d1a on GBPUSD H4: REJECT at RUNG 0 SANITY -- min_trades: observed 272, required gte 400, short by 128
- fib_golden_pocket_pullback_me01e2d1a on NZDUSD H4: REJECT at RUNG 0 SANITY -- min_trades: observed 257, required gte 400, short by 143
- fib_golden_pocket_pullback_me01e2d1a on US500 H4: REJECT at RUNG 0 SANITY -- min_trades: observed 250, required gte 400, short by 150
- fib_golden_pocket_pullback_me01e2d1a on USDCAD H4: REJECT at RUNG 0 SANITY -- min_trades: observed 295, required gte 400, short by 105
- fib_golden_pocket_pullback_me01e2d1a on USDCHF H4: REJECT at RUNG 0 SANITY -- min_trades: observed 293, required gte 400, short by 107
- fib_golden_pocket_pullback_me01e2d1a on USDJPY H4: REJECT at RUNG 0 SANITY -- min_trades: observed 274, required gte 400, short by 126
- fib_golden_pocket_pullback_me01e2d1a on XAUUSD H4: REJECT at RUNG 0 SANITY -- min_trades: observed 230, required gte 400, short by 170
- fib_golden_pocket_pullback_m76354e7e on AUDUSD H4: REJECT at RUNG 0 SANITY -- min_trades: observed 268, required gte 400, short by 132
- fib_golden_pocket_pullback_m76354e7e on DE40 H4: REJECT at RUNG 0 SANITY -- min_trades: observed 159, required gte 400, short by 241
- fib_golden_pocket_pullback_m76354e7e on EURJPY H4: REJECT at RUNG 0 SANITY -- min_trades: observed 260, required gte 400, short by 140
- fib_golden_pocket_pullback_m76354e7e on EURUSD H4: REJECT at RUNG 0 SANITY -- min_trades: observed 258, required gte 400, short by 142
- fib_golden_pocket_pullback_m76354e7e on GBPJPY H4: REJECT at RUNG 0 SANITY -- min_trades: observed 273, required gte 400, short by 127
- fib_golden_pocket_pullback_m76354e7e on GBPUSD H4: REJECT at RUNG 0 SANITY -- min_trades: observed 261, required gte 400, short by 139
- fib_golden_pocket_pullback_m76354e7e on NZDUSD H4: REJECT at RUNG 0 SANITY -- min_trades: observed 236, required gte 400, short by 164
- fib_golden_pocket_pullback_m76354e7e on US500 H4: REJECT at RUNG 0 SANITY -- min_trades: observed 240, required gte 400, short by 160
- fib_golden_pocket_pullback_m76354e7e on USDCAD H4: REJECT at RUNG 0 SANITY -- min_trades: observed 274, required gte 400, short by 126
- fib_golden_pocket_pullback_m76354e7e on USDCHF H4: REJECT at RUNG 0 SANITY -- min_trades: observed 273, required gte 400, short by 127
- fib_golden_pocket_pullback_m76354e7e on USDJPY H4: REJECT at RUNG 0 SANITY -- min_trades: observed 257, required gte 400, short by 143
- fib_golden_pocket_pullback_m76354e7e on XAUUSD H4: REJECT at RUNG 0 SANITY -- min_trades: observed 217, required gte 400, short by 183
- fib_golden_pocket_pullback_mb73e28fd on AUDUSD H4: REJECT at RUNG 0 SANITY -- min_trades: observed 247, required gte 400, short by 153
- fib_golden_pocket_pullback_mb73e28fd on DE40 H4: REJECT at RUNG 0 SANITY -- min_trades: observed 120, required gte 400, short by 280
- fib_golden_pocket_pullback_mb73e28fd on EURJPY H4: REJECT at RUNG 0 SANITY -- min_trades: observed 240, required gte 400, short by 160
- fib_golden_pocket_pullback_mb73e28fd on EURUSD H4: REJECT at RUNG 0 SANITY -- min_trades: observed 238, required gte 400, short by 162
- fib_golden_pocket_pullback_mb73e28fd on GBPJPY H4: REJECT at RUNG 0 SANITY -- min_trades: observed 251, required gte 400, short by 149
- fib_golden_pocket_pullback_mb73e28fd on GBPUSD H4: REJECT at RUNG 0 SANITY -- min_trades: observed 247, required gte 400, short by 153
- fib_golden_pocket_pullback_mb73e28fd on NZDUSD H4: REJECT at RUNG 0 SANITY -- min_trades: observed 215, required gte 400, short by 185
- fib_golden_pocket_pullback_mb73e28fd on US500 H4: REJECT at RUNG 0 SANITY -- min_trades: observed 210, required gte 400, short by 190
- fib_golden_pocket_pullback_mb73e28fd on USDCAD H4: REJECT at RUNG 0 SANITY -- min_trades: observed 256, required gte 400, short by 144
- fib_golden_pocket_pullback_mb73e28fd on USDCHF H4: REJECT at RUNG 0 SANITY -- min_trades: observed 254, required gte 400, short by 146
- fib_golden_pocket_pullback_mb73e28fd on USDJPY H4: REJECT at RUNG 0 SANITY -- min_trades: observed 233, required gte 400, short by 167
- fib_golden_pocket_pullback_mb73e28fd on XAUUSD H4: REJECT at RUNG 0 SANITY -- min_trades: observed 200, required gte 400, short by 200
- ichimoku_kumo_trend_m689694c1 on AUDUSD H4: REJECT at RUNG 0 SANITY -- min_trades: observed 60, required gte 400, short by 340
- ichimoku_kumo_trend_m689694c1 on DE40 H4: REJECT at RUNG 0 SANITY -- min_trades: observed 46, required gte 400, short by 354
- ichimoku_kumo_trend_m689694c1 on EURJPY H4: REJECT at RUNG 0 SANITY -- min_trades: observed 68, required gte 400, short by 332
- ichimoku_kumo_trend_m689694c1 on EURUSD H4: REJECT at RUNG 0 SANITY -- min_trades: observed 59, required gte 400, short by 341
- ichimoku_kumo_trend_m689694c1 on GBPJPY H4: REJECT at RUNG 0 SANITY -- min_trades: observed 56, required gte 400, short by 344
- ichimoku_kumo_trend_m689694c1 on GBPUSD H4: REJECT at RUNG 0 SANITY -- min_trades: observed 53, required gte 400, short by 347
- ichimoku_kumo_trend_m689694c1 on NZDUSD H4: REJECT at RUNG 0 SANITY -- min_trades: observed 66, required gte 400, short by 334
- ichimoku_kumo_trend_m689694c1 on US500 H4: REJECT at RUNG 0 SANITY -- min_trades: observed 43, required gte 400, short by 357
- ichimoku_kumo_trend_m689694c1 on USDCAD H4: REJECT at RUNG 0 SANITY -- min_trades: observed 56, required gte 400, short by 344
- ichimoku_kumo_trend_m689694c1 on USDCHF H4: REJECT at RUNG 0 SANITY -- min_trades: observed 63, required gte 400, short by 337
- ichimoku_kumo_trend_m689694c1 on USDJPY H4: REJECT at RUNG 0 SANITY -- min_trades: observed 78, required gte 400, short by 322
- ichimoku_kumo_trend_m689694c1 on XAUUSD H4: REJECT at RUNG 0 SANITY -- min_trades: observed 49, required gte 400, short by 351
- ichimoku_kumo_trend_m4195c8bf on AUDUSD H4: REJECT at RUNG 0 SANITY -- min_trades: observed 59, required gte 400, short by 341
- ichimoku_kumo_trend_m4195c8bf on DE40 H4: REJECT at RUNG 0 SANITY -- min_trades: observed 35, required gte 400, short by 365
- ichimoku_kumo_trend_m4195c8bf on EURJPY H4: REJECT at RUNG 0 SANITY -- min_trades: observed 66, required gte 400, short by 334
- ichimoku_kumo_trend_m4195c8bf on EURUSD H4: REJECT at RUNG 0 SANITY -- min_trades: observed 59, required gte 400, short by 341
- ichimoku_kumo_trend_m4195c8bf on GBPJPY H4: REJECT at RUNG 0 SANITY -- min_trades: observed 56, required gte 400, short by 344
- ichimoku_kumo_trend_m4195c8bf on GBPUSD H4: REJECT at RUNG 0 SANITY -- min_trades: observed 52, required gte 400, short by 348
- ichimoku_kumo_trend_m4195c8bf on NZDUSD H4: REJECT at RUNG 0 SANITY -- min_trades: observed 63, required gte 400, short by 337
- ichimoku_kumo_trend_m4195c8bf on US500 H4: REJECT at RUNG 0 SANITY -- min_trades: observed 43, required gte 400, short by 357
- ichimoku_kumo_trend_m4195c8bf on USDCAD H4: REJECT at RUNG 0 SANITY -- min_trades: observed 55, required gte 400, short by 345
- ichimoku_kumo_trend_m4195c8bf on USDCHF H4: REJECT at RUNG 0 SANITY -- min_trades: observed 60, required gte 400, short by 340
- ichimoku_kumo_trend_m4195c8bf on USDJPY H4: REJECT at RUNG 0 SANITY -- min_trades: observed 77, required gte 400, short by 323
- ichimoku_kumo_trend_m4195c8bf on XAUUSD H4: REJECT at RUNG 0 SANITY -- min_trades: observed 48, required gte 400, short by 352
- ichimoku_kumo_trend_m8fbcd36c on AUDUSD H4: REJECT at RUNG 0 SANITY -- min_trades: observed 60, required gte 400, short by 340
- ichimoku_kumo_trend_m8fbcd36c on DE40 H4: REJECT at RUNG 0 SANITY -- min_trades: observed 47, required gte 400, short by 353
- ichimoku_kumo_trend_m8fbcd36c on EURJPY H4: REJECT at RUNG 0 SANITY -- min_trades: observed 69, required gte 400, short by 331
- ichimoku_kumo_trend_m8fbcd36c on EURUSD H4: REJECT at RUNG 0 SANITY -- min_trades: observed 59, required gte 400, short by 341
- ichimoku_kumo_trend_m8fbcd36c on GBPJPY H4: REJECT at RUNG 0 SANITY -- min_trades: observed 57, required gte 400, short by 343
- ichimoku_kumo_trend_m8fbcd36c on GBPUSD H4: REJECT at RUNG 0 SANITY -- min_trades: observed 53, required gte 400, short by 347
- ichimoku_kumo_trend_m8fbcd36c on NZDUSD H4: REJECT at RUNG 0 SANITY -- min_trades: observed 66, required gte 400, short by 334
- ichimoku_kumo_trend_m8fbcd36c on US500 H4: REJECT at RUNG 0 SANITY -- min_trades: observed 44, required gte 400, short by 356
- ichimoku_kumo_trend_m8fbcd36c on USDCAD H4: REJECT at RUNG 0 SANITY -- min_trades: observed 56, required gte 400, short by 344
- ichimoku_kumo_trend_m8fbcd36c on USDCHF H4: REJECT at RUNG 0 SANITY -- min_trades: observed 63, required gte 400, short by 337
- ichimoku_kumo_trend_m8fbcd36c on USDJPY H4: REJECT at RUNG 0 SANITY -- min_trades: observed 79, required gte 400, short by 321
- ichimoku_kumo_trend_m8fbcd36c on XAUUSD H4: REJECT at RUNG 0 SANITY -- min_trades: observed 49, required gte 400, short by 351
- ichimoku_kumo_trend_ma4c92c38 on AUDUSD H4: REJECT at RUNG 0 SANITY -- min_trades: observed 38, required gte 400, short by 362
- ichimoku_kumo_trend_ma4c92c38 on DE40 H4: REJECT at RUNG 0 SANITY -- min_trades: observed 28, required gte 400, short by 372
- ichimoku_kumo_trend_ma4c92c38 on EURJPY H4: REJECT at RUNG 0 SANITY -- min_trades: observed 38, required gte 400, short by 362
- ichimoku_kumo_trend_ma4c92c38 on EURUSD H4: REJECT at RUNG 0 SANITY -- min_trades: observed 34, required gte 400, short by 366
- ichimoku_kumo_trend_ma4c92c38 on GBPJPY H4: REJECT at RUNG 0 SANITY -- min_trades: observed 31, required gte 400, short by 369
- ichimoku_kumo_trend_ma4c92c38 on GBPUSD H4: REJECT at RUNG 0 SANITY -- min_trades: observed 34, required gte 400, short by 366
- ichimoku_kumo_trend_ma4c92c38 on NZDUSD H4: REJECT at RUNG 0 SANITY -- min_trades: observed 35, required gte 400, short by 365
- ichimoku_kumo_trend_ma4c92c38 on US500 H4: REJECT at RUNG 0 SANITY -- min_trades: observed 26, required gte 400, short by 374
- ichimoku_kumo_trend_ma4c92c38 on USDCAD H4: REJECT at RUNG 0 SANITY -- min_trades: observed 41, required gte 400, short by 359
- ichimoku_kumo_trend_ma4c92c38 on USDCHF H4: REJECT at RUNG 0 SANITY -- min_trades: observed 31, required gte 400, short by 369
- ichimoku_kumo_trend_ma4c92c38 on USDJPY H4: REJECT at RUNG 0 SANITY -- min_trades: observed 51, required gte 400, short by 349
- ichimoku_kumo_trend_ma4c92c38 on XAUUSD H4: REJECT at RUNG 0 SANITY -- min_trades: observed 31, required gte 400, short by 369
- ichimoku_kumo_trend_me73acb6c on AUDUSD H4: REJECT at RUNG 0 SANITY -- min_trades: observed 77, required gte 400, short by 323
- ichimoku_kumo_trend_me73acb6c on DE40 H4: REJECT at RUNG 0 SANITY -- min_trades: observed 65, required gte 400, short by 335
- ichimoku_kumo_trend_me73acb6c on EURJPY H4: REJECT at RUNG 0 SANITY -- min_trades: observed 81, required gte 400, short by 319
- ichimoku_kumo_trend_me73acb6c on EURUSD H4: REJECT at RUNG 0 SANITY -- min_trades: observed 66, required gte 400, short by 334
- ichimoku_kumo_trend_me73acb6c on GBPJPY H4: REJECT at RUNG 0 SANITY -- min_trades: observed 63, required gte 400, short by 337
- ichimoku_kumo_trend_me73acb6c on GBPUSD H4: REJECT at RUNG 0 SANITY -- min_trades: observed 67, required gte 400, short by 333
- ichimoku_kumo_trend_me73acb6c on NZDUSD H4: REJECT at RUNG 0 SANITY -- min_trades: observed 73, required gte 400, short by 327
- ichimoku_kumo_trend_me73acb6c on US500 H4: REJECT at RUNG 0 SANITY -- min_trades: observed 46, required gte 400, short by 354
- ichimoku_kumo_trend_me73acb6c on USDCAD H4: REJECT at RUNG 0 SANITY -- min_trades: observed 63, required gte 400, short by 337
- ichimoku_kumo_trend_me73acb6c on USDCHF H4: REJECT at RUNG 0 SANITY -- min_trades: observed 75, required gte 400, short by 325
- ichimoku_kumo_trend_me73acb6c on USDJPY H4: REJECT at RUNG 0 SANITY -- min_trades: observed 89, required gte 400, short by 311
- ichimoku_kumo_trend_me73acb6c on XAUUSD H4: REJECT at RUNG 0 SANITY -- min_trades: observed 60, required gte 400, short by 340
- ichimoku_kumo_trend_mca231a0f on AUDUSD H4: REJECT at RUNG 0 SANITY -- min_trades: observed 60, required gte 400, short by 340
- ichimoku_kumo_trend_mca231a0f on DE40 H4: REJECT at RUNG 0 SANITY -- min_trades: observed 47, required gte 400, short by 353
- ichimoku_kumo_trend_mca231a0f on EURJPY H4: REJECT at RUNG 0 SANITY -- min_trades: observed 69, required gte 400, short by 331
- ichimoku_kumo_trend_mca231a0f on EURUSD H4: REJECT at RUNG 0 SANITY -- min_trades: observed 59, required gte 400, short by 341
- ichimoku_kumo_trend_mca231a0f on GBPJPY H4: REJECT at RUNG 0 SANITY -- min_trades: observed 57, required gte 400, short by 343
- ichimoku_kumo_trend_mca231a0f on GBPUSD H4: REJECT at RUNG 0 SANITY -- min_trades: observed 53, required gte 400, short by 347
- ichimoku_kumo_trend_mca231a0f on NZDUSD H4: REJECT at RUNG 0 SANITY -- min_trades: observed 66, required gte 400, short by 334
- ichimoku_kumo_trend_mca231a0f on US500 H4: REJECT at RUNG 0 SANITY -- min_trades: observed 43, required gte 400, short by 357
- ichimoku_kumo_trend_mca231a0f on USDCAD H4: REJECT at RUNG 0 SANITY -- min_trades: observed 56, required gte 400, short by 344
- ichimoku_kumo_trend_mca231a0f on USDCHF H4: REJECT at RUNG 0 SANITY -- min_trades: observed 63, required gte 400, short by 337
- ichimoku_kumo_trend_mca231a0f on USDJPY H4: REJECT at RUNG 0 SANITY -- min_trades: observed 79, required gte 400, short by 321
- ichimoku_kumo_trend_mca231a0f on XAUUSD H4: REJECT at RUNG 0 SANITY -- min_trades: observed 49, required gte 400, short by 351
- ichimoku_kumo_trend_m0bd80cab on AUDUSD H4: REJECT at RUNG 0 SANITY -- min_trades: observed 61, required gte 400, short by 339
- ichimoku_kumo_trend_m0bd80cab on DE40 H4: REJECT at RUNG 0 SANITY -- min_trades: observed 55, required gte 400, short by 345
- ichimoku_kumo_trend_m0bd80cab on EURJPY H4: REJECT at RUNG 0 SANITY -- min_trades: observed 69, required gte 400, short by 331
- ichimoku_kumo_trend_m0bd80cab on EURUSD H4: REJECT at RUNG 0 SANITY -- min_trades: observed 60, required gte 400, short by 340
- ichimoku_kumo_trend_m0bd80cab on GBPJPY H4: REJECT at RUNG 0 SANITY -- min_trades: observed 58, required gte 400, short by 342
- ichimoku_kumo_trend_m0bd80cab on GBPUSD H4: REJECT at RUNG 0 SANITY -- min_trades: observed 53, required gte 400, short by 347
- ichimoku_kumo_trend_m0bd80cab on NZDUSD H4: REJECT at RUNG 0 SANITY -- min_trades: observed 67, required gte 400, short by 333
- ichimoku_kumo_trend_m0bd80cab on US500 H4: REJECT at RUNG 0 SANITY -- min_trades: observed 44, required gte 400, short by 356
- ichimoku_kumo_trend_m0bd80cab on USDCAD H4: REJECT at RUNG 0 SANITY -- min_trades: observed 58, required gte 400, short by 342
- ichimoku_kumo_trend_m0bd80cab on USDCHF H4: REJECT at RUNG 0 SANITY -- min_trades: observed 63, required gte 400, short by 337
- ichimoku_kumo_trend_m0bd80cab on USDJPY H4: REJECT at RUNG 0 SANITY -- min_trades: observed 76, required gte 400, short by 324
- ichimoku_kumo_trend_m0bd80cab on XAUUSD H4: REJECT at RUNG 0 SANITY -- min_trades: observed 49, required gte 400, short by 351
- ichimoku_kumo_trend_m67c7da5a on AUDUSD H4: REJECT at RUNG 0 SANITY -- min_trades: observed 54, required gte 400, short by 346
- ichimoku_kumo_trend_m67c7da5a on EURUSD H4: REJECT at RUNG 0 SANITY -- min_trades: observed 54, required gte 400, short by 346
- ichimoku_kumo_trend_m67c7da5a on GBPUSD H4: REJECT at RUNG 0 SANITY -- min_trades: observed 48, required gte 400, short by 352
- ichimoku_kumo_trend_m67c7da5a on NZDUSD H4: REJECT at RUNG 0 SANITY -- min_trades: observed 60, required gte 400, short by 340
- ichimoku_kumo_trend_m67c7da5a on USDCAD H4: REJECT at RUNG 0 SANITY -- min_trades: observed 57, required gte 400, short by 343
- ichimoku_kumo_trend_m67c7da5a on USDCHF H4: REJECT at RUNG 0 SANITY -- min_trades: observed 61, required gte 400, short by 339
- ichimoku_kumo_trend_m67c7da5a on USDJPY H4: REJECT at RUNG 0 SANITY -- min_trades: observed 69, required gte 400, short by 331
- macd_ema_trend_hybrid_ma2bc336b on AUDUSD H4: REJECT at RUNG 0 SANITY -- min_trades: observed 49, required gte 400, short by 351
- macd_ema_trend_hybrid_ma2bc336b on DE40 H4: REJECT at RUNG 0 SANITY -- min_trades: observed 31, required gte 400, short by 369
- macd_ema_trend_hybrid_ma2bc336b on EURJPY H4: REJECT at RUNG 0 SANITY -- min_trades: observed 44, required gte 400, short by 356
- macd_ema_trend_hybrid_ma2bc336b on EURUSD H4: REJECT at RUNG 0 SANITY -- min_trades: observed 53, required gte 400, short by 347
- macd_ema_trend_hybrid_ma2bc336b on GBPJPY H4: REJECT at RUNG 0 SANITY -- min_trades: observed 53, required gte 400, short by 347
- macd_ema_trend_hybrid_ma2bc336b on GBPUSD H4: REJECT at RUNG 0 SANITY -- min_trades: observed 68, required gte 400, short by 332
- macd_ema_trend_hybrid_ma2bc336b on NZDUSD H4: REJECT at RUNG 0 SANITY -- min_trades: observed 61, required gte 400, short by 339
- macd_ema_trend_hybrid_ma2bc336b on US500 H4: REJECT at RUNG 0 SANITY -- min_trades: observed 55, required gte 400, short by 345
- macd_ema_trend_hybrid_ma2bc336b on USDCAD H4: REJECT at RUNG 0 SANITY -- min_trades: observed 60, required gte 400, short by 340
- macd_ema_trend_hybrid_ma2bc336b on USDCHF H4: REJECT at RUNG 0 SANITY -- min_trades: observed 55, required gte 400, short by 345
- macd_ema_trend_hybrid_ma2bc336b on USDJPY H4: REJECT at RUNG 0 SANITY -- min_trades: observed 44, required gte 400, short by 356
- macd_ema_trend_hybrid_ma2bc336b on XAUUSD H4: REJECT at RUNG 0 SANITY -- min_trades: observed 41, required gte 400, short by 359
- macd_ema_trend_hybrid_m03d3ee23 on AUDUSD H4: REJECT at RUNG 0 SANITY -- min_trades: observed 47, required gte 400, short by 353
- macd_ema_trend_hybrid_m03d3ee23 on DE40 H4: REJECT at RUNG 0 SANITY -- min_trades: observed 45, required gte 400, short by 355
- macd_ema_trend_hybrid_m03d3ee23 on EURJPY H4: REJECT at RUNG 0 SANITY -- min_trades: observed 45, required gte 400, short by 355
- macd_ema_trend_hybrid_m03d3ee23 on EURUSD H4: REJECT at RUNG 0 SANITY -- min_trades: observed 51, required gte 400, short by 349
- macd_ema_trend_hybrid_m03d3ee23 on GBPJPY H4: REJECT at RUNG 0 SANITY -- min_trades: observed 53, required gte 400, short by 347
- macd_ema_trend_hybrid_m03d3ee23 on GBPUSD H4: REJECT at RUNG 0 SANITY -- min_trades: observed 63, required gte 400, short by 337
- macd_ema_trend_hybrid_m03d3ee23 on NZDUSD H4: REJECT at RUNG 0 SANITY -- min_trades: observed 64, required gte 400, short by 336
- macd_ema_trend_hybrid_m03d3ee23 on US500 H4: REJECT at RUNG 0 SANITY -- min_trades: observed 53, required gte 400, short by 347
- macd_ema_trend_hybrid_m03d3ee23 on USDCAD H4: REJECT at RUNG 0 SANITY -- min_trades: observed 54, required gte 400, short by 346
- macd_ema_trend_hybrid_m03d3ee23 on USDCHF H4: REJECT at RUNG 0 SANITY -- min_trades: observed 53, required gte 400, short by 347
- macd_ema_trend_hybrid_m03d3ee23 on USDJPY H4: REJECT at RUNG 0 SANITY -- min_trades: observed 42, required gte 400, short by 358
- macd_ema_trend_hybrid_m03d3ee23 on XAUUSD H4: REJECT at RUNG 0 SANITY -- min_trades: observed 42, required gte 400, short by 358
- macd_ema_trend_hybrid_m3728054d on AUDUSD H4: REJECT at RUNG 0 SANITY -- min_trades: observed 49, required gte 400, short by 351
- macd_ema_trend_hybrid_m3728054d on DE40 H4: REJECT at RUNG 0 SANITY -- min_trades: observed 31, required gte 400, short by 369
- macd_ema_trend_hybrid_m3728054d on EURJPY H4: REJECT at RUNG 0 SANITY -- min_trades: observed 44, required gte 400, short by 356
- macd_ema_trend_hybrid_m3728054d on EURUSD H4: REJECT at RUNG 0 SANITY -- min_trades: observed 53, required gte 400, short by 347
- macd_ema_trend_hybrid_m3728054d on GBPJPY H4: REJECT at RUNG 0 SANITY -- min_trades: observed 53, required gte 400, short by 347
- macd_ema_trend_hybrid_m3728054d on GBPUSD H4: REJECT at RUNG 0 SANITY -- min_trades: observed 68, required gte 400, short by 332
- macd_ema_trend_hybrid_m3728054d on NZDUSD H4: REJECT at RUNG 0 SANITY -- min_trades: observed 61, required gte 400, short by 339
- macd_ema_trend_hybrid_m3728054d on US500 H4: REJECT at RUNG 0 SANITY -- min_trades: observed 55, required gte 400, short by 345
- macd_ema_trend_hybrid_m3728054d on USDCAD H4: REJECT at RUNG 0 SANITY -- min_trades: observed 60, required gte 400, short by 340
- macd_ema_trend_hybrid_m3728054d on USDCHF H4: REJECT at RUNG 0 SANITY -- min_trades: observed 55, required gte 400, short by 345
- macd_ema_trend_hybrid_m3728054d on USDJPY H4: REJECT at RUNG 0 SANITY -- min_trades: observed 44, required gte 400, short by 356
- macd_ema_trend_hybrid_m3728054d on XAUUSD H4: REJECT at RUNG 0 SANITY -- min_trades: observed 41, required gte 400, short by 359
- macd_ema_trend_hybrid_m4b9a1dcd on AUDUSD H4: REJECT at RUNG 0 SANITY -- min_trades: observed 41, required gte 400, short by 359
- macd_ema_trend_hybrid_m4b9a1dcd on DE40 H4: REJECT at RUNG 0 SANITY -- min_trades: observed 27, required gte 400, short by 373
- macd_ema_trend_hybrid_m4b9a1dcd on EURJPY H4: REJECT at RUNG 0 SANITY -- min_trades: observed 37, required gte 400, short by 363
- macd_ema_trend_hybrid_m4b9a1dcd on EURUSD H4: REJECT at RUNG 0 SANITY -- min_trades: observed 41, required gte 400, short by 359
- macd_ema_trend_hybrid_m4b9a1dcd on GBPJPY H4: REJECT at RUNG 0 SANITY -- min_trades: observed 36, required gte 400, short by 364
- macd_ema_trend_hybrid_m4b9a1dcd on GBPUSD H4: REJECT at RUNG 0 SANITY -- min_trades: observed 50, required gte 400, short by 350
- macd_ema_trend_hybrid_m4b9a1dcd on NZDUSD H4: REJECT at RUNG 0 SANITY -- min_trades: observed 40, required gte 400, short by 360
- macd_ema_trend_hybrid_m4b9a1dcd on US500 H4: REJECT at RUNG 0 SANITY -- min_trades: observed 45, required gte 400, short by 355
- macd_ema_trend_hybrid_m4b9a1dcd on USDCAD H4: REJECT at RUNG 0 SANITY -- min_trades: observed 44, required gte 400, short by 356
- macd_ema_trend_hybrid_m4b9a1dcd on USDCHF H4: REJECT at RUNG 0 SANITY -- min_trades: observed 43, required gte 400, short by 357
- macd_ema_trend_hybrid_m4b9a1dcd on USDJPY H4: REJECT at RUNG 0 SANITY -- min_trades: observed 35, required gte 400, short by 365
- macd_ema_trend_hybrid_m4b9a1dcd on XAUUSD H4: REJECT at RUNG 0 SANITY -- min_trades: observed 32, required gte 400, short by 368
- macd_ema_trend_hybrid_m03f1d536 on AUDUSD H4: REJECT at RUNG 0 SANITY -- min_trades: observed 21, required gte 400, short by 379
- macd_ema_trend_hybrid_m03f1d536 on DE40 H4: REJECT at RUNG 0 SANITY -- min_trades: observed 16, required gte 400, short by 384
- macd_ema_trend_hybrid_m03f1d536 on EURJPY H4: REJECT at RUNG 0 SANITY -- min_trades: observed 23, required gte 400, short by 377
- macd_ema_trend_hybrid_m03f1d536 on EURUSD H4: REJECT at RUNG 0 SANITY -- min_trades: observed 24, required gte 400, short by 376
- macd_ema_trend_hybrid_m03f1d536 on GBPJPY H4: REJECT at RUNG 0 SANITY -- min_trades: observed 25, required gte 400, short by 375
- macd_ema_trend_hybrid_m03f1d536 on GBPUSD H4: REJECT at RUNG 0 SANITY -- min_trades: observed 32, required gte 400, short by 368
- macd_ema_trend_hybrid_m03f1d536 on NZDUSD H4: REJECT at RUNG 0 SANITY -- min_trades: observed 31, required gte 400, short by 369
- macd_ema_trend_hybrid_m03f1d536 on US500 H4: REJECT at RUNG 0 SANITY -- min_trades: observed 32, required gte 400, short by 368
- macd_ema_trend_hybrid_m03f1d536 on USDCAD H4: REJECT at RUNG 0 SANITY -- min_trades: observed 28, required gte 400, short by 372
- macd_ema_trend_hybrid_m03f1d536 on USDCHF H4: REJECT at RUNG 0 SANITY -- min_trades: observed 33, required gte 400, short by 367
- macd_ema_trend_hybrid_m03f1d536 on USDJPY H4: REJECT at RUNG 0 SANITY -- min_trades: observed 24, required gte 400, short by 376
- macd_ema_trend_hybrid_m03f1d536 on XAUUSD H4: REJECT at RUNG 0 SANITY -- min_trades: observed 17, required gte 400, short by 383
- macd_ema_trend_hybrid_mb7889927 on AUDUSD H4: REJECT at RUNG 0 SANITY -- min_trades: observed 49, required gte 400, short by 351
- macd_ema_trend_hybrid_mb7889927 on DE40 H4: REJECT at RUNG 0 SANITY -- min_trades: observed 31, required gte 400, short by 369
- macd_ema_trend_hybrid_mb7889927 on EURJPY H4: REJECT at RUNG 0 SANITY -- min_trades: observed 44, required gte 400, short by 356
- macd_ema_trend_hybrid_mb7889927 on EURUSD H4: REJECT at RUNG 0 SANITY -- min_trades: observed 53, required gte 400, short by 347
- macd_ema_trend_hybrid_mb7889927 on GBPJPY H4: REJECT at RUNG 0 SANITY -- min_trades: observed 53, required gte 400, short by 347
- macd_ema_trend_hybrid_mb7889927 on GBPUSD H4: REJECT at RUNG 0 SANITY -- min_trades: observed 68, required gte 400, short by 332
- macd_ema_trend_hybrid_mb7889927 on NZDUSD H4: REJECT at RUNG 0 SANITY -- min_trades: observed 61, required gte 400, short by 339
- macd_ema_trend_hybrid_mb7889927 on US500 H4: REJECT at RUNG 0 SANITY -- min_trades: observed 55, required gte 400, short by 345
- macd_ema_trend_hybrid_mb7889927 on USDCAD H4: REJECT at RUNG 0 SANITY -- min_trades: observed 60, required gte 400, short by 340
- macd_ema_trend_hybrid_mb7889927 on USDCHF H4: REJECT at RUNG 0 SANITY -- min_trades: observed 55, required gte 400, short by 345
- macd_ema_trend_hybrid_mb7889927 on USDJPY H4: REJECT at RUNG 0 SANITY -- min_trades: observed 44, required gte 400, short by 356
- macd_ema_trend_hybrid_mb7889927 on XAUUSD H4: REJECT at RUNG 0 SANITY -- min_trades: observed 41, required gte 400, short by 359
- macd_ema_trend_hybrid_mc45594a0 on AUDUSD H4: REJECT at RUNG 0 SANITY -- min_trades: observed 127, required gte 400, short by 273
- macd_ema_trend_hybrid_mc45594a0 on DE40 H4: REJECT at RUNG 0 SANITY -- min_trades: observed 55, required gte 400, short by 345
- macd_ema_trend_hybrid_mc45594a0 on EURJPY H4: REJECT at RUNG 0 SANITY -- min_trades: observed 127, required gte 400, short by 273
- macd_ema_trend_hybrid_mc45594a0 on EURUSD H4: REJECT at RUNG 0 SANITY -- min_trades: observed 144, required gte 400, short by 256
- macd_ema_trend_hybrid_mc45594a0 on GBPJPY H4: REJECT at RUNG 0 SANITY -- min_trades: observed 131, required gte 400, short by 269
- macd_ema_trend_hybrid_mc45594a0 on GBPUSD H4: REJECT at RUNG 0 SANITY -- min_trades: observed 156, required gte 400, short by 244
- macd_ema_trend_hybrid_mc45594a0 on NZDUSD H4: REJECT at RUNG 0 SANITY -- min_trades: observed 135, required gte 400, short by 265
- macd_ema_trend_hybrid_mc45594a0 on US500 H4: REJECT at RUNG 0 SANITY -- min_trades: observed 113, required gte 400, short by 287
- macd_ema_trend_hybrid_mc45594a0 on USDCAD H4: REJECT at RUNG 0 SANITY -- min_trades: observed 139, required gte 400, short by 261
- macd_ema_trend_hybrid_mc45594a0 on USDCHF H4: REJECT at RUNG 0 SANITY -- min_trades: observed 144, required gte 400, short by 256
- macd_ema_trend_hybrid_mc45594a0 on USDJPY H4: REJECT at RUNG 0 SANITY -- min_trades: observed 132, required gte 400, short by 268
- macd_ema_trend_hybrid_mc45594a0 on XAUUSD H4: REJECT at RUNG 0 SANITY -- min_trades: observed 120, required gte 400, short by 280
- macd_ema_trend_hybrid_m270351c7 on AUDUSD H4: REJECT at RUNG 0 SANITY -- min_trades: observed 47, required gte 400, short by 353
- macd_ema_trend_hybrid_m270351c7 on DE40 H4: REJECT at RUNG 0 SANITY -- min_trades: observed 37, required gte 400, short by 363
- macd_ema_trend_hybrid_m270351c7 on EURJPY H4: REJECT at RUNG 0 SANITY -- min_trades: observed 45, required gte 400, short by 355
- macd_ema_trend_hybrid_m270351c7 on EURUSD H4: REJECT at RUNG 0 SANITY -- min_trades: observed 27, required gte 400, short by 373
- macd_ema_trend_hybrid_m270351c7 on GBPJPY H4: REJECT at RUNG 0 SANITY -- min_trades: observed 51, required gte 400, short by 349
- macd_ema_trend_hybrid_m270351c7 on GBPUSD H4: REJECT at RUNG 0 SANITY -- min_trades: observed 68, required gte 400, short by 332
- macd_ema_trend_hybrid_m270351c7 on NZDUSD H4: REJECT at RUNG 0 SANITY -- min_trades: observed 29, required gte 400, short by 371
- macd_ema_trend_hybrid_m270351c7 on US500 H4: REJECT at RUNG 0 SANITY -- min_trades: observed 52, required gte 400, short by 348
- macd_ema_trend_hybrid_m270351c7 on USDCAD H4: REJECT at RUNG 0 SANITY -- min_trades: observed 4, required gte 400, short by 396
- macd_ema_trend_hybrid_m270351c7 on USDCHF H4: REJECT at RUNG 0 SANITY -- min_trades: observed 55, required gte 400, short by 345
- macd_ema_trend_hybrid_m270351c7 on USDJPY H4: REJECT at RUNG 0 SANITY -- min_trades: observed 43, required gte 400, short by 357
- macd_ema_trend_hybrid_m270351c7 on XAUUSD H4: REJECT at RUNG 0 SANITY -- min_trades: observed 38, required gte 400, short by 362
- macd_ema_trend_hybrid_mbd5c916c on AUDUSD H4: REJECT at RUNG 0 SANITY -- min_trades: observed 25, required gte 400, short by 375
- macd_ema_trend_hybrid_mbd5c916c on EURUSD H4: REJECT at RUNG 0 SANITY -- min_trades: observed 27, required gte 400, short by 373
- macd_ema_trend_hybrid_mbd5c916c on GBPUSD H4: REJECT at RUNG 0 SANITY -- min_trades: observed 66, required gte 400, short by 334
- macd_ema_trend_hybrid_mbd5c916c on NZDUSD H4: REJECT at RUNG 0 SANITY -- min_trades: observed 9, required gte 400, short by 391
- macd_ema_trend_hybrid_mbd5c916c on USDCAD H4: REJECT at RUNG 0 SANITY -- min_trades: observed 43, required gte 400, short by 357
- macd_ema_trend_hybrid_mbd5c916c on USDCHF H4: REJECT at RUNG 0 SANITY -- min_trades: observed 14, required gte 400, short by 386
- macd_ema_trend_hybrid_mbd5c916c on USDJPY H4: REJECT at RUNG 0 SANITY -- min_trades: observed 44, required gte 400, short by 356
- rsi_band_mean_reversion_md48b52cb on AUDUSD H4: REJECT at RUNG 0 SANITY -- min_trades: observed 170, required gte 400, short by 230
- rsi_band_mean_reversion_md48b52cb on EURGBP H4: REJECT at RUNG 0 SANITY -- min_trades: observed 110, required gte 400, short by 290
- rsi_band_mean_reversion_md48b52cb on EURUSD H4: REJECT at RUNG 0 SANITY -- min_trades: observed 93, required gte 400, short by 307
- rsi_band_mean_reversion_md48b52cb on GBPUSD H4: REJECT at RUNG 0 SANITY -- min_trades: observed 123, required gte 400, short by 277
- rsi_band_mean_reversion_md48b52cb on NZDUSD H4: REJECT at RUNG 0 SANITY -- min_trades: observed 168, required gte 400, short by 232
- rsi_band_mean_reversion_md48b52cb on USDCAD H4: REJECT at RUNG 0 SANITY -- min_trades: observed 81, required gte 400, short by 319
- rsi_band_mean_reversion_md48b52cb on USDCHF H4: REJECT at RUNG 0 SANITY -- min_trades: observed 111, required gte 400, short by 289
- rsi_band_mean_reversion_md48b52cb on USDJPY H4: REJECT at RUNG 0 SANITY -- min_trades: observed 88, required gte 400, short by 312
- rsi_band_mean_reversion_mdbb3266a on AUDUSD H4: REJECT at RUNG 0 SANITY -- min_trades: observed 186, required gte 400, short by 214
- rsi_band_mean_reversion_mdbb3266a on EURGBP H4: REJECT at RUNG 0 SANITY -- min_trades: observed 160, required gte 400, short by 240
- rsi_band_mean_reversion_mdbb3266a on EURUSD H4: REJECT at RUNG 0 SANITY -- min_trades: observed 129, required gte 400, short by 271
- rsi_band_mean_reversion_mdbb3266a on GBPUSD H4: REJECT at RUNG 0 SANITY -- min_trades: observed 161, required gte 400, short by 239
- rsi_band_mean_reversion_mdbb3266a on NZDUSD H4: REJECT at RUNG 0 SANITY -- min_trades: observed 172, required gte 400, short by 228
- rsi_band_mean_reversion_mdbb3266a on USDCAD H4: REJECT at RUNG 0 SANITY -- min_trades: observed 128, required gte 400, short by 272
- rsi_band_mean_reversion_mdbb3266a on USDCHF H4: REJECT at RUNG 0 SANITY -- min_trades: observed 145, required gte 400, short by 255
- rsi_band_mean_reversion_mdbb3266a on USDJPY H4: REJECT at RUNG 0 SANITY -- min_trades: observed 143, required gte 400, short by 257
- rsi_band_mean_reversion_m6e0465f0 on AUDUSD H4: REJECT at RUNG 0 SANITY -- min_trades: observed 185, required gte 400, short by 215
- rsi_band_mean_reversion_m6e0465f0 on EURGBP H4: REJECT at RUNG 0 SANITY -- min_trades: observed 168, required gte 400, short by 232
- rsi_band_mean_reversion_m6e0465f0 on EURUSD H4: REJECT at RUNG 0 SANITY -- min_trades: observed 128, required gte 400, short by 272
- rsi_band_mean_reversion_m6e0465f0 on GBPUSD H4: REJECT at RUNG 0 SANITY -- min_trades: observed 163, required gte 400, short by 237
- rsi_band_mean_reversion_m6e0465f0 on NZDUSD H4: REJECT at RUNG 0 SANITY -- min_trades: observed 177, required gte 400, short by 223
- rsi_band_mean_reversion_m6e0465f0 on USDCAD H4: REJECT at RUNG 0 SANITY -- min_trades: observed 134, required gte 400, short by 266
- rsi_band_mean_reversion_m6e0465f0 on USDCHF H4: REJECT at RUNG 0 SANITY -- min_trades: observed 153, required gte 400, short by 247
- rsi_band_mean_reversion_m6e0465f0 on USDJPY H4: REJECT at RUNG 0 SANITY -- min_trades: observed 155, required gte 400, short by 245
- rsi_band_mean_reversion_me1d6d436 on AUDUSD H4: REJECT at RUNG 0 SANITY -- min_trades: observed 294, required gte 400, short by 106
- rsi_band_mean_reversion_me1d6d436 on EURGBP H4: REJECT at RUNG 0 SANITY -- min_trades: observed 246, required gte 400, short by 154
- rsi_band_mean_reversion_me1d6d436 on EURUSD H4: REJECT at RUNG 0 SANITY -- min_trades: observed 236, required gte 400, short by 164
- rsi_band_mean_reversion_me1d6d436 on GBPUSD H4: REJECT at RUNG 0 SANITY -- min_trades: observed 261, required gte 400, short by 139
- rsi_band_mean_reversion_me1d6d436 on NZDUSD H4: REJECT at RUNG 0 SANITY -- min_trades: observed 286, required gte 400, short by 114
- rsi_band_mean_reversion_me1d6d436 on USDCAD H4: REJECT at RUNG 0 SANITY -- min_trades: observed 256, required gte 400, short by 144
- rsi_band_mean_reversion_me1d6d436 on USDCHF H4: REJECT at RUNG 0 SANITY -- min_trades: observed 243, required gte 400, short by 157
- rsi_band_mean_reversion_me1d6d436 on USDJPY H4: REJECT at RUNG 0 SANITY -- min_trades: observed 247, required gte 400, short by 153
- rsi_band_mean_reversion_m42a36d28 on AUDUSD H4: REJECT at RUNG 0 SANITY -- min_trades: observed 0, required gte 400, short by 400
- rsi_band_mean_reversion_m42a36d28 on EURGBP H4: REJECT at RUNG 0 SANITY -- min_trades: observed 0, required gte 400, short by 400
- rsi_band_mean_reversion_m42a36d28 on EURUSD H4: REJECT at RUNG 0 SANITY -- min_trades: observed 0, required gte 400, short by 400
- rsi_band_mean_reversion_m42a36d28 on GBPUSD H4: REJECT at RUNG 0 SANITY -- min_trades: observed 0, required gte 400, short by 400
- rsi_band_mean_reversion_m42a36d28 on NZDUSD H4: REJECT at RUNG 0 SANITY -- min_trades: observed 0, required gte 400, short by 400
- rsi_band_mean_reversion_m42a36d28 on USDCAD H4: REJECT at RUNG 0 SANITY -- min_trades: observed 0, required gte 400, short by 400
- rsi_band_mean_reversion_m42a36d28 on USDCHF H4: REJECT at RUNG 0 SANITY -- min_trades: observed 0, required gte 400, short by 400
- rsi_band_mean_reversion_m42a36d28 on USDJPY H4: REJECT at RUNG 0 SANITY -- min_trades: observed 0, required gte 400, short by 400
- rsi_band_mean_reversion_m7c32d514 on AUDUSD H4: REJECT at RUNG 0 SANITY -- min_trades: observed 193, required gte 400, short by 207
- rsi_band_mean_reversion_m7c32d514 on EURGBP H4: REJECT at RUNG 0 SANITY -- min_trades: observed 173, required gte 400, short by 227
- rsi_band_mean_reversion_m7c32d514 on EURUSD H4: REJECT at RUNG 0 SANITY -- min_trades: observed 135, required gte 400, short by 265
- rsi_band_mean_reversion_m7c32d514 on GBPUSD H4: REJECT at RUNG 0 SANITY -- min_trades: observed 170, required gte 400, short by 230
- rsi_band_mean_reversion_m7c32d514 on NZDUSD H4: REJECT at RUNG 0 SANITY -- min_trades: observed 182, required gte 400, short by 218
- rsi_band_mean_reversion_m7c32d514 on USDCAD H4: REJECT at RUNG 0 SANITY -- min_trades: observed 137, required gte 400, short by 263
- rsi_band_mean_reversion_m7c32d514 on USDCHF H4: REJECT at RUNG 0 SANITY -- min_trades: observed 155, required gte 400, short by 245
- rsi_band_mean_reversion_m7c32d514 on USDJPY H4: REJECT at RUNG 0 SANITY -- min_trades: observed 157, required gte 400, short by 243
- rsi_band_mean_reversion_m6113daf1 on AUDUSD H4: REJECT at RUNG 0 SANITY -- min_trades: observed 182, required gte 400, short by 218
- rsi_band_mean_reversion_m6113daf1 on EURUSD H4: REJECT at RUNG 0 SANITY -- min_trades: observed 128, required gte 400, short by 272
- rsi_band_mean_reversion_m6113daf1 on GBPUSD H4: REJECT at RUNG 0 SANITY -- min_trades: observed 160, required gte 400, short by 240
- rsi_band_mean_reversion_m6113daf1 on NZDUSD H4: REJECT at RUNG 0 SANITY -- min_trades: observed 175, required gte 400, short by 225
- rsi_band_mean_reversion_m6113daf1 on USDCAD H4: REJECT at RUNG 0 SANITY -- min_trades: observed 133, required gte 400, short by 267
- rsi_band_mean_reversion_m6113daf1 on USDCHF H4: REJECT at RUNG 0 SANITY -- min_trades: observed 150, required gte 400, short by 250
- rsi_band_mean_reversion_m6113daf1 on USDJPY H4: REJECT at RUNG 0 SANITY -- min_trades: observed 147, required gte 400, short by 253
- rsi_band_mean_reversion_md0807760 on AUDUSD H4: REJECT at RUNG 0 SANITY -- min_trades: observed 202, required gte 400, short by 198
- rsi_band_mean_reversion_md0807760 on EURUSD H4: REJECT at RUNG 0 SANITY -- min_trades: observed 136, required gte 400, short by 264
- rsi_band_mean_reversion_md0807760 on GBPUSD H4: REJECT at RUNG 0 SANITY -- min_trades: observed 174, required gte 400, short by 226
- rsi_band_mean_reversion_md0807760 on NZDUSD H4: REJECT at RUNG 0 SANITY -- min_trades: observed 191, required gte 400, short by 209
- rsi_band_mean_reversion_md0807760 on USDCAD H4: REJECT at RUNG 0 SANITY -- min_trades: observed 139, required gte 400, short by 261
- rsi_band_mean_reversion_md0807760 on USDCHF H4: REJECT at RUNG 0 SANITY -- min_trades: observed 162, required gte 400, short by 238
- rsi_band_mean_reversion_md0807760 on USDJPY H4: REJECT at RUNG 0 SANITY -- min_trades: observed 161, required gte 400, short by 239
- rsi_band_mean_reversion_m29180d06 on AUDUSD H4: REJECT at RUNG 0 SANITY -- min_trades: observed 175, required gte 400, short by 225
- rsi_band_mean_reversion_m29180d06 on EURUSD H4: REJECT at RUNG 0 SANITY -- min_trades: observed 127, required gte 400, short by 273
- rsi_band_mean_reversion_m29180d06 on GBPUSD H4: REJECT at RUNG 0 SANITY -- min_trades: observed 157, required gte 400, short by 243
- rsi_band_mean_reversion_m29180d06 on NZDUSD H4: REJECT at RUNG 0 SANITY -- min_trades: observed 166, required gte 400, short by 234
- rsi_band_mean_reversion_m29180d06 on USDCAD H4: REJECT at RUNG 0 SANITY -- min_trades: observed 129, required gte 400, short by 271
- rsi_band_mean_reversion_m29180d06 on USDCHF H4: REJECT at RUNG 0 SANITY -- min_trades: observed 144, required gte 400, short by 256
- rsi_band_mean_reversion_m29180d06 on USDJPY H4: REJECT at RUNG 0 SANITY -- min_trades: observed 146, required gte 400, short by 254

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
- donchian_breakout_atr on AUDJPY H4: out_of_universe -- donchian_breakout_atr declares universe ['EURUSD', 'GBPUSD', 'USDJPY', 'AUDUSD', 'USDCAD', 'USDCHF', 'NZDUSD', 'EURJPY', 'GBPJPY', 'XAUUSD', 'US500', 'DE40'] and timeframes ['H4', 'D1']; running it on AUDJPY H4 would validate a strategy nobody wrote
- donchian_breakout_atr on EURGBP H4: out_of_universe -- donchian_breakout_atr declares universe ['EURUSD', 'GBPUSD', 'USDJPY', 'AUDUSD', 'USDCAD', 'USDCHF', 'NZDUSD', 'EURJPY', 'GBPJPY', 'XAUUSD', 'US500', 'DE40'] and timeframes ['H4', 'D1']; running it on EURGBP H4 would validate a strategy nobody wrote
- donchian_breakout_atr on UK100 H4: out_of_universe -- donchian_breakout_atr declares universe ['EURUSD', 'GBPUSD', 'USDJPY', 'AUDUSD', 'USDCAD', 'USDCHF', 'NZDUSD', 'EURJPY', 'GBPJPY', 'XAUUSD', 'US500', 'DE40'] and timeframes ['H4', 'D1']; running it on UK100 H4 would validate a strategy nobody wrote
- donchian_breakout_atr on XAGUSD H4: out_of_universe -- donchian_breakout_atr declares universe ['EURUSD', 'GBPUSD', 'USDJPY', 'AUDUSD', 'USDCAD', 'USDCHF', 'NZDUSD', 'EURJPY', 'GBPJPY', 'XAUUSD', 'US500', 'DE40'] and timeframes ['H4', 'D1']; running it on XAGUSD H4 would validate a strategy nobody wrote
- fib_golden_pocket_pullback on AUDJPY H4: out_of_universe -- fib_golden_pocket_pullback declares universe ['EURUSD', 'GBPUSD', 'USDJPY', 'AUDUSD', 'USDCAD', 'USDCHF', 'NZDUSD', 'EURJPY', 'GBPJPY', 'XAUUSD', 'US500', 'DE40'] and timeframes ['H1', 'H4', 'D1']; running it on AUDJPY H4 would validate a strategy nobody wrote
- fib_golden_pocket_pullback on EURGBP H4: out_of_universe -- fib_golden_pocket_pullback declares universe ['EURUSD', 'GBPUSD', 'USDJPY', 'AUDUSD', 'USDCAD', 'USDCHF', 'NZDUSD', 'EURJPY', 'GBPJPY', 'XAUUSD', 'US500', 'DE40'] and timeframes ['H1', 'H4', 'D1']; running it on EURGBP H4 would validate a strategy nobody wrote
- fib_golden_pocket_pullback on UK100 H4: out_of_universe -- fib_golden_pocket_pullback declares universe ['EURUSD', 'GBPUSD', 'USDJPY', 'AUDUSD', 'USDCAD', 'USDCHF', 'NZDUSD', 'EURJPY', 'GBPJPY', 'XAUUSD', 'US500', 'DE40'] and timeframes ['H1', 'H4', 'D1']; running it on UK100 H4 would validate a strategy nobody wrote
- fib_golden_pocket_pullback on XAGUSD H4: out_of_universe -- fib_golden_pocket_pullback declares universe ['EURUSD', 'GBPUSD', 'USDJPY', 'AUDUSD', 'USDCAD', 'USDCHF', 'NZDUSD', 'EURJPY', 'GBPJPY', 'XAUUSD', 'US500', 'DE40'] and timeframes ['H1', 'H4', 'D1']; running it on XAGUSD H4 would validate a strategy nobody wrote
- ichimoku_kumo_trend on AUDJPY H4: out_of_universe -- ichimoku_kumo_trend declares universe ['EURUSD', 'GBPUSD', 'USDJPY', 'AUDUSD', 'USDCAD', 'USDCHF', 'NZDUSD', 'EURJPY', 'GBPJPY', 'XAUUSD', 'US500', 'DE40'] and timeframes ['H4', 'D1']; running it on AUDJPY H4 would validate a strategy nobody wrote
- ichimoku_kumo_trend on EURGBP H4: out_of_universe -- ichimoku_kumo_trend declares universe ['EURUSD', 'GBPUSD', 'USDJPY', 'AUDUSD', 'USDCAD', 'USDCHF', 'NZDUSD', 'EURJPY', 'GBPJPY', 'XAUUSD', 'US500', 'DE40'] and timeframes ['H4', 'D1']; running it on EURGBP H4 would validate a strategy nobody wrote
- ichimoku_kumo_trend on UK100 H4: out_of_universe -- ichimoku_kumo_trend declares universe ['EURUSD', 'GBPUSD', 'USDJPY', 'AUDUSD', 'USDCAD', 'USDCHF', 'NZDUSD', 'EURJPY', 'GBPJPY', 'XAUUSD', 'US500', 'DE40'] and timeframes ['H4', 'D1']; running it on UK100 H4 would validate a strategy nobody wrote
- ichimoku_kumo_trend on XAGUSD H4: out_of_universe -- ichimoku_kumo_trend declares universe ['EURUSD', 'GBPUSD', 'USDJPY', 'AUDUSD', 'USDCAD', 'USDCHF', 'NZDUSD', 'EURJPY', 'GBPJPY', 'XAUUSD', 'US500', 'DE40'] and timeframes ['H4', 'D1']; running it on XAGUSD H4 would validate a strategy nobody wrote
- macd_ema_trend_hybrid on AUDJPY H4: out_of_universe -- macd_ema_trend_hybrid declares universe ['EURUSD', 'GBPUSD', 'USDJPY', 'AUDUSD', 'USDCAD', 'USDCHF', 'NZDUSD', 'EURJPY', 'GBPJPY', 'XAUUSD', 'US500', 'DE40'] and timeframes ['H4', 'D1']; running it on AUDJPY H4 would validate a strategy nobody wrote
- macd_ema_trend_hybrid on EURGBP H4: out_of_universe -- macd_ema_trend_hybrid declares universe ['EURUSD', 'GBPUSD', 'USDJPY', 'AUDUSD', 'USDCAD', 'USDCHF', 'NZDUSD', 'EURJPY', 'GBPJPY', 'XAUUSD', 'US500', 'DE40'] and timeframes ['H4', 'D1']; running it on EURGBP H4 would validate a strategy nobody wrote
- macd_ema_trend_hybrid on UK100 H4: out_of_universe -- macd_ema_trend_hybrid declares universe ['EURUSD', 'GBPUSD', 'USDJPY', 'AUDUSD', 'USDCAD', 'USDCHF', 'NZDUSD', 'EURJPY', 'GBPJPY', 'XAUUSD', 'US500', 'DE40'] and timeframes ['H4', 'D1']; running it on UK100 H4 would validate a strategy nobody wrote
- macd_ema_trend_hybrid on XAGUSD H4: out_of_universe -- macd_ema_trend_hybrid declares universe ['EURUSD', 'GBPUSD', 'USDJPY', 'AUDUSD', 'USDCAD', 'USDCHF', 'NZDUSD', 'EURJPY', 'GBPJPY', 'XAUUSD', 'US500', 'DE40'] and timeframes ['H4', 'D1']; running it on XAGUSD H4 would validate a strategy nobody wrote
- rsi_band_mean_reversion on AUDJPY H4: out_of_universe -- rsi_band_mean_reversion declares universe ['EURUSD', 'GBPUSD', 'USDJPY', 'AUDUSD', 'USDCAD', 'USDCHF', 'NZDUSD', 'EURGBP', 'AUDNZD', 'EURCHF'] and timeframes ['H1', 'H4']; running it on AUDJPY H4 would validate a strategy nobody wrote
- rsi_band_mean_reversion on DE40 H4: out_of_universe -- rsi_band_mean_reversion declares universe ['EURUSD', 'GBPUSD', 'USDJPY', 'AUDUSD', 'USDCAD', 'USDCHF', 'NZDUSD', 'EURGBP', 'AUDNZD', 'EURCHF'] and timeframes ['H1', 'H4']; running it on DE40 H4 would validate a strategy nobody wrote
- rsi_band_mean_reversion on EURJPY H4: out_of_universe -- rsi_band_mean_reversion declares universe ['EURUSD', 'GBPUSD', 'USDJPY', 'AUDUSD', 'USDCAD', 'USDCHF', 'NZDUSD', 'EURGBP', 'AUDNZD', 'EURCHF'] and timeframes ['H1', 'H4']; running it on EURJPY H4 would validate a strategy nobody wrote
- rsi_band_mean_reversion on GBPJPY H4: out_of_universe -- rsi_band_mean_reversion declares universe ['EURUSD', 'GBPUSD', 'USDJPY', 'AUDUSD', 'USDCAD', 'USDCHF', 'NZDUSD', 'EURGBP', 'AUDNZD', 'EURCHF'] and timeframes ['H1', 'H4']; running it on GBPJPY H4 would validate a strategy nobody wrote
- rsi_band_mean_reversion on UK100 H4: out_of_universe -- rsi_band_mean_reversion declares universe ['EURUSD', 'GBPUSD', 'USDJPY', 'AUDUSD', 'USDCAD', 'USDCHF', 'NZDUSD', 'EURGBP', 'AUDNZD', 'EURCHF'] and timeframes ['H1', 'H4']; running it on UK100 H4 would validate a strategy nobody wrote
- rsi_band_mean_reversion on US500 H4: out_of_universe -- rsi_band_mean_reversion declares universe ['EURUSD', 'GBPUSD', 'USDJPY', 'AUDUSD', 'USDCAD', 'USDCHF', 'NZDUSD', 'EURGBP', 'AUDNZD', 'EURCHF'] and timeframes ['H1', 'H4']; running it on US500 H4 would validate a strategy nobody wrote
- rsi_band_mean_reversion on XAGUSD H4: out_of_universe -- rsi_band_mean_reversion declares universe ['EURUSD', 'GBPUSD', 'USDJPY', 'AUDUSD', 'USDCAD', 'USDCHF', 'NZDUSD', 'EURGBP', 'AUDNZD', 'EURCHF'] and timeframes ['H1', 'H4']; running it on XAGUSD H4 would validate a strategy nobody wrote
- rsi_band_mean_reversion on XAUUSD H4: out_of_universe -- rsi_band_mean_reversion declares universe ['EURUSD', 'GBPUSD', 'USDJPY', 'AUDUSD', 'USDCAD', 'USDCHF', 'NZDUSD', 'EURGBP', 'AUDNZD', 'EURCHF'] and timeframes ['H1', 'H4']; running it on XAUUSD H4 would validate a strategy nobody wrote
- donchian_breakout_atr_m3ecf77ae on AUDJPY H4: out_of_universe -- donchian_breakout_atr_m3ecf77ae declares universe ['EURUSD', 'GBPUSD', 'USDJPY', 'AUDUSD', 'USDCAD', 'USDCHF', 'NZDUSD', 'EURJPY', 'GBPJPY', 'XAUUSD', 'US500', 'DE40'] and timeframes ['H4', 'D1']; running it on AUDJPY H4 would validate a strategy nobody wrote
- donchian_breakout_atr_m3ecf77ae on EURGBP H4: out_of_universe -- donchian_breakout_atr_m3ecf77ae declares universe ['EURUSD', 'GBPUSD', 'USDJPY', 'AUDUSD', 'USDCAD', 'USDCHF', 'NZDUSD', 'EURJPY', 'GBPJPY', 'XAUUSD', 'US500', 'DE40'] and timeframes ['H4', 'D1']; running it on EURGBP H4 would validate a strategy nobody wrote
- donchian_breakout_atr_m3ecf77ae on UK100 H4: out_of_universe -- donchian_breakout_atr_m3ecf77ae declares universe ['EURUSD', 'GBPUSD', 'USDJPY', 'AUDUSD', 'USDCAD', 'USDCHF', 'NZDUSD', 'EURJPY', 'GBPJPY', 'XAUUSD', 'US500', 'DE40'] and timeframes ['H4', 'D1']; running it on UK100 H4 would validate a strategy nobody wrote
- donchian_breakout_atr_m3ecf77ae on XAGUSD H4: out_of_universe -- donchian_breakout_atr_m3ecf77ae declares universe ['EURUSD', 'GBPUSD', 'USDJPY', 'AUDUSD', 'USDCAD', 'USDCHF', 'NZDUSD', 'EURJPY', 'GBPJPY', 'XAUUSD', 'US500', 'DE40'] and timeframes ['H4', 'D1']; running it on XAGUSD H4 would validate a strategy nobody wrote
- donchian_breakout_atr_mbdfe4742 on AUDJPY H4: out_of_universe -- donchian_breakout_atr_mbdfe4742 declares universe ['EURUSD', 'GBPUSD', 'USDJPY', 'AUDUSD', 'USDCAD', 'USDCHF', 'NZDUSD', 'EURJPY', 'GBPJPY', 'XAUUSD', 'US500', 'DE40'] and timeframes ['H4', 'D1']; running it on AUDJPY H4 would validate a strategy nobody wrote
- donchian_breakout_atr_mbdfe4742 on EURGBP H4: out_of_universe -- donchian_breakout_atr_mbdfe4742 declares universe ['EURUSD', 'GBPUSD', 'USDJPY', 'AUDUSD', 'USDCAD', 'USDCHF', 'NZDUSD', 'EURJPY', 'GBPJPY', 'XAUUSD', 'US500', 'DE40'] and timeframes ['H4', 'D1']; running it on EURGBP H4 would validate a strategy nobody wrote
- donchian_breakout_atr_mbdfe4742 on UK100 H4: out_of_universe -- donchian_breakout_atr_mbdfe4742 declares universe ['EURUSD', 'GBPUSD', 'USDJPY', 'AUDUSD', 'USDCAD', 'USDCHF', 'NZDUSD', 'EURJPY', 'GBPJPY', 'XAUUSD', 'US500', 'DE40'] and timeframes ['H4', 'D1']; running it on UK100 H4 would validate a strategy nobody wrote
- donchian_breakout_atr_mbdfe4742 on XAGUSD H4: out_of_universe -- donchian_breakout_atr_mbdfe4742 declares universe ['EURUSD', 'GBPUSD', 'USDJPY', 'AUDUSD', 'USDCAD', 'USDCHF', 'NZDUSD', 'EURJPY', 'GBPJPY', 'XAUUSD', 'US500', 'DE40'] and timeframes ['H4', 'D1']; running it on XAGUSD H4 would validate a strategy nobody wrote
- donchian_breakout_atr_m3c934a3b on AUDJPY H4: out_of_universe -- donchian_breakout_atr_m3c934a3b declares universe ['EURUSD', 'GBPUSD', 'USDJPY', 'AUDUSD', 'USDCAD', 'USDCHF', 'NZDUSD', 'EURJPY', 'GBPJPY', 'XAUUSD', 'US500', 'DE40'] and timeframes ['H4', 'D1']; running it on AUDJPY H4 would validate a strategy nobody wrote
- donchian_breakout_atr_m3c934a3b on EURGBP H4: out_of_universe -- donchian_breakout_atr_m3c934a3b declares universe ['EURUSD', 'GBPUSD', 'USDJPY', 'AUDUSD', 'USDCAD', 'USDCHF', 'NZDUSD', 'EURJPY', 'GBPJPY', 'XAUUSD', 'US500', 'DE40'] and timeframes ['H4', 'D1']; running it on EURGBP H4 would validate a strategy nobody wrote
- donchian_breakout_atr_m3c934a3b on UK100 H4: out_of_universe -- donchian_breakout_atr_m3c934a3b declares universe ['EURUSD', 'GBPUSD', 'USDJPY', 'AUDUSD', 'USDCAD', 'USDCHF', 'NZDUSD', 'EURJPY', 'GBPJPY', 'XAUUSD', 'US500', 'DE40'] and timeframes ['H4', 'D1']; running it on UK100 H4 would validate a strategy nobody wrote
- donchian_breakout_atr_m3c934a3b on XAGUSD H4: out_of_universe -- donchian_breakout_atr_m3c934a3b declares universe ['EURUSD', 'GBPUSD', 'USDJPY', 'AUDUSD', 'USDCAD', 'USDCHF', 'NZDUSD', 'EURJPY', 'GBPJPY', 'XAUUSD', 'US500', 'DE40'] and timeframes ['H4', 'D1']; running it on XAGUSD H4 would validate a strategy nobody wrote
- donchian_breakout_atr_m76dbb4f7 on AUDJPY H4: out_of_universe -- donchian_breakout_atr_m76dbb4f7 declares universe ['EURUSD', 'GBPUSD', 'USDJPY', 'AUDUSD', 'USDCAD', 'USDCHF', 'NZDUSD', 'EURJPY', 'GBPJPY', 'XAUUSD', 'US500', 'DE40'] and timeframes ['H4', 'D1']; running it on AUDJPY H4 would validate a strategy nobody wrote
- donchian_breakout_atr_m76dbb4f7 on EURGBP H4: out_of_universe -- donchian_breakout_atr_m76dbb4f7 declares universe ['EURUSD', 'GBPUSD', 'USDJPY', 'AUDUSD', 'USDCAD', 'USDCHF', 'NZDUSD', 'EURJPY', 'GBPJPY', 'XAUUSD', 'US500', 'DE40'] and timeframes ['H4', 'D1']; running it on EURGBP H4 would validate a strategy nobody wrote
- donchian_breakout_atr_m76dbb4f7 on UK100 H4: out_of_universe -- donchian_breakout_atr_m76dbb4f7 declares universe ['EURUSD', 'GBPUSD', 'USDJPY', 'AUDUSD', 'USDCAD', 'USDCHF', 'NZDUSD', 'EURJPY', 'GBPJPY', 'XAUUSD', 'US500', 'DE40'] and timeframes ['H4', 'D1']; running it on UK100 H4 would validate a strategy nobody wrote
- donchian_breakout_atr_m76dbb4f7 on XAGUSD H4: out_of_universe -- donchian_breakout_atr_m76dbb4f7 declares universe ['EURUSD', 'GBPUSD', 'USDJPY', 'AUDUSD', 'USDCAD', 'USDCHF', 'NZDUSD', 'EURJPY', 'GBPJPY', 'XAUUSD', 'US500', 'DE40'] and timeframes ['H4', 'D1']; running it on XAGUSD H4 would validate a strategy nobody wrote
- donchian_breakout_atr_m427fba0e on AUDJPY H4: out_of_universe -- donchian_breakout_atr_m427fba0e declares universe ['EURUSD', 'GBPUSD', 'USDJPY', 'AUDUSD', 'USDCAD', 'USDCHF', 'NZDUSD', 'EURJPY', 'GBPJPY', 'XAUUSD', 'US500', 'DE40'] and timeframes ['H4', 'D1']; running it on AUDJPY H4 would validate a strategy nobody wrote
- donchian_breakout_atr_m427fba0e on EURGBP H4: out_of_universe -- donchian_breakout_atr_m427fba0e declares universe ['EURUSD', 'GBPUSD', 'USDJPY', 'AUDUSD', 'USDCAD', 'USDCHF', 'NZDUSD', 'EURJPY', 'GBPJPY', 'XAUUSD', 'US500', 'DE40'] and timeframes ['H4', 'D1']; running it on EURGBP H4 would validate a strategy nobody wrote
- donchian_breakout_atr_m427fba0e on UK100 H4: out_of_universe -- donchian_breakout_atr_m427fba0e declares universe ['EURUSD', 'GBPUSD', 'USDJPY', 'AUDUSD', 'USDCAD', 'USDCHF', 'NZDUSD', 'EURJPY', 'GBPJPY', 'XAUUSD', 'US500', 'DE40'] and timeframes ['H4', 'D1']; running it on UK100 H4 would validate a strategy nobody wrote
- donchian_breakout_atr_m427fba0e on XAGUSD H4: out_of_universe -- donchian_breakout_atr_m427fba0e declares universe ['EURUSD', 'GBPUSD', 'USDJPY', 'AUDUSD', 'USDCAD', 'USDCHF', 'NZDUSD', 'EURJPY', 'GBPJPY', 'XAUUSD', 'US500', 'DE40'] and timeframes ['H4', 'D1']; running it on XAGUSD H4 would validate a strategy nobody wrote
- donchian_breakout_atr_m79f18824 on AUDJPY H4: out_of_universe -- donchian_breakout_atr_m79f18824 declares universe ['EURUSD', 'GBPUSD', 'USDJPY', 'AUDUSD', 'USDCAD', 'USDCHF', 'NZDUSD', 'EURJPY', 'GBPJPY', 'XAUUSD', 'US500', 'DE40'] and timeframes ['H4', 'D1']; running it on AUDJPY H4 would validate a strategy nobody wrote
- donchian_breakout_atr_m79f18824 on EURGBP H4: out_of_universe -- donchian_breakout_atr_m79f18824 declares universe ['EURUSD', 'GBPUSD', 'USDJPY', 'AUDUSD', 'USDCAD', 'USDCHF', 'NZDUSD', 'EURJPY', 'GBPJPY', 'XAUUSD', 'US500', 'DE40'] and timeframes ['H4', 'D1']; running it on EURGBP H4 would validate a strategy nobody wrote
- donchian_breakout_atr_m79f18824 on UK100 H4: out_of_universe -- donchian_breakout_atr_m79f18824 declares universe ['EURUSD', 'GBPUSD', 'USDJPY', 'AUDUSD', 'USDCAD', 'USDCHF', 'NZDUSD', 'EURJPY', 'GBPJPY', 'XAUUSD', 'US500', 'DE40'] and timeframes ['H4', 'D1']; running it on UK100 H4 would validate a strategy nobody wrote
- donchian_breakout_atr_m79f18824 on XAGUSD H4: out_of_universe -- donchian_breakout_atr_m79f18824 declares universe ['EURUSD', 'GBPUSD', 'USDJPY', 'AUDUSD', 'USDCAD', 'USDCHF', 'NZDUSD', 'EURJPY', 'GBPJPY', 'XAUUSD', 'US500', 'DE40'] and timeframes ['H4', 'D1']; running it on XAGUSD H4 would validate a strategy nobody wrote
- donchian_breakout_atr_mf2553d34 on AUDJPY H4: out_of_universe -- donchian_breakout_atr_mf2553d34 declares universe ['EURUSD', 'GBPUSD', 'USDJPY', 'AUDUSD', 'USDCAD', 'USDCHF', 'NZDUSD', 'EURJPY', 'GBPJPY', 'XAUUSD', 'US500', 'DE40'] and timeframes ['H4', 'D1']; running it on AUDJPY H4 would validate a strategy nobody wrote
- donchian_breakout_atr_mf2553d34 on EURGBP H4: out_of_universe -- donchian_breakout_atr_mf2553d34 declares universe ['EURUSD', 'GBPUSD', 'USDJPY', 'AUDUSD', 'USDCAD', 'USDCHF', 'NZDUSD', 'EURJPY', 'GBPJPY', 'XAUUSD', 'US500', 'DE40'] and timeframes ['H4', 'D1']; running it on EURGBP H4 would validate a strategy nobody wrote
- donchian_breakout_atr_mf2553d34 on UK100 H4: out_of_universe -- donchian_breakout_atr_mf2553d34 declares universe ['EURUSD', 'GBPUSD', 'USDJPY', 'AUDUSD', 'USDCAD', 'USDCHF', 'NZDUSD', 'EURJPY', 'GBPJPY', 'XAUUSD', 'US500', 'DE40'] and timeframes ['H4', 'D1']; running it on UK100 H4 would validate a strategy nobody wrote
- donchian_breakout_atr_mf2553d34 on XAGUSD H4: out_of_universe -- donchian_breakout_atr_mf2553d34 declares universe ['EURUSD', 'GBPUSD', 'USDJPY', 'AUDUSD', 'USDCAD', 'USDCHF', 'NZDUSD', 'EURJPY', 'GBPJPY', 'XAUUSD', 'US500', 'DE40'] and timeframes ['H4', 'D1']; running it on XAGUSD H4 would validate a strategy nobody wrote
- donchian_breakout_atr_m30945c80 on AUDJPY H4: out_of_universe -- donchian_breakout_atr_m30945c80 declares universe ['EURUSD', 'GBPUSD', 'USDJPY', 'AUDUSD', 'USDCAD', 'USDCHF', 'NZDUSD', 'EURJPY', 'GBPJPY', 'XAUUSD', 'US500', 'DE40'] and timeframes ['H4', 'D1']; running it on AUDJPY H4 would validate a strategy nobody wrote
- donchian_breakout_atr_m30945c80 on EURGBP H4: out_of_universe -- donchian_breakout_atr_m30945c80 declares universe ['EURUSD', 'GBPUSD', 'USDJPY', 'AUDUSD', 'USDCAD', 'USDCHF', 'NZDUSD', 'EURJPY', 'GBPJPY', 'XAUUSD', 'US500', 'DE40'] and timeframes ['H4', 'D1']; running it on EURGBP H4 would validate a strategy nobody wrote
- donchian_breakout_atr_m30945c80 on UK100 H4: out_of_universe -- donchian_breakout_atr_m30945c80 declares universe ['EURUSD', 'GBPUSD', 'USDJPY', 'AUDUSD', 'USDCAD', 'USDCHF', 'NZDUSD', 'EURJPY', 'GBPJPY', 'XAUUSD', 'US500', 'DE40'] and timeframes ['H4', 'D1']; running it on UK100 H4 would validate a strategy nobody wrote
- donchian_breakout_atr_m30945c80 on XAGUSD H4: out_of_universe -- donchian_breakout_atr_m30945c80 declares universe ['EURUSD', 'GBPUSD', 'USDJPY', 'AUDUSD', 'USDCAD', 'USDCHF', 'NZDUSD', 'EURJPY', 'GBPJPY', 'XAUUSD', 'US500', 'DE40'] and timeframes ['H4', 'D1']; running it on XAGUSD H4 would validate a strategy nobody wrote
- donchian_breakout_atr_m217dad55 on AUDJPY H4: out_of_universe -- donchian_breakout_atr_m217dad55 declares universe ['EURUSD', 'GBPUSD', 'USDJPY', 'AUDUSD', 'USDCAD', 'USDCHF', 'NZDUSD', 'EURJPY', 'GBPJPY', 'XAUUSD', 'US500', 'DE40'] and timeframes ['H4', 'D1']; running it on AUDJPY H4 would validate a strategy nobody wrote
- donchian_breakout_atr_m217dad55 on EURGBP H4: out_of_universe -- donchian_breakout_atr_m217dad55 declares universe ['EURUSD', 'GBPUSD', 'USDJPY', 'AUDUSD', 'USDCAD', 'USDCHF', 'NZDUSD', 'EURJPY', 'GBPJPY', 'XAUUSD', 'US500', 'DE40'] and timeframes ['H4', 'D1']; running it on EURGBP H4 would validate a strategy nobody wrote
- donchian_breakout_atr_m217dad55 on UK100 H4: out_of_universe -- donchian_breakout_atr_m217dad55 declares universe ['EURUSD', 'GBPUSD', 'USDJPY', 'AUDUSD', 'USDCAD', 'USDCHF', 'NZDUSD', 'EURJPY', 'GBPJPY', 'XAUUSD', 'US500', 'DE40'] and timeframes ['H4', 'D1']; running it on UK100 H4 would validate a strategy nobody wrote
- donchian_breakout_atr_m217dad55 on XAGUSD H4: out_of_universe -- donchian_breakout_atr_m217dad55 declares universe ['EURUSD', 'GBPUSD', 'USDJPY', 'AUDUSD', 'USDCAD', 'USDCHF', 'NZDUSD', 'EURJPY', 'GBPJPY', 'XAUUSD', 'US500', 'DE40'] and timeframes ['H4', 'D1']; running it on XAGUSD H4 would validate a strategy nobody wrote
- donchian_breakout_atr_m4d42994e on AUDJPY H4: out_of_universe -- donchian_breakout_atr_m4d42994e declares universe ['EURUSD', 'GBPUSD', 'USDJPY', 'AUDUSD', 'USDCAD', 'USDCHF', 'NZDUSD'] and timeframes ['H4']; running it on AUDJPY H4 would validate a strategy nobody wrote
- donchian_breakout_atr_m4d42994e on DE40 H4: out_of_universe -- donchian_breakout_atr_m4d42994e declares universe ['EURUSD', 'GBPUSD', 'USDJPY', 'AUDUSD', 'USDCAD', 'USDCHF', 'NZDUSD'] and timeframes ['H4']; running it on DE40 H4 would validate a strategy nobody wrote
- donchian_breakout_atr_m4d42994e on EURGBP H4: out_of_universe -- donchian_breakout_atr_m4d42994e declares universe ['EURUSD', 'GBPUSD', 'USDJPY', 'AUDUSD', 'USDCAD', 'USDCHF', 'NZDUSD'] and timeframes ['H4']; running it on EURGBP H4 would validate a strategy nobody wrote
- donchian_breakout_atr_m4d42994e on EURJPY H4: out_of_universe -- donchian_breakout_atr_m4d42994e declares universe ['EURUSD', 'GBPUSD', 'USDJPY', 'AUDUSD', 'USDCAD', 'USDCHF', 'NZDUSD'] and timeframes ['H4']; running it on EURJPY H4 would validate a strategy nobody wrote
- donchian_breakout_atr_m4d42994e on GBPJPY H4: out_of_universe -- donchian_breakout_atr_m4d42994e declares universe ['EURUSD', 'GBPUSD', 'USDJPY', 'AUDUSD', 'USDCAD', 'USDCHF', 'NZDUSD'] and timeframes ['H4']; running it on GBPJPY H4 would validate a strategy nobody wrote
- donchian_breakout_atr_m4d42994e on UK100 H4: out_of_universe -- donchian_breakout_atr_m4d42994e declares universe ['EURUSD', 'GBPUSD', 'USDJPY', 'AUDUSD', 'USDCAD', 'USDCHF', 'NZDUSD'] and timeframes ['H4']; running it on UK100 H4 would validate a strategy nobody wrote
- donchian_breakout_atr_m4d42994e on US500 H4: out_of_universe -- donchian_breakout_atr_m4d42994e declares universe ['EURUSD', 'GBPUSD', 'USDJPY', 'AUDUSD', 'USDCAD', 'USDCHF', 'NZDUSD'] and timeframes ['H4']; running it on US500 H4 would validate a strategy nobody wrote
- donchian_breakout_atr_m4d42994e on XAGUSD H4: out_of_universe -- donchian_breakout_atr_m4d42994e declares universe ['EURUSD', 'GBPUSD', 'USDJPY', 'AUDUSD', 'USDCAD', 'USDCHF', 'NZDUSD'] and timeframes ['H4']; running it on XAGUSD H4 would validate a strategy nobody wrote
- donchian_breakout_atr_m4d42994e on XAUUSD H4: out_of_universe -- donchian_breakout_atr_m4d42994e declares universe ['EURUSD', 'GBPUSD', 'USDJPY', 'AUDUSD', 'USDCAD', 'USDCHF', 'NZDUSD'] and timeframes ['H4']; running it on XAUUSD H4 would validate a strategy nobody wrote
- fib_golden_pocket_pullback_ma8f9f32b on AUDJPY H4: out_of_universe -- fib_golden_pocket_pullback_ma8f9f32b declares universe ['EURUSD', 'GBPUSD', 'USDJPY', 'AUDUSD', 'USDCAD', 'USDCHF', 'NZDUSD', 'EURJPY', 'GBPJPY', 'XAUUSD', 'US500', 'DE40'] and timeframes ['H1', 'H4', 'D1']; running it on AUDJPY H4 would validate a strategy nobody wrote
- fib_golden_pocket_pullback_ma8f9f32b on EURGBP H4: out_of_universe -- fib_golden_pocket_pullback_ma8f9f32b declares universe ['EURUSD', 'GBPUSD', 'USDJPY', 'AUDUSD', 'USDCAD', 'USDCHF', 'NZDUSD', 'EURJPY', 'GBPJPY', 'XAUUSD', 'US500', 'DE40'] and timeframes ['H1', 'H4', 'D1']; running it on EURGBP H4 would validate a strategy nobody wrote
- fib_golden_pocket_pullback_ma8f9f32b on UK100 H4: out_of_universe -- fib_golden_pocket_pullback_ma8f9f32b declares universe ['EURUSD', 'GBPUSD', 'USDJPY', 'AUDUSD', 'USDCAD', 'USDCHF', 'NZDUSD', 'EURJPY', 'GBPJPY', 'XAUUSD', 'US500', 'DE40'] and timeframes ['H1', 'H4', 'D1']; running it on UK100 H4 would validate a strategy nobody wrote
- fib_golden_pocket_pullback_ma8f9f32b on XAGUSD H4: out_of_universe -- fib_golden_pocket_pullback_ma8f9f32b declares universe ['EURUSD', 'GBPUSD', 'USDJPY', 'AUDUSD', 'USDCAD', 'USDCHF', 'NZDUSD', 'EURJPY', 'GBPJPY', 'XAUUSD', 'US500', 'DE40'] and timeframes ['H1', 'H4', 'D1']; running it on XAGUSD H4 would validate a strategy nobody wrote
- fib_golden_pocket_pullback_md15c64ac on AUDJPY H4: out_of_universe -- fib_golden_pocket_pullback_md15c64ac declares universe ['EURUSD', 'GBPUSD', 'USDJPY', 'AUDUSD', 'USDCAD', 'USDCHF', 'NZDUSD', 'EURJPY', 'GBPJPY', 'XAUUSD', 'US500', 'DE40'] and timeframes ['H1', 'H4', 'D1']; running it on AUDJPY H4 would validate a strategy nobody wrote
- fib_golden_pocket_pullback_md15c64ac on EURGBP H4: out_of_universe -- fib_golden_pocket_pullback_md15c64ac declares universe ['EURUSD', 'GBPUSD', 'USDJPY', 'AUDUSD', 'USDCAD', 'USDCHF', 'NZDUSD', 'EURJPY', 'GBPJPY', 'XAUUSD', 'US500', 'DE40'] and timeframes ['H1', 'H4', 'D1']; running it on EURGBP H4 would validate a strategy nobody wrote
- fib_golden_pocket_pullback_md15c64ac on UK100 H4: out_of_universe -- fib_golden_pocket_pullback_md15c64ac declares universe ['EURUSD', 'GBPUSD', 'USDJPY', 'AUDUSD', 'USDCAD', 'USDCHF', 'NZDUSD', 'EURJPY', 'GBPJPY', 'XAUUSD', 'US500', 'DE40'] and timeframes ['H1', 'H4', 'D1']; running it on UK100 H4 would validate a strategy nobody wrote
- fib_golden_pocket_pullback_md15c64ac on XAGUSD H4: out_of_universe -- fib_golden_pocket_pullback_md15c64ac declares universe ['EURUSD', 'GBPUSD', 'USDJPY', 'AUDUSD', 'USDCAD', 'USDCHF', 'NZDUSD', 'EURJPY', 'GBPJPY', 'XAUUSD', 'US500', 'DE40'] and timeframes ['H1', 'H4', 'D1']; running it on XAGUSD H4 would validate a strategy nobody wrote
- fib_golden_pocket_pullback_mb82cde01 on AUDJPY H4: out_of_universe -- fib_golden_pocket_pullback_mb82cde01 declares universe ['EURUSD', 'GBPUSD', 'USDJPY', 'AUDUSD', 'USDCAD', 'USDCHF', 'NZDUSD', 'EURJPY', 'GBPJPY', 'XAUUSD', 'US500', 'DE40'] and timeframes ['H1', 'H4', 'D1']; running it on AUDJPY H4 would validate a strategy nobody wrote
- fib_golden_pocket_pullback_mb82cde01 on EURGBP H4: out_of_universe -- fib_golden_pocket_pullback_mb82cde01 declares universe ['EURUSD', 'GBPUSD', 'USDJPY', 'AUDUSD', 'USDCAD', 'USDCHF', 'NZDUSD', 'EURJPY', 'GBPJPY', 'XAUUSD', 'US500', 'DE40'] and timeframes ['H1', 'H4', 'D1']; running it on EURGBP H4 would validate a strategy nobody wrote
- fib_golden_pocket_pullback_mb82cde01 on UK100 H4: out_of_universe -- fib_golden_pocket_pullback_mb82cde01 declares universe ['EURUSD', 'GBPUSD', 'USDJPY', 'AUDUSD', 'USDCAD', 'USDCHF', 'NZDUSD', 'EURJPY', 'GBPJPY', 'XAUUSD', 'US500', 'DE40'] and timeframes ['H1', 'H4', 'D1']; running it on UK100 H4 would validate a strategy nobody wrote
- fib_golden_pocket_pullback_mb82cde01 on XAGUSD H4: out_of_universe -- fib_golden_pocket_pullback_mb82cde01 declares universe ['EURUSD', 'GBPUSD', 'USDJPY', 'AUDUSD', 'USDCAD', 'USDCHF', 'NZDUSD', 'EURJPY', 'GBPJPY', 'XAUUSD', 'US500', 'DE40'] and timeframes ['H1', 'H4', 'D1']; running it on XAGUSD H4 would validate a strategy nobody wrote
- fib_golden_pocket_pullback_mdd2357fa on AUDJPY H4: out_of_universe -- fib_golden_pocket_pullback_mdd2357fa declares universe ['EURUSD', 'GBPUSD', 'USDJPY', 'AUDUSD', 'USDCAD', 'USDCHF', 'NZDUSD', 'EURJPY', 'GBPJPY', 'XAUUSD', 'US500', 'DE40'] and timeframes ['H1', 'H4', 'D1']; running it on AUDJPY H4 would validate a strategy nobody wrote
- fib_golden_pocket_pullback_mdd2357fa on EURGBP H4: out_of_universe -- fib_golden_pocket_pullback_mdd2357fa declares universe ['EURUSD', 'GBPUSD', 'USDJPY', 'AUDUSD', 'USDCAD', 'USDCHF', 'NZDUSD', 'EURJPY', 'GBPJPY', 'XAUUSD', 'US500', 'DE40'] and timeframes ['H1', 'H4', 'D1']; running it on EURGBP H4 would validate a strategy nobody wrote
- fib_golden_pocket_pullback_mdd2357fa on UK100 H4: out_of_universe -- fib_golden_pocket_pullback_mdd2357fa declares universe ['EURUSD', 'GBPUSD', 'USDJPY', 'AUDUSD', 'USDCAD', 'USDCHF', 'NZDUSD', 'EURJPY', 'GBPJPY', 'XAUUSD', 'US500', 'DE40'] and timeframes ['H1', 'H4', 'D1']; running it on UK100 H4 would validate a strategy nobody wrote
- fib_golden_pocket_pullback_mdd2357fa on XAGUSD H4: out_of_universe -- fib_golden_pocket_pullback_mdd2357fa declares universe ['EURUSD', 'GBPUSD', 'USDJPY', 'AUDUSD', 'USDCAD', 'USDCHF', 'NZDUSD', 'EURJPY', 'GBPJPY', 'XAUUSD', 'US500', 'DE40'] and timeframes ['H1', 'H4', 'D1']; running it on XAGUSD H4 would validate a strategy nobody wrote
- fib_golden_pocket_pullback_md3507b8f on AUDJPY H4: out_of_universe -- fib_golden_pocket_pullback_md3507b8f declares universe ['EURUSD', 'GBPUSD', 'USDJPY', 'AUDUSD', 'USDCAD', 'USDCHF', 'NZDUSD', 'EURJPY', 'GBPJPY', 'XAUUSD', 'US500', 'DE40'] and timeframes ['H1', 'H4', 'D1']; running it on AUDJPY H4 would validate a strategy nobody wrote
- fib_golden_pocket_pullback_md3507b8f on EURGBP H4: out_of_universe -- fib_golden_pocket_pullback_md3507b8f declares universe ['EURUSD', 'GBPUSD', 'USDJPY', 'AUDUSD', 'USDCAD', 'USDCHF', 'NZDUSD', 'EURJPY', 'GBPJPY', 'XAUUSD', 'US500', 'DE40'] and timeframes ['H1', 'H4', 'D1']; running it on EURGBP H4 would validate a strategy nobody wrote
- fib_golden_pocket_pullback_md3507b8f on UK100 H4: out_of_universe -- fib_golden_pocket_pullback_md3507b8f declares universe ['EURUSD', 'GBPUSD', 'USDJPY', 'AUDUSD', 'USDCAD', 'USDCHF', 'NZDUSD', 'EURJPY', 'GBPJPY', 'XAUUSD', 'US500', 'DE40'] and timeframes ['H1', 'H4', 'D1']; running it on UK100 H4 would validate a strategy nobody wrote
- fib_golden_pocket_pullback_md3507b8f on XAGUSD H4: out_of_universe -- fib_golden_pocket_pullback_md3507b8f declares universe ['EURUSD', 'GBPUSD', 'USDJPY', 'AUDUSD', 'USDCAD', 'USDCHF', 'NZDUSD', 'EURJPY', 'GBPJPY', 'XAUUSD', 'US500', 'DE40'] and timeframes ['H1', 'H4', 'D1']; running it on XAGUSD H4 would validate a strategy nobody wrote
- fib_golden_pocket_pullback_m88fd2d1c on AUDJPY H4: out_of_universe -- fib_golden_pocket_pullback_m88fd2d1c declares universe ['EURUSD', 'GBPUSD', 'USDJPY', 'AUDUSD', 'USDCAD', 'USDCHF', 'NZDUSD', 'EURJPY', 'GBPJPY', 'XAUUSD', 'US500', 'DE40'] and timeframes ['H1', 'H4', 'D1']; running it on AUDJPY H4 would validate a strategy nobody wrote
- fib_golden_pocket_pullback_m88fd2d1c on EURGBP H4: out_of_universe -- fib_golden_pocket_pullback_m88fd2d1c declares universe ['EURUSD', 'GBPUSD', 'USDJPY', 'AUDUSD', 'USDCAD', 'USDCHF', 'NZDUSD', 'EURJPY', 'GBPJPY', 'XAUUSD', 'US500', 'DE40'] and timeframes ['H1', 'H4', 'D1']; running it on EURGBP H4 would validate a strategy nobody wrote
- fib_golden_pocket_pullback_m88fd2d1c on UK100 H4: out_of_universe -- fib_golden_pocket_pullback_m88fd2d1c declares universe ['EURUSD', 'GBPUSD', 'USDJPY', 'AUDUSD', 'USDCAD', 'USDCHF', 'NZDUSD', 'EURJPY', 'GBPJPY', 'XAUUSD', 'US500', 'DE40'] and timeframes ['H1', 'H4', 'D1']; running it on UK100 H4 would validate a strategy nobody wrote
- fib_golden_pocket_pullback_m88fd2d1c on XAGUSD H4: out_of_universe -- fib_golden_pocket_pullback_m88fd2d1c declares universe ['EURUSD', 'GBPUSD', 'USDJPY', 'AUDUSD', 'USDCAD', 'USDCHF', 'NZDUSD', 'EURJPY', 'GBPJPY', 'XAUUSD', 'US500', 'DE40'] and timeframes ['H1', 'H4', 'D1']; running it on XAGUSD H4 would validate a strategy nobody wrote
- fib_golden_pocket_pullback_me01e2d1a on AUDJPY H4: out_of_universe -- fib_golden_pocket_pullback_me01e2d1a declares universe ['EURUSD', 'GBPUSD', 'USDJPY', 'AUDUSD', 'USDCAD', 'USDCHF', 'NZDUSD', 'EURJPY', 'GBPJPY', 'XAUUSD', 'US500', 'DE40'] and timeframes ['H1', 'H4', 'D1']; running it on AUDJPY H4 would validate a strategy nobody wrote
- fib_golden_pocket_pullback_me01e2d1a on EURGBP H4: out_of_universe -- fib_golden_pocket_pullback_me01e2d1a declares universe ['EURUSD', 'GBPUSD', 'USDJPY', 'AUDUSD', 'USDCAD', 'USDCHF', 'NZDUSD', 'EURJPY', 'GBPJPY', 'XAUUSD', 'US500', 'DE40'] and timeframes ['H1', 'H4', 'D1']; running it on EURGBP H4 would validate a strategy nobody wrote
- fib_golden_pocket_pullback_me01e2d1a on UK100 H4: out_of_universe -- fib_golden_pocket_pullback_me01e2d1a declares universe ['EURUSD', 'GBPUSD', 'USDJPY', 'AUDUSD', 'USDCAD', 'USDCHF', 'NZDUSD', 'EURJPY', 'GBPJPY', 'XAUUSD', 'US500', 'DE40'] and timeframes ['H1', 'H4', 'D1']; running it on UK100 H4 would validate a strategy nobody wrote
- fib_golden_pocket_pullback_me01e2d1a on XAGUSD H4: out_of_universe -- fib_golden_pocket_pullback_me01e2d1a declares universe ['EURUSD', 'GBPUSD', 'USDJPY', 'AUDUSD', 'USDCAD', 'USDCHF', 'NZDUSD', 'EURJPY', 'GBPJPY', 'XAUUSD', 'US500', 'DE40'] and timeframes ['H1', 'H4', 'D1']; running it on XAGUSD H4 would validate a strategy nobody wrote
- fib_golden_pocket_pullback_m76354e7e on AUDJPY H4: out_of_universe -- fib_golden_pocket_pullback_m76354e7e declares universe ['EURUSD', 'GBPUSD', 'USDJPY', 'AUDUSD', 'USDCAD', 'USDCHF', 'NZDUSD', 'EURJPY', 'GBPJPY', 'XAUUSD', 'US500', 'DE40'] and timeframes ['H4', 'D1']; running it on AUDJPY H4 would validate a strategy nobody wrote
- fib_golden_pocket_pullback_m76354e7e on EURGBP H4: out_of_universe -- fib_golden_pocket_pullback_m76354e7e declares universe ['EURUSD', 'GBPUSD', 'USDJPY', 'AUDUSD', 'USDCAD', 'USDCHF', 'NZDUSD', 'EURJPY', 'GBPJPY', 'XAUUSD', 'US500', 'DE40'] and timeframes ['H4', 'D1']; running it on EURGBP H4 would validate a strategy nobody wrote
- fib_golden_pocket_pullback_m76354e7e on UK100 H4: out_of_universe -- fib_golden_pocket_pullback_m76354e7e declares universe ['EURUSD', 'GBPUSD', 'USDJPY', 'AUDUSD', 'USDCAD', 'USDCHF', 'NZDUSD', 'EURJPY', 'GBPJPY', 'XAUUSD', 'US500', 'DE40'] and timeframes ['H4', 'D1']; running it on UK100 H4 would validate a strategy nobody wrote
- fib_golden_pocket_pullback_m76354e7e on XAGUSD H4: out_of_universe -- fib_golden_pocket_pullback_m76354e7e declares universe ['EURUSD', 'GBPUSD', 'USDJPY', 'AUDUSD', 'USDCAD', 'USDCHF', 'NZDUSD', 'EURJPY', 'GBPJPY', 'XAUUSD', 'US500', 'DE40'] and timeframes ['H4', 'D1']; running it on XAGUSD H4 would validate a strategy nobody wrote
- fib_golden_pocket_pullback_mb73e28fd on AUDJPY H4: out_of_universe -- fib_golden_pocket_pullback_mb73e28fd declares universe ['EURUSD', 'GBPUSD', 'USDJPY', 'AUDUSD', 'USDCAD', 'USDCHF', 'NZDUSD', 'EURJPY', 'GBPJPY', 'XAUUSD', 'US500', 'DE40'] and timeframes ['H4', 'D1']; running it on AUDJPY H4 would validate a strategy nobody wrote
- fib_golden_pocket_pullback_mb73e28fd on EURGBP H4: out_of_universe -- fib_golden_pocket_pullback_mb73e28fd declares universe ['EURUSD', 'GBPUSD', 'USDJPY', 'AUDUSD', 'USDCAD', 'USDCHF', 'NZDUSD', 'EURJPY', 'GBPJPY', 'XAUUSD', 'US500', 'DE40'] and timeframes ['H4', 'D1']; running it on EURGBP H4 would validate a strategy nobody wrote
- fib_golden_pocket_pullback_mb73e28fd on UK100 H4: out_of_universe -- fib_golden_pocket_pullback_mb73e28fd declares universe ['EURUSD', 'GBPUSD', 'USDJPY', 'AUDUSD', 'USDCAD', 'USDCHF', 'NZDUSD', 'EURJPY', 'GBPJPY', 'XAUUSD', 'US500', 'DE40'] and timeframes ['H4', 'D1']; running it on UK100 H4 would validate a strategy nobody wrote
- fib_golden_pocket_pullback_mb73e28fd on XAGUSD H4: out_of_universe -- fib_golden_pocket_pullback_mb73e28fd declares universe ['EURUSD', 'GBPUSD', 'USDJPY', 'AUDUSD', 'USDCAD', 'USDCHF', 'NZDUSD', 'EURJPY', 'GBPJPY', 'XAUUSD', 'US500', 'DE40'] and timeframes ['H4', 'D1']; running it on XAGUSD H4 would validate a strategy nobody wrote
- ichimoku_kumo_trend_m689694c1 on AUDJPY H4: out_of_universe -- ichimoku_kumo_trend_m689694c1 declares universe ['EURUSD', 'GBPUSD', 'USDJPY', 'AUDUSD', 'USDCAD', 'USDCHF', 'NZDUSD', 'EURJPY', 'GBPJPY', 'XAUUSD', 'US500', 'DE40'] and timeframes ['H4', 'D1']; running it on AUDJPY H4 would validate a strategy nobody wrote
- ichimoku_kumo_trend_m689694c1 on EURGBP H4: out_of_universe -- ichimoku_kumo_trend_m689694c1 declares universe ['EURUSD', 'GBPUSD', 'USDJPY', 'AUDUSD', 'USDCAD', 'USDCHF', 'NZDUSD', 'EURJPY', 'GBPJPY', 'XAUUSD', 'US500', 'DE40'] and timeframes ['H4', 'D1']; running it on EURGBP H4 would validate a strategy nobody wrote
- ichimoku_kumo_trend_m689694c1 on UK100 H4: out_of_universe -- ichimoku_kumo_trend_m689694c1 declares universe ['EURUSD', 'GBPUSD', 'USDJPY', 'AUDUSD', 'USDCAD', 'USDCHF', 'NZDUSD', 'EURJPY', 'GBPJPY', 'XAUUSD', 'US500', 'DE40'] and timeframes ['H4', 'D1']; running it on UK100 H4 would validate a strategy nobody wrote
- ichimoku_kumo_trend_m689694c1 on XAGUSD H4: out_of_universe -- ichimoku_kumo_trend_m689694c1 declares universe ['EURUSD', 'GBPUSD', 'USDJPY', 'AUDUSD', 'USDCAD', 'USDCHF', 'NZDUSD', 'EURJPY', 'GBPJPY', 'XAUUSD', 'US500', 'DE40'] and timeframes ['H4', 'D1']; running it on XAGUSD H4 would validate a strategy nobody wrote
- ichimoku_kumo_trend_m4195c8bf on AUDJPY H4: out_of_universe -- ichimoku_kumo_trend_m4195c8bf declares universe ['EURUSD', 'GBPUSD', 'USDJPY', 'AUDUSD', 'USDCAD', 'USDCHF', 'NZDUSD', 'EURJPY', 'GBPJPY', 'XAUUSD', 'US500', 'DE40'] and timeframes ['H4', 'D1']; running it on AUDJPY H4 would validate a strategy nobody wrote
- ichimoku_kumo_trend_m4195c8bf on EURGBP H4: out_of_universe -- ichimoku_kumo_trend_m4195c8bf declares universe ['EURUSD', 'GBPUSD', 'USDJPY', 'AUDUSD', 'USDCAD', 'USDCHF', 'NZDUSD', 'EURJPY', 'GBPJPY', 'XAUUSD', 'US500', 'DE40'] and timeframes ['H4', 'D1']; running it on EURGBP H4 would validate a strategy nobody wrote
- ichimoku_kumo_trend_m4195c8bf on UK100 H4: out_of_universe -- ichimoku_kumo_trend_m4195c8bf declares universe ['EURUSD', 'GBPUSD', 'USDJPY', 'AUDUSD', 'USDCAD', 'USDCHF', 'NZDUSD', 'EURJPY', 'GBPJPY', 'XAUUSD', 'US500', 'DE40'] and timeframes ['H4', 'D1']; running it on UK100 H4 would validate a strategy nobody wrote
- ichimoku_kumo_trend_m4195c8bf on XAGUSD H4: out_of_universe -- ichimoku_kumo_trend_m4195c8bf declares universe ['EURUSD', 'GBPUSD', 'USDJPY', 'AUDUSD', 'USDCAD', 'USDCHF', 'NZDUSD', 'EURJPY', 'GBPJPY', 'XAUUSD', 'US500', 'DE40'] and timeframes ['H4', 'D1']; running it on XAGUSD H4 would validate a strategy nobody wrote
- ichimoku_kumo_trend_m8fbcd36c on AUDJPY H4: out_of_universe -- ichimoku_kumo_trend_m8fbcd36c declares universe ['EURUSD', 'GBPUSD', 'USDJPY', 'AUDUSD', 'USDCAD', 'USDCHF', 'NZDUSD', 'EURJPY', 'GBPJPY', 'XAUUSD', 'US500', 'DE40'] and timeframes ['H4', 'D1']; running it on AUDJPY H4 would validate a strategy nobody wrote
- ichimoku_kumo_trend_m8fbcd36c on EURGBP H4: out_of_universe -- ichimoku_kumo_trend_m8fbcd36c declares universe ['EURUSD', 'GBPUSD', 'USDJPY', 'AUDUSD', 'USDCAD', 'USDCHF', 'NZDUSD', 'EURJPY', 'GBPJPY', 'XAUUSD', 'US500', 'DE40'] and timeframes ['H4', 'D1']; running it on EURGBP H4 would validate a strategy nobody wrote
- ichimoku_kumo_trend_m8fbcd36c on UK100 H4: out_of_universe -- ichimoku_kumo_trend_m8fbcd36c declares universe ['EURUSD', 'GBPUSD', 'USDJPY', 'AUDUSD', 'USDCAD', 'USDCHF', 'NZDUSD', 'EURJPY', 'GBPJPY', 'XAUUSD', 'US500', 'DE40'] and timeframes ['H4', 'D1']; running it on UK100 H4 would validate a strategy nobody wrote
- ichimoku_kumo_trend_m8fbcd36c on XAGUSD H4: out_of_universe -- ichimoku_kumo_trend_m8fbcd36c declares universe ['EURUSD', 'GBPUSD', 'USDJPY', 'AUDUSD', 'USDCAD', 'USDCHF', 'NZDUSD', 'EURJPY', 'GBPJPY', 'XAUUSD', 'US500', 'DE40'] and timeframes ['H4', 'D1']; running it on XAGUSD H4 would validate a strategy nobody wrote
- ichimoku_kumo_trend_ma4c92c38 on AUDJPY H4: out_of_universe -- ichimoku_kumo_trend_ma4c92c38 declares universe ['EURUSD', 'GBPUSD', 'USDJPY', 'AUDUSD', 'USDCAD', 'USDCHF', 'NZDUSD', 'EURJPY', 'GBPJPY', 'XAUUSD', 'US500', 'DE40'] and timeframes ['H4', 'D1']; running it on AUDJPY H4 would validate a strategy nobody wrote
- ichimoku_kumo_trend_ma4c92c38 on EURGBP H4: out_of_universe -- ichimoku_kumo_trend_ma4c92c38 declares universe ['EURUSD', 'GBPUSD', 'USDJPY', 'AUDUSD', 'USDCAD', 'USDCHF', 'NZDUSD', 'EURJPY', 'GBPJPY', 'XAUUSD', 'US500', 'DE40'] and timeframes ['H4', 'D1']; running it on EURGBP H4 would validate a strategy nobody wrote
- ichimoku_kumo_trend_ma4c92c38 on UK100 H4: out_of_universe -- ichimoku_kumo_trend_ma4c92c38 declares universe ['EURUSD', 'GBPUSD', 'USDJPY', 'AUDUSD', 'USDCAD', 'USDCHF', 'NZDUSD', 'EURJPY', 'GBPJPY', 'XAUUSD', 'US500', 'DE40'] and timeframes ['H4', 'D1']; running it on UK100 H4 would validate a strategy nobody wrote
- ichimoku_kumo_trend_ma4c92c38 on XAGUSD H4: out_of_universe -- ichimoku_kumo_trend_ma4c92c38 declares universe ['EURUSD', 'GBPUSD', 'USDJPY', 'AUDUSD', 'USDCAD', 'USDCHF', 'NZDUSD', 'EURJPY', 'GBPJPY', 'XAUUSD', 'US500', 'DE40'] and timeframes ['H4', 'D1']; running it on XAGUSD H4 would validate a strategy nobody wrote
- ichimoku_kumo_trend_me73acb6c on AUDJPY H4: out_of_universe -- ichimoku_kumo_trend_me73acb6c declares universe ['EURUSD', 'GBPUSD', 'USDJPY', 'AUDUSD', 'USDCAD', 'USDCHF', 'NZDUSD', 'EURJPY', 'GBPJPY', 'XAUUSD', 'US500', 'DE40'] and timeframes ['H4', 'D1']; running it on AUDJPY H4 would validate a strategy nobody wrote
- ichimoku_kumo_trend_me73acb6c on EURGBP H4: out_of_universe -- ichimoku_kumo_trend_me73acb6c declares universe ['EURUSD', 'GBPUSD', 'USDJPY', 'AUDUSD', 'USDCAD', 'USDCHF', 'NZDUSD', 'EURJPY', 'GBPJPY', 'XAUUSD', 'US500', 'DE40'] and timeframes ['H4', 'D1']; running it on EURGBP H4 would validate a strategy nobody wrote
- ichimoku_kumo_trend_me73acb6c on UK100 H4: out_of_universe -- ichimoku_kumo_trend_me73acb6c declares universe ['EURUSD', 'GBPUSD', 'USDJPY', 'AUDUSD', 'USDCAD', 'USDCHF', 'NZDUSD', 'EURJPY', 'GBPJPY', 'XAUUSD', 'US500', 'DE40'] and timeframes ['H4', 'D1']; running it on UK100 H4 would validate a strategy nobody wrote
- ichimoku_kumo_trend_me73acb6c on XAGUSD H4: out_of_universe -- ichimoku_kumo_trend_me73acb6c declares universe ['EURUSD', 'GBPUSD', 'USDJPY', 'AUDUSD', 'USDCAD', 'USDCHF', 'NZDUSD', 'EURJPY', 'GBPJPY', 'XAUUSD', 'US500', 'DE40'] and timeframes ['H4', 'D1']; running it on XAGUSD H4 would validate a strategy nobody wrote
- ichimoku_kumo_trend_mca231a0f on AUDJPY H4: out_of_universe -- ichimoku_kumo_trend_mca231a0f declares universe ['EURUSD', 'GBPUSD', 'USDJPY', 'AUDUSD', 'USDCAD', 'USDCHF', 'NZDUSD', 'EURJPY', 'GBPJPY', 'XAUUSD', 'US500', 'DE40'] and timeframes ['H4', 'D1']; running it on AUDJPY H4 would validate a strategy nobody wrote
- ichimoku_kumo_trend_mca231a0f on EURGBP H4: out_of_universe -- ichimoku_kumo_trend_mca231a0f declares universe ['EURUSD', 'GBPUSD', 'USDJPY', 'AUDUSD', 'USDCAD', 'USDCHF', 'NZDUSD', 'EURJPY', 'GBPJPY', 'XAUUSD', 'US500', 'DE40'] and timeframes ['H4', 'D1']; running it on EURGBP H4 would validate a strategy nobody wrote
- ichimoku_kumo_trend_mca231a0f on UK100 H4: out_of_universe -- ichimoku_kumo_trend_mca231a0f declares universe ['EURUSD', 'GBPUSD', 'USDJPY', 'AUDUSD', 'USDCAD', 'USDCHF', 'NZDUSD', 'EURJPY', 'GBPJPY', 'XAUUSD', 'US500', 'DE40'] and timeframes ['H4', 'D1']; running it on UK100 H4 would validate a strategy nobody wrote
- ichimoku_kumo_trend_mca231a0f on XAGUSD H4: out_of_universe -- ichimoku_kumo_trend_mca231a0f declares universe ['EURUSD', 'GBPUSD', 'USDJPY', 'AUDUSD', 'USDCAD', 'USDCHF', 'NZDUSD', 'EURJPY', 'GBPJPY', 'XAUUSD', 'US500', 'DE40'] and timeframes ['H4', 'D1']; running it on XAGUSD H4 would validate a strategy nobody wrote
- ichimoku_kumo_trend_m0bd80cab on AUDJPY H4: out_of_universe -- ichimoku_kumo_trend_m0bd80cab declares universe ['EURUSD', 'GBPUSD', 'USDJPY', 'AUDUSD', 'USDCAD', 'USDCHF', 'NZDUSD', 'EURJPY', 'GBPJPY', 'XAUUSD', 'US500', 'DE40'] and timeframes ['H4', 'D1']; running it on AUDJPY H4 would validate a strategy nobody wrote
- ichimoku_kumo_trend_m0bd80cab on EURGBP H4: out_of_universe -- ichimoku_kumo_trend_m0bd80cab declares universe ['EURUSD', 'GBPUSD', 'USDJPY', 'AUDUSD', 'USDCAD', 'USDCHF', 'NZDUSD', 'EURJPY', 'GBPJPY', 'XAUUSD', 'US500', 'DE40'] and timeframes ['H4', 'D1']; running it on EURGBP H4 would validate a strategy nobody wrote
- ichimoku_kumo_trend_m0bd80cab on UK100 H4: out_of_universe -- ichimoku_kumo_trend_m0bd80cab declares universe ['EURUSD', 'GBPUSD', 'USDJPY', 'AUDUSD', 'USDCAD', 'USDCHF', 'NZDUSD', 'EURJPY', 'GBPJPY', 'XAUUSD', 'US500', 'DE40'] and timeframes ['H4', 'D1']; running it on UK100 H4 would validate a strategy nobody wrote
- ichimoku_kumo_trend_m0bd80cab on XAGUSD H4: out_of_universe -- ichimoku_kumo_trend_m0bd80cab declares universe ['EURUSD', 'GBPUSD', 'USDJPY', 'AUDUSD', 'USDCAD', 'USDCHF', 'NZDUSD', 'EURJPY', 'GBPJPY', 'XAUUSD', 'US500', 'DE40'] and timeframes ['H4', 'D1']; running it on XAGUSD H4 would validate a strategy nobody wrote
- ichimoku_kumo_trend_m67c7da5a on AUDJPY H4: out_of_universe -- ichimoku_kumo_trend_m67c7da5a declares universe ['EURUSD', 'GBPUSD', 'USDJPY', 'AUDUSD', 'USDCAD', 'USDCHF', 'NZDUSD'] and timeframes ['H4']; running it on AUDJPY H4 would validate a strategy nobody wrote
- ichimoku_kumo_trend_m67c7da5a on DE40 H4: out_of_universe -- ichimoku_kumo_trend_m67c7da5a declares universe ['EURUSD', 'GBPUSD', 'USDJPY', 'AUDUSD', 'USDCAD', 'USDCHF', 'NZDUSD'] and timeframes ['H4']; running it on DE40 H4 would validate a strategy nobody wrote
- ichimoku_kumo_trend_m67c7da5a on EURGBP H4: out_of_universe -- ichimoku_kumo_trend_m67c7da5a declares universe ['EURUSD', 'GBPUSD', 'USDJPY', 'AUDUSD', 'USDCAD', 'USDCHF', 'NZDUSD'] and timeframes ['H4']; running it on EURGBP H4 would validate a strategy nobody wrote
- ichimoku_kumo_trend_m67c7da5a on EURJPY H4: out_of_universe -- ichimoku_kumo_trend_m67c7da5a declares universe ['EURUSD', 'GBPUSD', 'USDJPY', 'AUDUSD', 'USDCAD', 'USDCHF', 'NZDUSD'] and timeframes ['H4']; running it on EURJPY H4 would validate a strategy nobody wrote
- ichimoku_kumo_trend_m67c7da5a on GBPJPY H4: out_of_universe -- ichimoku_kumo_trend_m67c7da5a declares universe ['EURUSD', 'GBPUSD', 'USDJPY', 'AUDUSD', 'USDCAD', 'USDCHF', 'NZDUSD'] and timeframes ['H4']; running it on GBPJPY H4 would validate a strategy nobody wrote
- ichimoku_kumo_trend_m67c7da5a on UK100 H4: out_of_universe -- ichimoku_kumo_trend_m67c7da5a declares universe ['EURUSD', 'GBPUSD', 'USDJPY', 'AUDUSD', 'USDCAD', 'USDCHF', 'NZDUSD'] and timeframes ['H4']; running it on UK100 H4 would validate a strategy nobody wrote
- ichimoku_kumo_trend_m67c7da5a on US500 H4: out_of_universe -- ichimoku_kumo_trend_m67c7da5a declares universe ['EURUSD', 'GBPUSD', 'USDJPY', 'AUDUSD', 'USDCAD', 'USDCHF', 'NZDUSD'] and timeframes ['H4']; running it on US500 H4 would validate a strategy nobody wrote
- ichimoku_kumo_trend_m67c7da5a on XAGUSD H4: out_of_universe -- ichimoku_kumo_trend_m67c7da5a declares universe ['EURUSD', 'GBPUSD', 'USDJPY', 'AUDUSD', 'USDCAD', 'USDCHF', 'NZDUSD'] and timeframes ['H4']; running it on XAGUSD H4 would validate a strategy nobody wrote
- ichimoku_kumo_trend_m67c7da5a on XAUUSD H4: out_of_universe -- ichimoku_kumo_trend_m67c7da5a declares universe ['EURUSD', 'GBPUSD', 'USDJPY', 'AUDUSD', 'USDCAD', 'USDCHF', 'NZDUSD'] and timeframes ['H4']; running it on XAUUSD H4 would validate a strategy nobody wrote
- macd_ema_trend_hybrid_ma2bc336b on AUDJPY H4: out_of_universe -- macd_ema_trend_hybrid_ma2bc336b declares universe ['EURUSD', 'GBPUSD', 'USDJPY', 'AUDUSD', 'USDCAD', 'USDCHF', 'NZDUSD', 'EURJPY', 'GBPJPY', 'XAUUSD', 'US500', 'DE40'] and timeframes ['H4', 'D1']; running it on AUDJPY H4 would validate a strategy nobody wrote
- macd_ema_trend_hybrid_ma2bc336b on EURGBP H4: out_of_universe -- macd_ema_trend_hybrid_ma2bc336b declares universe ['EURUSD', 'GBPUSD', 'USDJPY', 'AUDUSD', 'USDCAD', 'USDCHF', 'NZDUSD', 'EURJPY', 'GBPJPY', 'XAUUSD', 'US500', 'DE40'] and timeframes ['H4', 'D1']; running it on EURGBP H4 would validate a strategy nobody wrote
- macd_ema_trend_hybrid_ma2bc336b on UK100 H4: out_of_universe -- macd_ema_trend_hybrid_ma2bc336b declares universe ['EURUSD', 'GBPUSD', 'USDJPY', 'AUDUSD', 'USDCAD', 'USDCHF', 'NZDUSD', 'EURJPY', 'GBPJPY', 'XAUUSD', 'US500', 'DE40'] and timeframes ['H4', 'D1']; running it on UK100 H4 would validate a strategy nobody wrote
- macd_ema_trend_hybrid_ma2bc336b on XAGUSD H4: out_of_universe -- macd_ema_trend_hybrid_ma2bc336b declares universe ['EURUSD', 'GBPUSD', 'USDJPY', 'AUDUSD', 'USDCAD', 'USDCHF', 'NZDUSD', 'EURJPY', 'GBPJPY', 'XAUUSD', 'US500', 'DE40'] and timeframes ['H4', 'D1']; running it on XAGUSD H4 would validate a strategy nobody wrote
- macd_ema_trend_hybrid_m03d3ee23 on AUDJPY H4: out_of_universe -- macd_ema_trend_hybrid_m03d3ee23 declares universe ['EURUSD', 'GBPUSD', 'USDJPY', 'AUDUSD', 'USDCAD', 'USDCHF', 'NZDUSD', 'EURJPY', 'GBPJPY', 'XAUUSD', 'US500', 'DE40'] and timeframes ['H4', 'D1']; running it on AUDJPY H4 would validate a strategy nobody wrote
- macd_ema_trend_hybrid_m03d3ee23 on EURGBP H4: out_of_universe -- macd_ema_trend_hybrid_m03d3ee23 declares universe ['EURUSD', 'GBPUSD', 'USDJPY', 'AUDUSD', 'USDCAD', 'USDCHF', 'NZDUSD', 'EURJPY', 'GBPJPY', 'XAUUSD', 'US500', 'DE40'] and timeframes ['H4', 'D1']; running it on EURGBP H4 would validate a strategy nobody wrote
- macd_ema_trend_hybrid_m03d3ee23 on UK100 H4: out_of_universe -- macd_ema_trend_hybrid_m03d3ee23 declares universe ['EURUSD', 'GBPUSD', 'USDJPY', 'AUDUSD', 'USDCAD', 'USDCHF', 'NZDUSD', 'EURJPY', 'GBPJPY', 'XAUUSD', 'US500', 'DE40'] and timeframes ['H4', 'D1']; running it on UK100 H4 would validate a strategy nobody wrote
- macd_ema_trend_hybrid_m03d3ee23 on XAGUSD H4: out_of_universe -- macd_ema_trend_hybrid_m03d3ee23 declares universe ['EURUSD', 'GBPUSD', 'USDJPY', 'AUDUSD', 'USDCAD', 'USDCHF', 'NZDUSD', 'EURJPY', 'GBPJPY', 'XAUUSD', 'US500', 'DE40'] and timeframes ['H4', 'D1']; running it on XAGUSD H4 would validate a strategy nobody wrote
- macd_ema_trend_hybrid_m3728054d on AUDJPY H4: out_of_universe -- macd_ema_trend_hybrid_m3728054d declares universe ['EURUSD', 'GBPUSD', 'USDJPY', 'AUDUSD', 'USDCAD', 'USDCHF', 'NZDUSD', 'EURJPY', 'GBPJPY', 'XAUUSD', 'US500', 'DE40'] and timeframes ['H4', 'D1']; running it on AUDJPY H4 would validate a strategy nobody wrote
- macd_ema_trend_hybrid_m3728054d on EURGBP H4: out_of_universe -- macd_ema_trend_hybrid_m3728054d declares universe ['EURUSD', 'GBPUSD', 'USDJPY', 'AUDUSD', 'USDCAD', 'USDCHF', 'NZDUSD', 'EURJPY', 'GBPJPY', 'XAUUSD', 'US500', 'DE40'] and timeframes ['H4', 'D1']; running it on EURGBP H4 would validate a strategy nobody wrote
- macd_ema_trend_hybrid_m3728054d on UK100 H4: out_of_universe -- macd_ema_trend_hybrid_m3728054d declares universe ['EURUSD', 'GBPUSD', 'USDJPY', 'AUDUSD', 'USDCAD', 'USDCHF', 'NZDUSD', 'EURJPY', 'GBPJPY', 'XAUUSD', 'US500', 'DE40'] and timeframes ['H4', 'D1']; running it on UK100 H4 would validate a strategy nobody wrote
- macd_ema_trend_hybrid_m3728054d on XAGUSD H4: out_of_universe -- macd_ema_trend_hybrid_m3728054d declares universe ['EURUSD', 'GBPUSD', 'USDJPY', 'AUDUSD', 'USDCAD', 'USDCHF', 'NZDUSD', 'EURJPY', 'GBPJPY', 'XAUUSD', 'US500', 'DE40'] and timeframes ['H4', 'D1']; running it on XAGUSD H4 would validate a strategy nobody wrote
- macd_ema_trend_hybrid_m4b9a1dcd on AUDJPY H4: out_of_universe -- macd_ema_trend_hybrid_m4b9a1dcd declares universe ['EURUSD', 'GBPUSD', 'USDJPY', 'AUDUSD', 'USDCAD', 'USDCHF', 'NZDUSD', 'EURJPY', 'GBPJPY', 'XAUUSD', 'US500', 'DE40'] and timeframes ['H4', 'D1']; running it on AUDJPY H4 would validate a strategy nobody wrote
- macd_ema_trend_hybrid_m4b9a1dcd on EURGBP H4: out_of_universe -- macd_ema_trend_hybrid_m4b9a1dcd declares universe ['EURUSD', 'GBPUSD', 'USDJPY', 'AUDUSD', 'USDCAD', 'USDCHF', 'NZDUSD', 'EURJPY', 'GBPJPY', 'XAUUSD', 'US500', 'DE40'] and timeframes ['H4', 'D1']; running it on EURGBP H4 would validate a strategy nobody wrote
- macd_ema_trend_hybrid_m4b9a1dcd on UK100 H4: out_of_universe -- macd_ema_trend_hybrid_m4b9a1dcd declares universe ['EURUSD', 'GBPUSD', 'USDJPY', 'AUDUSD', 'USDCAD', 'USDCHF', 'NZDUSD', 'EURJPY', 'GBPJPY', 'XAUUSD', 'US500', 'DE40'] and timeframes ['H4', 'D1']; running it on UK100 H4 would validate a strategy nobody wrote
- macd_ema_trend_hybrid_m4b9a1dcd on XAGUSD H4: out_of_universe -- macd_ema_trend_hybrid_m4b9a1dcd declares universe ['EURUSD', 'GBPUSD', 'USDJPY', 'AUDUSD', 'USDCAD', 'USDCHF', 'NZDUSD', 'EURJPY', 'GBPJPY', 'XAUUSD', 'US500', 'DE40'] and timeframes ['H4', 'D1']; running it on XAGUSD H4 would validate a strategy nobody wrote
- macd_ema_trend_hybrid_m03f1d536 on AUDJPY H4: out_of_universe -- macd_ema_trend_hybrid_m03f1d536 declares universe ['EURUSD', 'GBPUSD', 'USDJPY', 'AUDUSD', 'USDCAD', 'USDCHF', 'NZDUSD', 'EURJPY', 'GBPJPY', 'XAUUSD', 'US500', 'DE40'] and timeframes ['H4', 'D1']; running it on AUDJPY H4 would validate a strategy nobody wrote
- macd_ema_trend_hybrid_m03f1d536 on EURGBP H4: out_of_universe -- macd_ema_trend_hybrid_m03f1d536 declares universe ['EURUSD', 'GBPUSD', 'USDJPY', 'AUDUSD', 'USDCAD', 'USDCHF', 'NZDUSD', 'EURJPY', 'GBPJPY', 'XAUUSD', 'US500', 'DE40'] and timeframes ['H4', 'D1']; running it on EURGBP H4 would validate a strategy nobody wrote
- macd_ema_trend_hybrid_m03f1d536 on UK100 H4: out_of_universe -- macd_ema_trend_hybrid_m03f1d536 declares universe ['EURUSD', 'GBPUSD', 'USDJPY', 'AUDUSD', 'USDCAD', 'USDCHF', 'NZDUSD', 'EURJPY', 'GBPJPY', 'XAUUSD', 'US500', 'DE40'] and timeframes ['H4', 'D1']; running it on UK100 H4 would validate a strategy nobody wrote
- macd_ema_trend_hybrid_m03f1d536 on XAGUSD H4: out_of_universe -- macd_ema_trend_hybrid_m03f1d536 declares universe ['EURUSD', 'GBPUSD', 'USDJPY', 'AUDUSD', 'USDCAD', 'USDCHF', 'NZDUSD', 'EURJPY', 'GBPJPY', 'XAUUSD', 'US500', 'DE40'] and timeframes ['H4', 'D1']; running it on XAGUSD H4 would validate a strategy nobody wrote
- macd_ema_trend_hybrid_mb7889927 on AUDJPY H4: out_of_universe -- macd_ema_trend_hybrid_mb7889927 declares universe ['EURUSD', 'GBPUSD', 'USDJPY', 'AUDUSD', 'USDCAD', 'USDCHF', 'NZDUSD', 'EURJPY', 'GBPJPY', 'XAUUSD', 'US500', 'DE40'] and timeframes ['H4', 'D1']; running it on AUDJPY H4 would validate a strategy nobody wrote
- macd_ema_trend_hybrid_mb7889927 on EURGBP H4: out_of_universe -- macd_ema_trend_hybrid_mb7889927 declares universe ['EURUSD', 'GBPUSD', 'USDJPY', 'AUDUSD', 'USDCAD', 'USDCHF', 'NZDUSD', 'EURJPY', 'GBPJPY', 'XAUUSD', 'US500', 'DE40'] and timeframes ['H4', 'D1']; running it on EURGBP H4 would validate a strategy nobody wrote
- macd_ema_trend_hybrid_mb7889927 on UK100 H4: out_of_universe -- macd_ema_trend_hybrid_mb7889927 declares universe ['EURUSD', 'GBPUSD', 'USDJPY', 'AUDUSD', 'USDCAD', 'USDCHF', 'NZDUSD', 'EURJPY', 'GBPJPY', 'XAUUSD', 'US500', 'DE40'] and timeframes ['H4', 'D1']; running it on UK100 H4 would validate a strategy nobody wrote
- macd_ema_trend_hybrid_mb7889927 on XAGUSD H4: out_of_universe -- macd_ema_trend_hybrid_mb7889927 declares universe ['EURUSD', 'GBPUSD', 'USDJPY', 'AUDUSD', 'USDCAD', 'USDCHF', 'NZDUSD', 'EURJPY', 'GBPJPY', 'XAUUSD', 'US500', 'DE40'] and timeframes ['H4', 'D1']; running it on XAGUSD H4 would validate a strategy nobody wrote
- macd_ema_trend_hybrid_mc45594a0 on AUDJPY H4: out_of_universe -- macd_ema_trend_hybrid_mc45594a0 declares universe ['EURUSD', 'GBPUSD', 'USDJPY', 'AUDUSD', 'USDCAD', 'USDCHF', 'NZDUSD', 'EURJPY', 'GBPJPY', 'XAUUSD', 'US500', 'DE40'] and timeframes ['H4', 'D1']; running it on AUDJPY H4 would validate a strategy nobody wrote
- macd_ema_trend_hybrid_mc45594a0 on EURGBP H4: out_of_universe -- macd_ema_trend_hybrid_mc45594a0 declares universe ['EURUSD', 'GBPUSD', 'USDJPY', 'AUDUSD', 'USDCAD', 'USDCHF', 'NZDUSD', 'EURJPY', 'GBPJPY', 'XAUUSD', 'US500', 'DE40'] and timeframes ['H4', 'D1']; running it on EURGBP H4 would validate a strategy nobody wrote
- macd_ema_trend_hybrid_mc45594a0 on UK100 H4: out_of_universe -- macd_ema_trend_hybrid_mc45594a0 declares universe ['EURUSD', 'GBPUSD', 'USDJPY', 'AUDUSD', 'USDCAD', 'USDCHF', 'NZDUSD', 'EURJPY', 'GBPJPY', 'XAUUSD', 'US500', 'DE40'] and timeframes ['H4', 'D1']; running it on UK100 H4 would validate a strategy nobody wrote
- macd_ema_trend_hybrid_mc45594a0 on XAGUSD H4: out_of_universe -- macd_ema_trend_hybrid_mc45594a0 declares universe ['EURUSD', 'GBPUSD', 'USDJPY', 'AUDUSD', 'USDCAD', 'USDCHF', 'NZDUSD', 'EURJPY', 'GBPJPY', 'XAUUSD', 'US500', 'DE40'] and timeframes ['H4', 'D1']; running it on XAGUSD H4 would validate a strategy nobody wrote
- macd_ema_trend_hybrid_m270351c7 on AUDJPY H4: out_of_universe -- macd_ema_trend_hybrid_m270351c7 declares universe ['EURUSD', 'GBPUSD', 'USDJPY', 'AUDUSD', 'USDCAD', 'USDCHF', 'NZDUSD', 'EURJPY', 'GBPJPY', 'XAUUSD', 'US500', 'DE40'] and timeframes ['H4', 'D1']; running it on AUDJPY H4 would validate a strategy nobody wrote
- macd_ema_trend_hybrid_m270351c7 on EURGBP H4: out_of_universe -- macd_ema_trend_hybrid_m270351c7 declares universe ['EURUSD', 'GBPUSD', 'USDJPY', 'AUDUSD', 'USDCAD', 'USDCHF', 'NZDUSD', 'EURJPY', 'GBPJPY', 'XAUUSD', 'US500', 'DE40'] and timeframes ['H4', 'D1']; running it on EURGBP H4 would validate a strategy nobody wrote
- macd_ema_trend_hybrid_m270351c7 on UK100 H4: out_of_universe -- macd_ema_trend_hybrid_m270351c7 declares universe ['EURUSD', 'GBPUSD', 'USDJPY', 'AUDUSD', 'USDCAD', 'USDCHF', 'NZDUSD', 'EURJPY', 'GBPJPY', 'XAUUSD', 'US500', 'DE40'] and timeframes ['H4', 'D1']; running it on UK100 H4 would validate a strategy nobody wrote
- macd_ema_trend_hybrid_m270351c7 on XAGUSD H4: out_of_universe -- macd_ema_trend_hybrid_m270351c7 declares universe ['EURUSD', 'GBPUSD', 'USDJPY', 'AUDUSD', 'USDCAD', 'USDCHF', 'NZDUSD', 'EURJPY', 'GBPJPY', 'XAUUSD', 'US500', 'DE40'] and timeframes ['H4', 'D1']; running it on XAGUSD H4 would validate a strategy nobody wrote
- macd_ema_trend_hybrid_mbd5c916c on AUDJPY H4: out_of_universe -- macd_ema_trend_hybrid_mbd5c916c declares universe ['EURUSD', 'GBPUSD', 'USDJPY', 'AUDUSD', 'USDCAD', 'USDCHF', 'NZDUSD'] and timeframes ['H4']; running it on AUDJPY H4 would validate a strategy nobody wrote
- macd_ema_trend_hybrid_mbd5c916c on DE40 H4: out_of_universe -- macd_ema_trend_hybrid_mbd5c916c declares universe ['EURUSD', 'GBPUSD', 'USDJPY', 'AUDUSD', 'USDCAD', 'USDCHF', 'NZDUSD'] and timeframes ['H4']; running it on DE40 H4 would validate a strategy nobody wrote
- macd_ema_trend_hybrid_mbd5c916c on EURGBP H4: out_of_universe -- macd_ema_trend_hybrid_mbd5c916c declares universe ['EURUSD', 'GBPUSD', 'USDJPY', 'AUDUSD', 'USDCAD', 'USDCHF', 'NZDUSD'] and timeframes ['H4']; running it on EURGBP H4 would validate a strategy nobody wrote
- macd_ema_trend_hybrid_mbd5c916c on EURJPY H4: out_of_universe -- macd_ema_trend_hybrid_mbd5c916c declares universe ['EURUSD', 'GBPUSD', 'USDJPY', 'AUDUSD', 'USDCAD', 'USDCHF', 'NZDUSD'] and timeframes ['H4']; running it on EURJPY H4 would validate a strategy nobody wrote
- macd_ema_trend_hybrid_mbd5c916c on GBPJPY H4: out_of_universe -- macd_ema_trend_hybrid_mbd5c916c declares universe ['EURUSD', 'GBPUSD', 'USDJPY', 'AUDUSD', 'USDCAD', 'USDCHF', 'NZDUSD'] and timeframes ['H4']; running it on GBPJPY H4 would validate a strategy nobody wrote
- macd_ema_trend_hybrid_mbd5c916c on UK100 H4: out_of_universe -- macd_ema_trend_hybrid_mbd5c916c declares universe ['EURUSD', 'GBPUSD', 'USDJPY', 'AUDUSD', 'USDCAD', 'USDCHF', 'NZDUSD'] and timeframes ['H4']; running it on UK100 H4 would validate a strategy nobody wrote
- macd_ema_trend_hybrid_mbd5c916c on US500 H4: out_of_universe -- macd_ema_trend_hybrid_mbd5c916c declares universe ['EURUSD', 'GBPUSD', 'USDJPY', 'AUDUSD', 'USDCAD', 'USDCHF', 'NZDUSD'] and timeframes ['H4']; running it on US500 H4 would validate a strategy nobody wrote
- macd_ema_trend_hybrid_mbd5c916c on XAGUSD H4: out_of_universe -- macd_ema_trend_hybrid_mbd5c916c declares universe ['EURUSD', 'GBPUSD', 'USDJPY', 'AUDUSD', 'USDCAD', 'USDCHF', 'NZDUSD'] and timeframes ['H4']; running it on XAGUSD H4 would validate a strategy nobody wrote
- macd_ema_trend_hybrid_mbd5c916c on XAUUSD H4: out_of_universe -- macd_ema_trend_hybrid_mbd5c916c declares universe ['EURUSD', 'GBPUSD', 'USDJPY', 'AUDUSD', 'USDCAD', 'USDCHF', 'NZDUSD'] and timeframes ['H4']; running it on XAUUSD H4 would validate a strategy nobody wrote
- rsi_band_mean_reversion_md48b52cb on AUDJPY H4: out_of_universe -- rsi_band_mean_reversion_md48b52cb declares universe ['EURUSD', 'GBPUSD', 'USDJPY', 'AUDUSD', 'USDCAD', 'USDCHF', 'NZDUSD', 'EURGBP', 'AUDNZD', 'EURCHF'] and timeframes ['H1', 'H4']; running it on AUDJPY H4 would validate a strategy nobody wrote
- rsi_band_mean_reversion_md48b52cb on DE40 H4: out_of_universe -- rsi_band_mean_reversion_md48b52cb declares universe ['EURUSD', 'GBPUSD', 'USDJPY', 'AUDUSD', 'USDCAD', 'USDCHF', 'NZDUSD', 'EURGBP', 'AUDNZD', 'EURCHF'] and timeframes ['H1', 'H4']; running it on DE40 H4 would validate a strategy nobody wrote
- rsi_band_mean_reversion_md48b52cb on EURJPY H4: out_of_universe -- rsi_band_mean_reversion_md48b52cb declares universe ['EURUSD', 'GBPUSD', 'USDJPY', 'AUDUSD', 'USDCAD', 'USDCHF', 'NZDUSD', 'EURGBP', 'AUDNZD', 'EURCHF'] and timeframes ['H1', 'H4']; running it on EURJPY H4 would validate a strategy nobody wrote
- rsi_band_mean_reversion_md48b52cb on GBPJPY H4: out_of_universe -- rsi_band_mean_reversion_md48b52cb declares universe ['EURUSD', 'GBPUSD', 'USDJPY', 'AUDUSD', 'USDCAD', 'USDCHF', 'NZDUSD', 'EURGBP', 'AUDNZD', 'EURCHF'] and timeframes ['H1', 'H4']; running it on GBPJPY H4 would validate a strategy nobody wrote
- rsi_band_mean_reversion_md48b52cb on UK100 H4: out_of_universe -- rsi_band_mean_reversion_md48b52cb declares universe ['EURUSD', 'GBPUSD', 'USDJPY', 'AUDUSD', 'USDCAD', 'USDCHF', 'NZDUSD', 'EURGBP', 'AUDNZD', 'EURCHF'] and timeframes ['H1', 'H4']; running it on UK100 H4 would validate a strategy nobody wrote
- rsi_band_mean_reversion_md48b52cb on US500 H4: out_of_universe -- rsi_band_mean_reversion_md48b52cb declares universe ['EURUSD', 'GBPUSD', 'USDJPY', 'AUDUSD', 'USDCAD', 'USDCHF', 'NZDUSD', 'EURGBP', 'AUDNZD', 'EURCHF'] and timeframes ['H1', 'H4']; running it on US500 H4 would validate a strategy nobody wrote
- rsi_band_mean_reversion_md48b52cb on XAGUSD H4: out_of_universe -- rsi_band_mean_reversion_md48b52cb declares universe ['EURUSD', 'GBPUSD', 'USDJPY', 'AUDUSD', 'USDCAD', 'USDCHF', 'NZDUSD', 'EURGBP', 'AUDNZD', 'EURCHF'] and timeframes ['H1', 'H4']; running it on XAGUSD H4 would validate a strategy nobody wrote
- rsi_band_mean_reversion_md48b52cb on XAUUSD H4: out_of_universe -- rsi_band_mean_reversion_md48b52cb declares universe ['EURUSD', 'GBPUSD', 'USDJPY', 'AUDUSD', 'USDCAD', 'USDCHF', 'NZDUSD', 'EURGBP', 'AUDNZD', 'EURCHF'] and timeframes ['H1', 'H4']; running it on XAUUSD H4 would validate a strategy nobody wrote
- rsi_band_mean_reversion_mdbb3266a on AUDJPY H4: out_of_universe -- rsi_band_mean_reversion_mdbb3266a declares universe ['EURUSD', 'GBPUSD', 'USDJPY', 'AUDUSD', 'USDCAD', 'USDCHF', 'NZDUSD', 'EURGBP', 'AUDNZD', 'EURCHF'] and timeframes ['H1', 'H4']; running it on AUDJPY H4 would validate a strategy nobody wrote
- rsi_band_mean_reversion_mdbb3266a on DE40 H4: out_of_universe -- rsi_band_mean_reversion_mdbb3266a declares universe ['EURUSD', 'GBPUSD', 'USDJPY', 'AUDUSD', 'USDCAD', 'USDCHF', 'NZDUSD', 'EURGBP', 'AUDNZD', 'EURCHF'] and timeframes ['H1', 'H4']; running it on DE40 H4 would validate a strategy nobody wrote
- rsi_band_mean_reversion_mdbb3266a on EURJPY H4: out_of_universe -- rsi_band_mean_reversion_mdbb3266a declares universe ['EURUSD', 'GBPUSD', 'USDJPY', 'AUDUSD', 'USDCAD', 'USDCHF', 'NZDUSD', 'EURGBP', 'AUDNZD', 'EURCHF'] and timeframes ['H1', 'H4']; running it on EURJPY H4 would validate a strategy nobody wrote
- rsi_band_mean_reversion_mdbb3266a on GBPJPY H4: out_of_universe -- rsi_band_mean_reversion_mdbb3266a declares universe ['EURUSD', 'GBPUSD', 'USDJPY', 'AUDUSD', 'USDCAD', 'USDCHF', 'NZDUSD', 'EURGBP', 'AUDNZD', 'EURCHF'] and timeframes ['H1', 'H4']; running it on GBPJPY H4 would validate a strategy nobody wrote
- rsi_band_mean_reversion_mdbb3266a on UK100 H4: out_of_universe -- rsi_band_mean_reversion_mdbb3266a declares universe ['EURUSD', 'GBPUSD', 'USDJPY', 'AUDUSD', 'USDCAD', 'USDCHF', 'NZDUSD', 'EURGBP', 'AUDNZD', 'EURCHF'] and timeframes ['H1', 'H4']; running it on UK100 H4 would validate a strategy nobody wrote
- rsi_band_mean_reversion_mdbb3266a on US500 H4: out_of_universe -- rsi_band_mean_reversion_mdbb3266a declares universe ['EURUSD', 'GBPUSD', 'USDJPY', 'AUDUSD', 'USDCAD', 'USDCHF', 'NZDUSD', 'EURGBP', 'AUDNZD', 'EURCHF'] and timeframes ['H1', 'H4']; running it on US500 H4 would validate a strategy nobody wrote
- rsi_band_mean_reversion_mdbb3266a on XAGUSD H4: out_of_universe -- rsi_band_mean_reversion_mdbb3266a declares universe ['EURUSD', 'GBPUSD', 'USDJPY', 'AUDUSD', 'USDCAD', 'USDCHF', 'NZDUSD', 'EURGBP', 'AUDNZD', 'EURCHF'] and timeframes ['H1', 'H4']; running it on XAGUSD H4 would validate a strategy nobody wrote
- rsi_band_mean_reversion_mdbb3266a on XAUUSD H4: out_of_universe -- rsi_band_mean_reversion_mdbb3266a declares universe ['EURUSD', 'GBPUSD', 'USDJPY', 'AUDUSD', 'USDCAD', 'USDCHF', 'NZDUSD', 'EURGBP', 'AUDNZD', 'EURCHF'] and timeframes ['H1', 'H4']; running it on XAUUSD H4 would validate a strategy nobody wrote
- rsi_band_mean_reversion_m6e0465f0 on AUDJPY H4: out_of_universe -- rsi_band_mean_reversion_m6e0465f0 declares universe ['EURUSD', 'GBPUSD', 'USDJPY', 'AUDUSD', 'USDCAD', 'USDCHF', 'NZDUSD', 'EURGBP', 'AUDNZD', 'EURCHF'] and timeframes ['H1', 'H4']; running it on AUDJPY H4 would validate a strategy nobody wrote
- rsi_band_mean_reversion_m6e0465f0 on DE40 H4: out_of_universe -- rsi_band_mean_reversion_m6e0465f0 declares universe ['EURUSD', 'GBPUSD', 'USDJPY', 'AUDUSD', 'USDCAD', 'USDCHF', 'NZDUSD', 'EURGBP', 'AUDNZD', 'EURCHF'] and timeframes ['H1', 'H4']; running it on DE40 H4 would validate a strategy nobody wrote
- rsi_band_mean_reversion_m6e0465f0 on EURJPY H4: out_of_universe -- rsi_band_mean_reversion_m6e0465f0 declares universe ['EURUSD', 'GBPUSD', 'USDJPY', 'AUDUSD', 'USDCAD', 'USDCHF', 'NZDUSD', 'EURGBP', 'AUDNZD', 'EURCHF'] and timeframes ['H1', 'H4']; running it on EURJPY H4 would validate a strategy nobody wrote
- rsi_band_mean_reversion_m6e0465f0 on GBPJPY H4: out_of_universe -- rsi_band_mean_reversion_m6e0465f0 declares universe ['EURUSD', 'GBPUSD', 'USDJPY', 'AUDUSD', 'USDCAD', 'USDCHF', 'NZDUSD', 'EURGBP', 'AUDNZD', 'EURCHF'] and timeframes ['H1', 'H4']; running it on GBPJPY H4 would validate a strategy nobody wrote
- rsi_band_mean_reversion_m6e0465f0 on UK100 H4: out_of_universe -- rsi_band_mean_reversion_m6e0465f0 declares universe ['EURUSD', 'GBPUSD', 'USDJPY', 'AUDUSD', 'USDCAD', 'USDCHF', 'NZDUSD', 'EURGBP', 'AUDNZD', 'EURCHF'] and timeframes ['H1', 'H4']; running it on UK100 H4 would validate a strategy nobody wrote
- rsi_band_mean_reversion_m6e0465f0 on US500 H4: out_of_universe -- rsi_band_mean_reversion_m6e0465f0 declares universe ['EURUSD', 'GBPUSD', 'USDJPY', 'AUDUSD', 'USDCAD', 'USDCHF', 'NZDUSD', 'EURGBP', 'AUDNZD', 'EURCHF'] and timeframes ['H1', 'H4']; running it on US500 H4 would validate a strategy nobody wrote
- rsi_band_mean_reversion_m6e0465f0 on XAGUSD H4: out_of_universe -- rsi_band_mean_reversion_m6e0465f0 declares universe ['EURUSD', 'GBPUSD', 'USDJPY', 'AUDUSD', 'USDCAD', 'USDCHF', 'NZDUSD', 'EURGBP', 'AUDNZD', 'EURCHF'] and timeframes ['H1', 'H4']; running it on XAGUSD H4 would validate a strategy nobody wrote
- rsi_band_mean_reversion_m6e0465f0 on XAUUSD H4: out_of_universe -- rsi_band_mean_reversion_m6e0465f0 declares universe ['EURUSD', 'GBPUSD', 'USDJPY', 'AUDUSD', 'USDCAD', 'USDCHF', 'NZDUSD', 'EURGBP', 'AUDNZD', 'EURCHF'] and timeframes ['H1', 'H4']; running it on XAUUSD H4 would validate a strategy nobody wrote
- rsi_band_mean_reversion_me1d6d436 on AUDJPY H4: out_of_universe -- rsi_band_mean_reversion_me1d6d436 declares universe ['EURUSD', 'GBPUSD', 'USDJPY', 'AUDUSD', 'USDCAD', 'USDCHF', 'NZDUSD', 'EURGBP', 'AUDNZD', 'EURCHF'] and timeframes ['H1', 'H4']; running it on AUDJPY H4 would validate a strategy nobody wrote
- rsi_band_mean_reversion_me1d6d436 on DE40 H4: out_of_universe -- rsi_band_mean_reversion_me1d6d436 declares universe ['EURUSD', 'GBPUSD', 'USDJPY', 'AUDUSD', 'USDCAD', 'USDCHF', 'NZDUSD', 'EURGBP', 'AUDNZD', 'EURCHF'] and timeframes ['H1', 'H4']; running it on DE40 H4 would validate a strategy nobody wrote
- rsi_band_mean_reversion_me1d6d436 on EURJPY H4: out_of_universe -- rsi_band_mean_reversion_me1d6d436 declares universe ['EURUSD', 'GBPUSD', 'USDJPY', 'AUDUSD', 'USDCAD', 'USDCHF', 'NZDUSD', 'EURGBP', 'AUDNZD', 'EURCHF'] and timeframes ['H1', 'H4']; running it on EURJPY H4 would validate a strategy nobody wrote
- rsi_band_mean_reversion_me1d6d436 on GBPJPY H4: out_of_universe -- rsi_band_mean_reversion_me1d6d436 declares universe ['EURUSD', 'GBPUSD', 'USDJPY', 'AUDUSD', 'USDCAD', 'USDCHF', 'NZDUSD', 'EURGBP', 'AUDNZD', 'EURCHF'] and timeframes ['H1', 'H4']; running it on GBPJPY H4 would validate a strategy nobody wrote
- rsi_band_mean_reversion_me1d6d436 on UK100 H4: out_of_universe -- rsi_band_mean_reversion_me1d6d436 declares universe ['EURUSD', 'GBPUSD', 'USDJPY', 'AUDUSD', 'USDCAD', 'USDCHF', 'NZDUSD', 'EURGBP', 'AUDNZD', 'EURCHF'] and timeframes ['H1', 'H4']; running it on UK100 H4 would validate a strategy nobody wrote
- rsi_band_mean_reversion_me1d6d436 on US500 H4: out_of_universe -- rsi_band_mean_reversion_me1d6d436 declares universe ['EURUSD', 'GBPUSD', 'USDJPY', 'AUDUSD', 'USDCAD', 'USDCHF', 'NZDUSD', 'EURGBP', 'AUDNZD', 'EURCHF'] and timeframes ['H1', 'H4']; running it on US500 H4 would validate a strategy nobody wrote
- rsi_band_mean_reversion_me1d6d436 on XAGUSD H4: out_of_universe -- rsi_band_mean_reversion_me1d6d436 declares universe ['EURUSD', 'GBPUSD', 'USDJPY', 'AUDUSD', 'USDCAD', 'USDCHF', 'NZDUSD', 'EURGBP', 'AUDNZD', 'EURCHF'] and timeframes ['H1', 'H4']; running it on XAGUSD H4 would validate a strategy nobody wrote
- rsi_band_mean_reversion_me1d6d436 on XAUUSD H4: out_of_universe -- rsi_band_mean_reversion_me1d6d436 declares universe ['EURUSD', 'GBPUSD', 'USDJPY', 'AUDUSD', 'USDCAD', 'USDCHF', 'NZDUSD', 'EURGBP', 'AUDNZD', 'EURCHF'] and timeframes ['H1', 'H4']; running it on XAUUSD H4 would validate a strategy nobody wrote
- rsi_band_mean_reversion_m42a36d28 on AUDJPY H4: out_of_universe -- rsi_band_mean_reversion_m42a36d28 declares universe ['EURUSD', 'GBPUSD', 'USDJPY', 'AUDUSD', 'USDCAD', 'USDCHF', 'NZDUSD', 'EURGBP', 'AUDNZD', 'EURCHF'] and timeframes ['H1', 'H4']; running it on AUDJPY H4 would validate a strategy nobody wrote
- rsi_band_mean_reversion_m42a36d28 on DE40 H4: out_of_universe -- rsi_band_mean_reversion_m42a36d28 declares universe ['EURUSD', 'GBPUSD', 'USDJPY', 'AUDUSD', 'USDCAD', 'USDCHF', 'NZDUSD', 'EURGBP', 'AUDNZD', 'EURCHF'] and timeframes ['H1', 'H4']; running it on DE40 H4 would validate a strategy nobody wrote
- rsi_band_mean_reversion_m42a36d28 on EURJPY H4: out_of_universe -- rsi_band_mean_reversion_m42a36d28 declares universe ['EURUSD', 'GBPUSD', 'USDJPY', 'AUDUSD', 'USDCAD', 'USDCHF', 'NZDUSD', 'EURGBP', 'AUDNZD', 'EURCHF'] and timeframes ['H1', 'H4']; running it on EURJPY H4 would validate a strategy nobody wrote
- rsi_band_mean_reversion_m42a36d28 on GBPJPY H4: out_of_universe -- rsi_band_mean_reversion_m42a36d28 declares universe ['EURUSD', 'GBPUSD', 'USDJPY', 'AUDUSD', 'USDCAD', 'USDCHF', 'NZDUSD', 'EURGBP', 'AUDNZD', 'EURCHF'] and timeframes ['H1', 'H4']; running it on GBPJPY H4 would validate a strategy nobody wrote
- rsi_band_mean_reversion_m42a36d28 on UK100 H4: out_of_universe -- rsi_band_mean_reversion_m42a36d28 declares universe ['EURUSD', 'GBPUSD', 'USDJPY', 'AUDUSD', 'USDCAD', 'USDCHF', 'NZDUSD', 'EURGBP', 'AUDNZD', 'EURCHF'] and timeframes ['H1', 'H4']; running it on UK100 H4 would validate a strategy nobody wrote
- rsi_band_mean_reversion_m42a36d28 on US500 H4: out_of_universe -- rsi_band_mean_reversion_m42a36d28 declares universe ['EURUSD', 'GBPUSD', 'USDJPY', 'AUDUSD', 'USDCAD', 'USDCHF', 'NZDUSD', 'EURGBP', 'AUDNZD', 'EURCHF'] and timeframes ['H1', 'H4']; running it on US500 H4 would validate a strategy nobody wrote
- rsi_band_mean_reversion_m42a36d28 on XAGUSD H4: out_of_universe -- rsi_band_mean_reversion_m42a36d28 declares universe ['EURUSD', 'GBPUSD', 'USDJPY', 'AUDUSD', 'USDCAD', 'USDCHF', 'NZDUSD', 'EURGBP', 'AUDNZD', 'EURCHF'] and timeframes ['H1', 'H4']; running it on XAGUSD H4 would validate a strategy nobody wrote
- rsi_band_mean_reversion_m42a36d28 on XAUUSD H4: out_of_universe -- rsi_band_mean_reversion_m42a36d28 declares universe ['EURUSD', 'GBPUSD', 'USDJPY', 'AUDUSD', 'USDCAD', 'USDCHF', 'NZDUSD', 'EURGBP', 'AUDNZD', 'EURCHF'] and timeframes ['H1', 'H4']; running it on XAUUSD H4 would validate a strategy nobody wrote
- rsi_band_mean_reversion_m7c32d514 on AUDJPY H4: out_of_universe -- rsi_band_mean_reversion_m7c32d514 declares universe ['EURUSD', 'GBPUSD', 'USDJPY', 'AUDUSD', 'USDCAD', 'USDCHF', 'NZDUSD', 'EURGBP', 'AUDNZD', 'EURCHF'] and timeframes ['H1', 'H4']; running it on AUDJPY H4 would validate a strategy nobody wrote
- rsi_band_mean_reversion_m7c32d514 on DE40 H4: out_of_universe -- rsi_band_mean_reversion_m7c32d514 declares universe ['EURUSD', 'GBPUSD', 'USDJPY', 'AUDUSD', 'USDCAD', 'USDCHF', 'NZDUSD', 'EURGBP', 'AUDNZD', 'EURCHF'] and timeframes ['H1', 'H4']; running it on DE40 H4 would validate a strategy nobody wrote
- rsi_band_mean_reversion_m7c32d514 on EURJPY H4: out_of_universe -- rsi_band_mean_reversion_m7c32d514 declares universe ['EURUSD', 'GBPUSD', 'USDJPY', 'AUDUSD', 'USDCAD', 'USDCHF', 'NZDUSD', 'EURGBP', 'AUDNZD', 'EURCHF'] and timeframes ['H1', 'H4']; running it on EURJPY H4 would validate a strategy nobody wrote
- rsi_band_mean_reversion_m7c32d514 on GBPJPY H4: out_of_universe -- rsi_band_mean_reversion_m7c32d514 declares universe ['EURUSD', 'GBPUSD', 'USDJPY', 'AUDUSD', 'USDCAD', 'USDCHF', 'NZDUSD', 'EURGBP', 'AUDNZD', 'EURCHF'] and timeframes ['H1', 'H4']; running it on GBPJPY H4 would validate a strategy nobody wrote
- rsi_band_mean_reversion_m7c32d514 on UK100 H4: out_of_universe -- rsi_band_mean_reversion_m7c32d514 declares universe ['EURUSD', 'GBPUSD', 'USDJPY', 'AUDUSD', 'USDCAD', 'USDCHF', 'NZDUSD', 'EURGBP', 'AUDNZD', 'EURCHF'] and timeframes ['H1', 'H4']; running it on UK100 H4 would validate a strategy nobody wrote
- rsi_band_mean_reversion_m7c32d514 on US500 H4: out_of_universe -- rsi_band_mean_reversion_m7c32d514 declares universe ['EURUSD', 'GBPUSD', 'USDJPY', 'AUDUSD', 'USDCAD', 'USDCHF', 'NZDUSD', 'EURGBP', 'AUDNZD', 'EURCHF'] and timeframes ['H1', 'H4']; running it on US500 H4 would validate a strategy nobody wrote
- rsi_band_mean_reversion_m7c32d514 on XAGUSD H4: out_of_universe -- rsi_band_mean_reversion_m7c32d514 declares universe ['EURUSD', 'GBPUSD', 'USDJPY', 'AUDUSD', 'USDCAD', 'USDCHF', 'NZDUSD', 'EURGBP', 'AUDNZD', 'EURCHF'] and timeframes ['H1', 'H4']; running it on XAGUSD H4 would validate a strategy nobody wrote
- rsi_band_mean_reversion_m7c32d514 on XAUUSD H4: out_of_universe -- rsi_band_mean_reversion_m7c32d514 declares universe ['EURUSD', 'GBPUSD', 'USDJPY', 'AUDUSD', 'USDCAD', 'USDCHF', 'NZDUSD', 'EURGBP', 'AUDNZD', 'EURCHF'] and timeframes ['H1', 'H4']; running it on XAUUSD H4 would validate a strategy nobody wrote
- rsi_band_mean_reversion_m6113daf1 on AUDJPY H4: out_of_universe -- rsi_band_mean_reversion_m6113daf1 declares universe ['EURUSD', 'GBPUSD', 'USDJPY', 'AUDUSD', 'USDCAD', 'USDCHF', 'NZDUSD'] and timeframes ['H4']; running it on AUDJPY H4 would validate a strategy nobody wrote
- rsi_band_mean_reversion_m6113daf1 on DE40 H4: out_of_universe -- rsi_band_mean_reversion_m6113daf1 declares universe ['EURUSD', 'GBPUSD', 'USDJPY', 'AUDUSD', 'USDCAD', 'USDCHF', 'NZDUSD'] and timeframes ['H4']; running it on DE40 H4 would validate a strategy nobody wrote
- rsi_band_mean_reversion_m6113daf1 on EURGBP H4: out_of_universe -- rsi_band_mean_reversion_m6113daf1 declares universe ['EURUSD', 'GBPUSD', 'USDJPY', 'AUDUSD', 'USDCAD', 'USDCHF', 'NZDUSD'] and timeframes ['H4']; running it on EURGBP H4 would validate a strategy nobody wrote
- rsi_band_mean_reversion_m6113daf1 on EURJPY H4: out_of_universe -- rsi_band_mean_reversion_m6113daf1 declares universe ['EURUSD', 'GBPUSD', 'USDJPY', 'AUDUSD', 'USDCAD', 'USDCHF', 'NZDUSD'] and timeframes ['H4']; running it on EURJPY H4 would validate a strategy nobody wrote
- rsi_band_mean_reversion_m6113daf1 on GBPJPY H4: out_of_universe -- rsi_band_mean_reversion_m6113daf1 declares universe ['EURUSD', 'GBPUSD', 'USDJPY', 'AUDUSD', 'USDCAD', 'USDCHF', 'NZDUSD'] and timeframes ['H4']; running it on GBPJPY H4 would validate a strategy nobody wrote
- rsi_band_mean_reversion_m6113daf1 on UK100 H4: out_of_universe -- rsi_band_mean_reversion_m6113daf1 declares universe ['EURUSD', 'GBPUSD', 'USDJPY', 'AUDUSD', 'USDCAD', 'USDCHF', 'NZDUSD'] and timeframes ['H4']; running it on UK100 H4 would validate a strategy nobody wrote
- rsi_band_mean_reversion_m6113daf1 on US500 H4: out_of_universe -- rsi_band_mean_reversion_m6113daf1 declares universe ['EURUSD', 'GBPUSD', 'USDJPY', 'AUDUSD', 'USDCAD', 'USDCHF', 'NZDUSD'] and timeframes ['H4']; running it on US500 H4 would validate a strategy nobody wrote
- rsi_band_mean_reversion_m6113daf1 on XAGUSD H4: out_of_universe -- rsi_band_mean_reversion_m6113daf1 declares universe ['EURUSD', 'GBPUSD', 'USDJPY', 'AUDUSD', 'USDCAD', 'USDCHF', 'NZDUSD'] and timeframes ['H4']; running it on XAGUSD H4 would validate a strategy nobody wrote
- rsi_band_mean_reversion_m6113daf1 on XAUUSD H4: out_of_universe -- rsi_band_mean_reversion_m6113daf1 declares universe ['EURUSD', 'GBPUSD', 'USDJPY', 'AUDUSD', 'USDCAD', 'USDCHF', 'NZDUSD'] and timeframes ['H4']; running it on XAUUSD H4 would validate a strategy nobody wrote
- rsi_band_mean_reversion_md0807760 on AUDJPY H4: out_of_universe -- rsi_band_mean_reversion_md0807760 declares universe ['EURUSD', 'GBPUSD', 'USDJPY', 'AUDUSD', 'USDCAD', 'USDCHF', 'NZDUSD'] and timeframes ['H4']; running it on AUDJPY H4 would validate a strategy nobody wrote
- rsi_band_mean_reversion_md0807760 on DE40 H4: out_of_universe -- rsi_band_mean_reversion_md0807760 declares universe ['EURUSD', 'GBPUSD', 'USDJPY', 'AUDUSD', 'USDCAD', 'USDCHF', 'NZDUSD'] and timeframes ['H4']; running it on DE40 H4 would validate a strategy nobody wrote
- rsi_band_mean_reversion_md0807760 on EURGBP H4: out_of_universe -- rsi_band_mean_reversion_md0807760 declares universe ['EURUSD', 'GBPUSD', 'USDJPY', 'AUDUSD', 'USDCAD', 'USDCHF', 'NZDUSD'] and timeframes ['H4']; running it on EURGBP H4 would validate a strategy nobody wrote
- rsi_band_mean_reversion_md0807760 on EURJPY H4: out_of_universe -- rsi_band_mean_reversion_md0807760 declares universe ['EURUSD', 'GBPUSD', 'USDJPY', 'AUDUSD', 'USDCAD', 'USDCHF', 'NZDUSD'] and timeframes ['H4']; running it on EURJPY H4 would validate a strategy nobody wrote
- rsi_band_mean_reversion_md0807760 on GBPJPY H4: out_of_universe -- rsi_band_mean_reversion_md0807760 declares universe ['EURUSD', 'GBPUSD', 'USDJPY', 'AUDUSD', 'USDCAD', 'USDCHF', 'NZDUSD'] and timeframes ['H4']; running it on GBPJPY H4 would validate a strategy nobody wrote
- rsi_band_mean_reversion_md0807760 on UK100 H4: out_of_universe -- rsi_band_mean_reversion_md0807760 declares universe ['EURUSD', 'GBPUSD', 'USDJPY', 'AUDUSD', 'USDCAD', 'USDCHF', 'NZDUSD'] and timeframes ['H4']; running it on UK100 H4 would validate a strategy nobody wrote
- rsi_band_mean_reversion_md0807760 on US500 H4: out_of_universe -- rsi_band_mean_reversion_md0807760 declares universe ['EURUSD', 'GBPUSD', 'USDJPY', 'AUDUSD', 'USDCAD', 'USDCHF', 'NZDUSD'] and timeframes ['H4']; running it on US500 H4 would validate a strategy nobody wrote
- rsi_band_mean_reversion_md0807760 on XAGUSD H4: out_of_universe -- rsi_band_mean_reversion_md0807760 declares universe ['EURUSD', 'GBPUSD', 'USDJPY', 'AUDUSD', 'USDCAD', 'USDCHF', 'NZDUSD'] and timeframes ['H4']; running it on XAGUSD H4 would validate a strategy nobody wrote
- rsi_band_mean_reversion_md0807760 on XAUUSD H4: out_of_universe -- rsi_band_mean_reversion_md0807760 declares universe ['EURUSD', 'GBPUSD', 'USDJPY', 'AUDUSD', 'USDCAD', 'USDCHF', 'NZDUSD'] and timeframes ['H4']; running it on XAUUSD H4 would validate a strategy nobody wrote
- rsi_band_mean_reversion_m29180d06 on AUDJPY H4: out_of_universe -- rsi_band_mean_reversion_m29180d06 declares universe ['EURUSD', 'GBPUSD', 'USDJPY', 'AUDUSD', 'USDCAD', 'USDCHF', 'NZDUSD'] and timeframes ['H4']; running it on AUDJPY H4 would validate a strategy nobody wrote
- rsi_band_mean_reversion_m29180d06 on DE40 H4: out_of_universe -- rsi_band_mean_reversion_m29180d06 declares universe ['EURUSD', 'GBPUSD', 'USDJPY', 'AUDUSD', 'USDCAD', 'USDCHF', 'NZDUSD'] and timeframes ['H4']; running it on DE40 H4 would validate a strategy nobody wrote
- rsi_band_mean_reversion_m29180d06 on EURGBP H4: out_of_universe -- rsi_band_mean_reversion_m29180d06 declares universe ['EURUSD', 'GBPUSD', 'USDJPY', 'AUDUSD', 'USDCAD', 'USDCHF', 'NZDUSD'] and timeframes ['H4']; running it on EURGBP H4 would validate a strategy nobody wrote
- rsi_band_mean_reversion_m29180d06 on EURJPY H4: out_of_universe -- rsi_band_mean_reversion_m29180d06 declares universe ['EURUSD', 'GBPUSD', 'USDJPY', 'AUDUSD', 'USDCAD', 'USDCHF', 'NZDUSD'] and timeframes ['H4']; running it on EURJPY H4 would validate a strategy nobody wrote
- rsi_band_mean_reversion_m29180d06 on GBPJPY H4: out_of_universe -- rsi_band_mean_reversion_m29180d06 declares universe ['EURUSD', 'GBPUSD', 'USDJPY', 'AUDUSD', 'USDCAD', 'USDCHF', 'NZDUSD'] and timeframes ['H4']; running it on GBPJPY H4 would validate a strategy nobody wrote
- rsi_band_mean_reversion_m29180d06 on UK100 H4: out_of_universe -- rsi_band_mean_reversion_m29180d06 declares universe ['EURUSD', 'GBPUSD', 'USDJPY', 'AUDUSD', 'USDCAD', 'USDCHF', 'NZDUSD'] and timeframes ['H4']; running it on UK100 H4 would validate a strategy nobody wrote
- rsi_band_mean_reversion_m29180d06 on US500 H4: out_of_universe -- rsi_band_mean_reversion_m29180d06 declares universe ['EURUSD', 'GBPUSD', 'USDJPY', 'AUDUSD', 'USDCAD', 'USDCHF', 'NZDUSD'] and timeframes ['H4']; running it on US500 H4 would validate a strategy nobody wrote
- rsi_band_mean_reversion_m29180d06 on XAGUSD H4: out_of_universe -- rsi_band_mean_reversion_m29180d06 declares universe ['EURUSD', 'GBPUSD', 'USDJPY', 'AUDUSD', 'USDCAD', 'USDCHF', 'NZDUSD'] and timeframes ['H4']; running it on XAGUSD H4 would validate a strategy nobody wrote
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

- RUNG 0 SANITY: 526
- RUNG 1 IN_SAMPLE_SCREEN: 2
- RUNG 2 WALK_FORWARD: 12
- RUNG 4 ROBUSTNESS: 1
- RUNG 5 DEFLATION: 1

## Holdout

```
{
  "consumptions": [],
  "segments": [
    {
      "dataset_version_id": "ds_0bafc32b4ed7b8065261e7a4",
      "data_start": "2006-01-04T01:00:00+00:00",
      "data_end": "2025-12-31T21:00:00+00:00",
      "holdout_start": "2022-01-01T12:12:00+00:00",
      "holdout_fraction": 0.2,
      "label": "EURGBP H4",
      "holdout_window": {
        "name": "holdout::ds_0bafc32b4ed7b8065261e7a4",
        "start": "2022-01-01T12:12:00+00:00",
        "end": "2025-12-31T21:00:00+00:00"
      },
      "research_window": {
        "name": "research::ds_0bafc32b4ed7b8065261e7a4",
        "start": "2006-01-04T01:00:00+00:00",
        "end": "2022-01-01T12:12:00+00:00"
      }
    },
    {
      "dataset_version_id": "ds_300921ee00603a414fc03e98",
      "data_start": "2006-01-04T01:00:00+00:00",
      "data_end": "2025-12-31T21:00:00+00:00",
      "holdout_start": "2022-01-01T12:12:00+00:00",
      "holdout_fraction": 0.2,
      "label": "USDJPY H4",
      "holdout_window": {
        "name": "holdout::ds_300921ee00603a414fc03e98",
        "start": "2022-01-01T12:12:00+00:00",
        "end": "2025-12-31T21:00:00+00:00"
      },
      "research_window": {
        "name": "research::ds_300921ee00603a414fc03e98",
        "start": "2006-01-04T01:00:00+00:00",
        "end": "2022-01-01T12:12:00+00:00"
      }
    },
    {
      "dataset_version_id": "ds_5a2b99b30031d45d3546c1ca",
      "data_start": "2006-01-04T01:00:00+00:00",
      "data_end": "2025-12-31T21:00:00+00:00",
      "holdout_start": "2022-01-01T12:12:00+00:00",
      "holdout_fraction": 0.2,
      "label": "USDCHF H4",
      "holdout_window": {
        "name": "holdout::ds_5a2b99b30031d45d3546c1ca",
        "start": "2022-01-01T12:12:00+00:00",
        "end": "2025-12-31T21:00:00+00:00"
      },
      "research_window": {
        "name": "research::ds_5a2b99b30031d45d3546c1ca",
        "start": "2006-01-04T01:00:00+00:00",
        "end": "2022-01-01T12:12:00+00:00"
      }
    },
    {
      "dataset_version_id": "ds_9f7d5da5c796bd86da1011e8",
      "data_start": "2006-01-04T01:00:00+00:00",
      "data_end": "2025-12-31T21:00:00+00:00",
      "holdout_start": "2022-01-01T12:12:00+00:00",
      "holdout_fraction": 0.2,
      "label": "GBPJPY H4",
      "holdout_window": {
        "name": "holdout::ds_9f7d5da5c796bd86da1011e8",
        "start": "2022-01-01T12:12:00+00:00",
        "end": "2025-12-31T21:00:00+00:00"
      },
      "research_window": {
        "name": "research::ds_9f7d5da5c796bd86da1011e8",
        "start": "2006-01-04T01:00:00+00:00",
        "end": "2022-01-01T12:12:00+00:00"
      }
    },
    {
      "dataset_version_id": "ds_af544dc37dc887fdb98c4e62",
      "data_start": "2006-01-04T01:00:00+00:00",
      "data_end": "2025-12-31T21:00:00+00:00",
      "holdout_start": "2022-01-01T12:12:00+00:00",
      "holdout_fraction": 0.2,
      "label": "NZDUSD H4",
      "holdout_window": {
        "name": "holdout::ds_af544dc37dc887fdb98c4e62",
        "start": "2022-01-01T12:12:00+00:00",
        "end": "2025-12-31T21:00:00+00:00"
      },
      "research_window": {
        "name": "research::ds_af544dc37dc887fdb98c4e62",
        "start": "2006-01-04T01:00:00+00:00",
        "end": "2022-01-01T12:12:00+00:00"
      }
    },
    {
      "dataset_version_id": "ds_af8fc1b3143b3530c3f24ded",
      "data_start": "2009-03-15T21:00:00+00:00",
      "data_end": "2025-12-31T21:00:00+00:00",
      "holdout_start": "2022-08-22T21:00:00+00:00",
      "holdout_fraction": 0.2,
      "label": "XAUUSD H4",
      "holdout_window": {
        "name": "holdout::ds_af8fc1b3143b3530c3f24ded",
        "start": "2022-08-22T21:00:00+00:00",
        "end": "2025-12-31T21:00:00+00:00"
      },
      "research_window": {
        "name": "research::ds_af8fc1b3143b3530c3f24ded",
        "start": "2009-03-15T21:00:00+00:00",
        "end": "2022-08-22T21:00:00+00:00"
      }
    },
    {
      "dataset_version_id": "ds_b34988ed16240d36d2b9248c",
      "data_start": "2006-01-04T01:00:00+00:00",
      "data_end": "2025-12-31T21:00:00+00:00",
      "holdout_start": "2022-01-01T12:12:00+00:00",
      "holdout_fraction": 0.2,
      "label": "GBPUSD H4",
      "holdout_window": {
        "name": "holdout::ds_b34988ed16240d36d2b9248c",
        "start": "2022-01-01T12:12:00+00:00",
        "end": "2025-12-31T21:00:00+00:00"
      },
      "research_window": {
        "name": "research::ds_b34988ed16240d36d2b9248c",
        "start": "2006-01-04T01:00:00+00:00",
        "end": "2022-01-01T12:12:00+00:00"
      }
    },
    {
      "dataset_version_id": "ds_b81ab5fe112100c4094cfb73",
      "data_start": "2010-11-15T05:00:00+00:00",
      "data_end": "2025-12-31T17:00:00+00:00",
      "holdout_start": "2022-12-22T14:36:00+00:00",
      "holdout_fraction": 0.2,
      "label": "DE40 H4",
      "holdout_window": {
        "name": "holdout::ds_b81ab5fe112100c4094cfb73",
        "start": "2022-12-22T14:36:00+00:00",
        "end": "2025-12-31T17:00:00+00:00"
      },
      "research_window": {
        "name": "research::ds_b81ab5fe112100c4094cfb73",
        "start": "2010-11-15T05:00:00+00:00",
        "end": "2022-12-22T14:36:00+00:00"
      }
    },
    {
      "dataset_version_id": "ds_ba66154d681cb623c3780947",
      "data_start": "2006-01-04T01:00:00+00:00",
      "data_end": "2025-12-31T21:00:00+00:00",
      "holdout_start": "2022-01-01T12:12:00+00:00",
      "holdout_fraction": 0.2,
      "label": "AUDUSD H4",
      "holdout_window": {
        "name": "holdout::ds_ba66154d681cb623c3780947",
        "start": "2022-01-01T12:12:00+00:00",
        "end": "2025-12-31T21:00:00+00:00"
      },
      "research_window": {
        "name": "research::ds_ba66154d681cb623c3780947",
        "start": "2006-01-04T01:00:00+00:00",
        "end": "2022-01-01T12:12:00+00:00"
      }
    },
    {
      "dataset_version_id": "ds_c62e333d89200eecf29e9a12",
      "data_start": "2006-01-04T01:00:00+00:00",
      "data_end": "2025-12-31T21:00:00+00:00",
      "holdout_start": "2022-01-01T12:12:00+00:00",
      "holdout_fraction": 0.2,
      "label": "EURJPY H4",
      "holdout_window": {
        "name": "holdout::ds_c62e333d89200eecf29e9a12",
        "start": "2022-01-01T12:12:00+00:00",
        "end": "2025-12-31T21:00:00+00:00"
      },
      "research_window": {
        "name": "research::ds_c62e333d89200eecf29e9a12",
        "start": "2006-01-04T01:00:00+00:00",
        "end": "2022-01-01T12:12:00+00:00"
      }
    },
    {
      "dataset_version_id": "ds_d8739e54df9f12ecf0ce71c8",
      "data_start": "2006-01-04T01:00:00+00:00",
      "data_end": "2025-12-31T21:00:00+00:00",
      "holdout_start": "2022-01-01T12:12:00+00:00",
      "holdout_fraction": 0.2,
      "label": "EURUSD H4",
      "holdout_window": {
        "name": "holdout::ds_d8739e54df9f12ecf0ce71c8",
        "start": "2022-01-01T12:12:00+00:00",
        "end": "2025-12-31T21:00:00+00:00"
      },
      "research_window": {
        "name": "research::ds_d8739e54df9f12ecf0ce71c8",
        "start": "2006-01-04T01:00:00+00:00",
        "end": "2022-01-01T12:12:00+00:00"
      }
    },
    {
      "dataset_version_id": "ds_de6aae350c45f9f5025c7f56",
      "data_start": "2006-01-04T01:00:00+00:00",
      "data_end": "2025-12-31T21:00:00+00:00",
      "holdout_start": "2022-01-01T12:12:00+00:00",
      "holdout_fraction": 0.2,
      "label": "USDCAD H4",
      "holdout_window": {
        "name": "holdout::ds_de6aae350c45f9f5025c7f56",
        "start": "2022-01-01T12:12:00+00:00",
        "end": "2025-12-31T21:00:00+00:00"
      },
      "research_window": {
        "name": "research::ds_de6aae350c45f9f5025c7f56",
        "start": "2006-01-04T01:00:00+00:00",
        "end": "2022-01-01T12:12:00+00:00"
      }
    },
    {
      "dataset_version_id": "ds_f6269df5956668a8fd72e6a1",
      "data_start": "2010-11-14T21:00:00+00:00",
      "data_end": "2025-12-31T21:00:00+00:00",
      "holdout_start": "2022-12-22T16:12:00+00:00",
      "holdout_fraction": 0.2,
      "label": "US500 H4",
      "holdout_window": {
        "name": "holdout::ds_f6269df5956668a8fd72e6a1",
        "start": "2022-12-22T16:12:00+00:00",
        "end": "2025-12-31T21:00:00+00:00"
      },
      "research_window": {
        "name": "research::ds_f6269df5956668a8fd72e6a1",
        "start": "2010-11-14T21:00:00+00:00",
        "end": "2022-12-22T16:12:00+00:00"
      }
    }
  ],
  "n_consumed": 0,
  "unconsumed": true,
  "note": "No candidate reached rung 6, so the holdout segment is untouched and remains available for a future campaign."
}
```

## What this does and does not demonstrate

This campaign carried 542 candidate cell(s) through the validation ladder over 13 instrument(s) (AUDUSD, DE40, EURGBP, EURJPY, EURUSD, GBPJPY, GBPUSD, NZDUSD, US500, USDCAD, USDCHF, USDJPY, XAUUSD) on 1 timeframe(s) (H4), against gate set v2.0.0-audit. The true trial count for the whole search is 7376 (3350 planned in this campaign plus 4026 already spent on the same bars before it began), and that is the number the deflation used -- not the size of any one strategy's own parameter sweep. NOTHING SURVIVED. That is the expected outcome and it is a real result: it says these rule families, on this data, under these costs, do not clear a bar set for a search of this size. It does NOT say the underlying effects do not exist, that another instrument would behave the same way, or that a different cost model would give the same answer.
