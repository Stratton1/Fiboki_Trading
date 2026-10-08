# Campaign k4_tsmom_dual_horizon_engine_v3

- actor: `script:run_discovery_campaign`
- created: 2026-09-29T23:18:49.128332+00:00
- gate set: `v2.0.0-audit` (`fe7daa4c71e886b1`)
- datasets: AUDJPY_H4 -> `ds_f4090c2697088bcedb423849`, AUDUSD_H4 -> `ds_ba66154d681cb623c3780947`, DE40_H4 -> `ds_b81ab5fe112100c4094cfb73`, EURGBP_H4 -> `ds_0bafc32b4ed7b8065261e7a4`, EURJPY_H4 -> `ds_c62e333d89200eecf29e9a12`, EURUSD_H4 -> `ds_d8739e54df9f12ecf0ce71c8`, GBPJPY_H4 -> `ds_9f7d5da5c796bd86da1011e8`, GBPUSD_H4 -> `ds_b34988ed16240d36d2b9248c`, NZDUSD_H4 -> `ds_af544dc37dc887fdb98c4e62`, UK100_H4 -> `ds_e5ec1300d41332ab9b36595a`, US500_H4 -> `ds_f6269df5956668a8fd72e6a1`, USDCAD_H4 -> `ds_de6aae350c45f9f5025c7f56`, USDCHF_H4 -> `ds_5a2b99b30031d45d3546c1ca`, USDJPY_H4 -> `ds_300921ee00603a414fc03e98`, XAGUSD_H4 -> `ds_0f1ab110d7cf2003134b2b39`, XAUUSD_H4 -> `ds_af8fc1b3143b3530c3f24ded`

## Trial accounting

- planned in this campaign: **1120**
- already spent on the same bars before this campaign began: **7376**
- **true trial count: 8496**
- ladder evaluations requested: 80
- engine backtests actually run: 80 (lower when the deterministic evaluation cache was warm)

The deflation threshold is E[max SR] for a search of 8496 trials. No candidate reached even the in-sample screen, so the dispersion of trial Sharpes was never measured and the threshold is reported as unavailable rather than guessed.

## What was tried

| strategy | cell | origin | n trials | external N | verdict | died at | deflated SR | threshold |
|---|---|---|---|---|---|---|---|---|
| `tsmom_dual_horizon` | AUDUSD H4 | seed | 16 | 8480 | reject | RUNG 0 SANITY | - | - |
| `tsmom_dual_horizon` | DE40 H4 | seed | 16 | 8480 | reject | RUNG 0 SANITY | - | - |
| `tsmom_dual_horizon` | EURUSD H4 | seed | 16 | 8480 | reject | RUNG 0 SANITY | - | - |
| `tsmom_dual_horizon` | GBPUSD H4 | seed | 16 | 8480 | reject | RUNG 0 SANITY | - | - |
| `tsmom_dual_horizon` | NZDUSD H4 | seed | 16 | 8480 | reject | RUNG 0 SANITY | - | - |
| `tsmom_dual_horizon` | US500 H4 | seed | 16 | 8480 | reject | RUNG 0 SANITY | - | - |
| `tsmom_dual_horizon` | USDCAD H4 | seed | 16 | 8480 | reject | RUNG 0 SANITY | - | - |
| `tsmom_dual_horizon` | USDCHF H4 | seed | 16 | 8480 | reject | RUNG 0 SANITY | - | - |
| `tsmom_dual_horizon` | USDJPY H4 | seed | 16 | 8480 | reject | RUNG 0 SANITY | - | - |
| `tsmom_dual_horizon` | XAUUSD H4 | seed | 16 | 8480 | reject | RUNG 0 SANITY | - | - |
| `tsmom_dual_horizon_m6ae48a52` | AUDUSD H4 | add_filter | 16 | 8480 | reject | RUNG 0 SANITY | - | - |
| `tsmom_dual_horizon_m6ae48a52` | DE40 H4 | add_filter | 16 | 8480 | reject | RUNG 0 SANITY | - | - |
| `tsmom_dual_horizon_m6ae48a52` | EURUSD H4 | add_filter | 16 | 8480 | reject | RUNG 0 SANITY | - | - |
| `tsmom_dual_horizon_m6ae48a52` | GBPUSD H4 | add_filter | 16 | 8480 | reject | RUNG 0 SANITY | - | - |
| `tsmom_dual_horizon_m6ae48a52` | NZDUSD H4 | add_filter | 16 | 8480 | reject | RUNG 0 SANITY | - | - |
| `tsmom_dual_horizon_m6ae48a52` | US500 H4 | add_filter | 16 | 8480 | reject | RUNG 0 SANITY | - | - |
| `tsmom_dual_horizon_m6ae48a52` | USDCAD H4 | add_filter | 16 | 8480 | reject | RUNG 0 SANITY | - | - |
| `tsmom_dual_horizon_m6ae48a52` | USDCHF H4 | add_filter | 16 | 8480 | reject | RUNG 0 SANITY | - | - |
| `tsmom_dual_horizon_m6ae48a52` | USDJPY H4 | add_filter | 16 | 8480 | reject | RUNG 0 SANITY | - | - |
| `tsmom_dual_horizon_m6ae48a52` | XAUUSD H4 | add_filter | 16 | 8480 | reject | RUNG 0 SANITY | - | - |
| `tsmom_dual_horizon_md76b9594` | AUDUSD H4 | alter_stop_model | 8 | 8488 | reject | RUNG 0 SANITY | - | - |
| `tsmom_dual_horizon_md76b9594` | DE40 H4 | alter_stop_model | 8 | 8488 | reject | RUNG 0 SANITY | - | - |
| `tsmom_dual_horizon_md76b9594` | EURUSD H4 | alter_stop_model | 8 | 8488 | reject | RUNG 0 SANITY | - | - |
| `tsmom_dual_horizon_md76b9594` | GBPUSD H4 | alter_stop_model | 8 | 8488 | reject | RUNG 0 SANITY | - | - |
| `tsmom_dual_horizon_md76b9594` | NZDUSD H4 | alter_stop_model | 8 | 8488 | reject | RUNG 0 SANITY | - | - |
| `tsmom_dual_horizon_md76b9594` | US500 H4 | alter_stop_model | 8 | 8488 | reject | RUNG 0 SANITY | - | - |
| `tsmom_dual_horizon_md76b9594` | USDCAD H4 | alter_stop_model | 8 | 8488 | reject | RUNG 0 SANITY | - | - |
| `tsmom_dual_horizon_md76b9594` | USDCHF H4 | alter_stop_model | 8 | 8488 | reject | RUNG 0 SANITY | - | - |
| `tsmom_dual_horizon_md76b9594` | USDJPY H4 | alter_stop_model | 8 | 8488 | reject | RUNG 0 SANITY | - | - |
| `tsmom_dual_horizon_md76b9594` | XAUUSD H4 | alter_stop_model | 8 | 8488 | reject | RUNG 0 SANITY | - | - |
| `tsmom_dual_horizon_m61ee3211` | AUDUSD H4 | alter_target_model | 16 | 8480 | reject | RUNG 0 SANITY | - | - |
| `tsmom_dual_horizon_m61ee3211` | DE40 H4 | alter_target_model | 16 | 8480 | reject | RUNG 0 SANITY | - | - |
| `tsmom_dual_horizon_m61ee3211` | EURUSD H4 | alter_target_model | 16 | 8480 | reject | RUNG 0 SANITY | - | - |
| `tsmom_dual_horizon_m61ee3211` | GBPUSD H4 | alter_target_model | 16 | 8480 | reject | RUNG 0 SANITY | - | - |
| `tsmom_dual_horizon_m61ee3211` | NZDUSD H4 | alter_target_model | 16 | 8480 | reject | RUNG 0 SANITY | - | - |
| `tsmom_dual_horizon_m61ee3211` | US500 H4 | alter_target_model | 16 | 8480 | reject | RUNG 0 SANITY | - | - |
| `tsmom_dual_horizon_m61ee3211` | USDCAD H4 | alter_target_model | 16 | 8480 | reject | RUNG 0 SANITY | - | - |
| `tsmom_dual_horizon_m61ee3211` | USDCHF H4 | alter_target_model | 16 | 8480 | reject | RUNG 0 SANITY | - | - |
| `tsmom_dual_horizon_m61ee3211` | USDJPY H4 | alter_target_model | 16 | 8480 | reject | RUNG 0 SANITY | - | - |
| `tsmom_dual_horizon_m61ee3211` | XAUUSD H4 | alter_target_model | 16 | 8480 | reject | RUNG 0 SANITY | - | - |
| `tsmom_dual_horizon_mc04c00b2` | AUDUSD H4 | change_regime_gate | 16 | 8480 | reject | RUNG 0 SANITY | - | - |
| `tsmom_dual_horizon_mc04c00b2` | DE40 H4 | change_regime_gate | 16 | 8480 | reject | RUNG 0 SANITY | - | - |
| `tsmom_dual_horizon_mc04c00b2` | EURUSD H4 | change_regime_gate | 16 | 8480 | reject | RUNG 0 SANITY | - | - |
| `tsmom_dual_horizon_mc04c00b2` | GBPUSD H4 | change_regime_gate | 16 | 8480 | reject | RUNG 0 SANITY | - | - |
| `tsmom_dual_horizon_mc04c00b2` | NZDUSD H4 | change_regime_gate | 16 | 8480 | reject | RUNG 0 SANITY | - | - |
| `tsmom_dual_horizon_mc04c00b2` | US500 H4 | change_regime_gate | 16 | 8480 | reject | RUNG 0 SANITY | - | - |
| `tsmom_dual_horizon_mc04c00b2` | USDCAD H4 | change_regime_gate | 16 | 8480 | reject | RUNG 0 SANITY | - | - |
| `tsmom_dual_horizon_mc04c00b2` | USDCHF H4 | change_regime_gate | 16 | 8480 | reject | RUNG 0 SANITY | - | - |
| `tsmom_dual_horizon_mc04c00b2` | USDJPY H4 | change_regime_gate | 16 | 8480 | reject | RUNG 0 SANITY | - | - |
| `tsmom_dual_horizon_mc04c00b2` | XAUUSD H4 | change_regime_gate | 16 | 8480 | reject | RUNG 0 SANITY | - | - |
| `tsmom_dual_horizon_mf3293339` | AUDUSD H4 | change_session_restriction | 16 | 8480 | reject | RUNG 0 SANITY | - | - |
| `tsmom_dual_horizon_mf3293339` | DE40 H4 | change_session_restriction | 16 | 8480 | reject | RUNG 0 SANITY | - | - |
| `tsmom_dual_horizon_mf3293339` | EURUSD H4 | change_session_restriction | 16 | 8480 | reject | RUNG 0 SANITY | - | - |
| `tsmom_dual_horizon_mf3293339` | GBPUSD H4 | change_session_restriction | 16 | 8480 | reject | RUNG 0 SANITY | - | - |
| `tsmom_dual_horizon_mf3293339` | NZDUSD H4 | change_session_restriction | 16 | 8480 | reject | RUNG 0 SANITY | - | - |
| `tsmom_dual_horizon_mf3293339` | US500 H4 | change_session_restriction | 16 | 8480 | reject | RUNG 0 SANITY | - | - |
| `tsmom_dual_horizon_mf3293339` | USDCAD H4 | change_session_restriction | 16 | 8480 | reject | RUNG 0 SANITY | - | - |
| `tsmom_dual_horizon_mf3293339` | USDCHF H4 | change_session_restriction | 16 | 8480 | reject | RUNG 0 SANITY | - | - |
| `tsmom_dual_horizon_mf3293339` | USDJPY H4 | change_session_restriction | 16 | 8480 | reject | RUNG 0 SANITY | - | - |
| `tsmom_dual_horizon_mf3293339` | XAUUSD H4 | change_session_restriction | 16 | 8480 | reject | RUNG 0 SANITY | - | - |
| `tsmom_dual_horizon_m47768d76` | AUDUSD H4 | change_confirmation_rule | 16 | 8480 | reject | RUNG 0 SANITY | - | - |
| `tsmom_dual_horizon_m47768d76` | DE40 H4 | change_confirmation_rule | 16 | 8480 | reject | RUNG 0 SANITY | - | - |
| `tsmom_dual_horizon_m47768d76` | EURUSD H4 | change_confirmation_rule | 16 | 8480 | reject | RUNG 0 SANITY | - | - |
| `tsmom_dual_horizon_m47768d76` | GBPUSD H4 | change_confirmation_rule | 16 | 8480 | reject | RUNG 0 SANITY | - | - |
| `tsmom_dual_horizon_m47768d76` | NZDUSD H4 | change_confirmation_rule | 16 | 8480 | reject | RUNG 0 SANITY | - | - |
| `tsmom_dual_horizon_m47768d76` | US500 H4 | change_confirmation_rule | 16 | 8480 | reject | RUNG 0 SANITY | - | - |
| `tsmom_dual_horizon_m47768d76` | USDCAD H4 | change_confirmation_rule | 16 | 8480 | reject | RUNG 0 SANITY | - | - |
| `tsmom_dual_horizon_m47768d76` | USDCHF H4 | change_confirmation_rule | 16 | 8480 | reject | RUNG 0 SANITY | - | - |
| `tsmom_dual_horizon_m47768d76` | USDJPY H4 | change_confirmation_rule | 16 | 8480 | reject | RUNG 0 SANITY | - | - |
| `tsmom_dual_horizon_m47768d76` | XAUUSD H4 | change_confirmation_rule | 16 | 8480 | reject | RUNG 0 SANITY | - | - |
| `tsmom_dual_horizon_md7df8207` | AUDUSD H4 | simplify | 8 | 8488 | reject | RUNG 0 SANITY | - | - |
| `tsmom_dual_horizon_md7df8207` | DE40 H4 | simplify | 8 | 8488 | reject | RUNG 0 SANITY | - | - |
| `tsmom_dual_horizon_md7df8207` | EURUSD H4 | simplify | 8 | 8488 | reject | RUNG 0 SANITY | - | - |
| `tsmom_dual_horizon_md7df8207` | GBPUSD H4 | simplify | 8 | 8488 | reject | RUNG 0 SANITY | - | - |
| `tsmom_dual_horizon_md7df8207` | NZDUSD H4 | simplify | 8 | 8488 | reject | RUNG 0 SANITY | - | - |
| `tsmom_dual_horizon_md7df8207` | US500 H4 | simplify | 8 | 8488 | reject | RUNG 0 SANITY | - | - |
| `tsmom_dual_horizon_md7df8207` | USDCAD H4 | simplify | 8 | 8488 | reject | RUNG 0 SANITY | - | - |
| `tsmom_dual_horizon_md7df8207` | USDCHF H4 | simplify | 8 | 8488 | reject | RUNG 0 SANITY | - | - |
| `tsmom_dual_horizon_md7df8207` | USDJPY H4 | simplify | 8 | 8488 | reject | RUNG 0 SANITY | - | - |
| `tsmom_dual_horizon_md7df8207` | XAUUSD H4 | simplify | 8 | 8488 | reject | RUNG 0 SANITY | - | - |

### Why each one stopped

- tsmom_dual_horizon on AUDUSD H4: REJECT at RUNG 0 SANITY -- RUNG 0 SANITY: expectancy -0.486601 at declared defaults is not positive; there is no edge to validate.
- tsmom_dual_horizon on DE40 H4: REJECT at RUNG 0 SANITY -- min_trades: observed 163, required gte 400, short by 237
- tsmom_dual_horizon on EURUSD H4: REJECT at RUNG 0 SANITY -- min_trades: observed 385, required gte 400, short by 15
- tsmom_dual_horizon on GBPUSD H4: REJECT at RUNG 0 SANITY -- RUNG 0 SANITY: expectancy -1.22736 at declared defaults is not positive; there is no edge to validate.
- tsmom_dual_horizon on NZDUSD H4: REJECT at RUNG 0 SANITY -- RUNG 0 SANITY: expectancy -0.716509 at declared defaults is not positive; there is no edge to validate.
- tsmom_dual_horizon on US500 H4: REJECT at RUNG 0 SANITY -- min_trades: observed 345, required gte 400, short by 55
- tsmom_dual_horizon on USDCAD H4: REJECT at RUNG 0 SANITY -- min_trades: observed 365, required gte 400, short by 35
- tsmom_dual_horizon on USDCHF H4: REJECT at RUNG 0 SANITY -- RUNG 0 SANITY: expectancy -0.958774 at declared defaults is not positive; there is no edge to validate.
- tsmom_dual_horizon on USDJPY H4: REJECT at RUNG 0 SANITY -- RUNG 0 SANITY: expectancy -0.705058 at declared defaults is not positive; there is no edge to validate.
- tsmom_dual_horizon on XAUUSD H4: REJECT at RUNG 0 SANITY -- RUNG 0 SANITY: expectancy -0.11301 at declared defaults is not positive; there is no edge to validate.
- tsmom_dual_horizon_m6ae48a52 on AUDUSD H4: REJECT at RUNG 0 SANITY -- RUNG 0 SANITY: expectancy -0.430802 at declared defaults is not positive; there is no edge to validate.
- tsmom_dual_horizon_m6ae48a52 on DE40 H4: REJECT at RUNG 0 SANITY -- min_trades: observed 162, required gte 400, short by 238
- tsmom_dual_horizon_m6ae48a52 on EURUSD H4: REJECT at RUNG 0 SANITY -- min_trades: observed 382, required gte 400, short by 18
- tsmom_dual_horizon_m6ae48a52 on GBPUSD H4: REJECT at RUNG 0 SANITY -- RUNG 0 SANITY: expectancy -1.21155 at declared defaults is not positive; there is no edge to validate.
- tsmom_dual_horizon_m6ae48a52 on NZDUSD H4: REJECT at RUNG 0 SANITY -- RUNG 0 SANITY: expectancy -0.754095 at declared defaults is not positive; there is no edge to validate.
- tsmom_dual_horizon_m6ae48a52 on US500 H4: REJECT at RUNG 0 SANITY -- min_trades: observed 346, required gte 400, short by 54
- tsmom_dual_horizon_m6ae48a52 on USDCAD H4: REJECT at RUNG 0 SANITY -- min_trades: observed 361, required gte 400, short by 39
- tsmom_dual_horizon_m6ae48a52 on USDCHF H4: REJECT at RUNG 0 SANITY -- RUNG 0 SANITY: expectancy -0.919633 at declared defaults is not positive; there is no edge to validate.
- tsmom_dual_horizon_m6ae48a52 on USDJPY H4: REJECT at RUNG 0 SANITY -- RUNG 0 SANITY: expectancy -0.464977 at declared defaults is not positive; there is no edge to validate.
- tsmom_dual_horizon_m6ae48a52 on XAUUSD H4: REJECT at RUNG 0 SANITY -- RUNG 0 SANITY: expectancy -0.184208 at declared defaults is not positive; there is no edge to validate.
- tsmom_dual_horizon_md76b9594 on AUDUSD H4: REJECT at RUNG 0 SANITY -- RUNG 0 SANITY: expectancy -0.463747 at declared defaults is not positive; there is no edge to validate.
- tsmom_dual_horizon_md76b9594 on DE40 H4: REJECT at RUNG 0 SANITY -- min_trades: observed 116, required gte 400, short by 284
- tsmom_dual_horizon_md76b9594 on EURUSD H4: REJECT at RUNG 0 SANITY -- min_trades: observed 371, required gte 400, short by 29
- tsmom_dual_horizon_md76b9594 on GBPUSD H4: REJECT at RUNG 0 SANITY -- min_trades: observed 396, required gte 400, short by 4
- tsmom_dual_horizon_md76b9594 on NZDUSD H4: REJECT at RUNG 0 SANITY -- RUNG 0 SANITY: expectancy -0.76794 at declared defaults is not positive; there is no edge to validate.
- tsmom_dual_horizon_md76b9594 on US500 H4: REJECT at RUNG 0 SANITY -- min_trades: observed 338, required gte 400, short by 62
- tsmom_dual_horizon_md76b9594 on USDCAD H4: REJECT at RUNG 0 SANITY -- min_trades: observed 350, required gte 400, short by 50
- tsmom_dual_horizon_md76b9594 on USDCHF H4: REJECT at RUNG 0 SANITY -- RUNG 0 SANITY: expectancy -1.15556 at declared defaults is not positive; there is no edge to validate.
- tsmom_dual_horizon_md76b9594 on USDJPY H4: REJECT at RUNG 0 SANITY -- min_trades: observed 397, required gte 400, short by 3
- tsmom_dual_horizon_md76b9594 on XAUUSD H4: REJECT at RUNG 0 SANITY -- RUNG 0 SANITY: expectancy -0.48065 at declared defaults is not positive; there is no edge to validate.
- tsmom_dual_horizon_m61ee3211 on AUDUSD H4: REJECT at RUNG 0 SANITY -- RUNG 0 SANITY: expectancy -0.454855 at declared defaults is not positive; there is no edge to validate.
- tsmom_dual_horizon_m61ee3211 on DE40 H4: REJECT at RUNG 0 SANITY -- min_trades: observed 163, required gte 400, short by 237
- tsmom_dual_horizon_m61ee3211 on EURUSD H4: REJECT at RUNG 0 SANITY -- min_trades: observed 385, required gte 400, short by 15
- tsmom_dual_horizon_m61ee3211 on GBPUSD H4: REJECT at RUNG 0 SANITY -- RUNG 0 SANITY: expectancy -1.30586 at declared defaults is not positive; there is no edge to validate.
- tsmom_dual_horizon_m61ee3211 on NZDUSD H4: REJECT at RUNG 0 SANITY -- RUNG 0 SANITY: expectancy -0.714591 at declared defaults is not positive; there is no edge to validate.
- tsmom_dual_horizon_m61ee3211 on US500 H4: REJECT at RUNG 0 SANITY -- min_trades: observed 345, required gte 400, short by 55
- tsmom_dual_horizon_m61ee3211 on USDCAD H4: REJECT at RUNG 0 SANITY -- min_trades: observed 365, required gte 400, short by 35
- tsmom_dual_horizon_m61ee3211 on USDCHF H4: REJECT at RUNG 0 SANITY -- RUNG 0 SANITY: expectancy -0.855153 at declared defaults is not positive; there is no edge to validate.
- tsmom_dual_horizon_m61ee3211 on USDJPY H4: REJECT at RUNG 0 SANITY -- RUNG 0 SANITY: expectancy -1.09574 at declared defaults is not positive; there is no edge to validate.
- tsmom_dual_horizon_m61ee3211 on XAUUSD H4: REJECT at RUNG 0 SANITY -- RUNG 0 SANITY: expectancy -0.178394 at declared defaults is not positive; there is no edge to validate.
- tsmom_dual_horizon_mc04c00b2 on AUDUSD H4: REJECT at RUNG 0 SANITY -- min_trades: observed 292, required gte 400, short by 108
- tsmom_dual_horizon_mc04c00b2 on DE40 H4: REJECT at RUNG 0 SANITY -- min_trades: observed 92, required gte 400, short by 308
- tsmom_dual_horizon_mc04c00b2 on EURUSD H4: REJECT at RUNG 0 SANITY -- min_trades: observed 329, required gte 400, short by 71
- tsmom_dual_horizon_mc04c00b2 on GBPUSD H4: REJECT at RUNG 0 SANITY -- min_trades: observed 310, required gte 400, short by 90
- tsmom_dual_horizon_mc04c00b2 on NZDUSD H4: REJECT at RUNG 0 SANITY -- min_trades: observed 294, required gte 400, short by 106
- tsmom_dual_horizon_mc04c00b2 on US500 H4: REJECT at RUNG 0 SANITY -- min_trades: observed 240, required gte 400, short by 160
- tsmom_dual_horizon_mc04c00b2 on USDCAD H4: REJECT at RUNG 0 SANITY -- min_trades: observed 315, required gte 400, short by 85
- tsmom_dual_horizon_mc04c00b2 on USDCHF H4: REJECT at RUNG 0 SANITY -- min_trades: observed 329, required gte 400, short by 71
- tsmom_dual_horizon_mc04c00b2 on USDJPY H4: REJECT at RUNG 0 SANITY -- min_trades: observed 349, required gte 400, short by 51
- tsmom_dual_horizon_mc04c00b2 on XAUUSD H4: REJECT at RUNG 0 SANITY -- min_trades: observed 290, required gte 400, short by 110
- tsmom_dual_horizon_mf3293339 on AUDUSD H4: REJECT at RUNG 0 SANITY -- min_trades: observed 188, required gte 400, short by 212
- tsmom_dual_horizon_mf3293339 on DE40 H4: REJECT at RUNG 0 SANITY -- min_trades: observed 91, required gte 400, short by 309
- tsmom_dual_horizon_mf3293339 on EURUSD H4: REJECT at RUNG 0 SANITY -- min_trades: observed 175, required gte 400, short by 225
- tsmom_dual_horizon_mf3293339 on GBPUSD H4: REJECT at RUNG 0 SANITY -- min_trades: observed 173, required gte 400, short by 227
- tsmom_dual_horizon_mf3293339 on NZDUSD H4: REJECT at RUNG 0 SANITY -- min_trades: observed 177, required gte 400, short by 223
- tsmom_dual_horizon_mf3293339 on US500 H4: REJECT at RUNG 0 SANITY -- min_trades: observed 140, required gte 400, short by 260
- tsmom_dual_horizon_mf3293339 on USDCAD H4: REJECT at RUNG 0 SANITY -- min_trades: observed 173, required gte 400, short by 227
- tsmom_dual_horizon_mf3293339 on USDCHF H4: REJECT at RUNG 0 SANITY -- min_trades: observed 190, required gte 400, short by 210
- tsmom_dual_horizon_mf3293339 on USDJPY H4: REJECT at RUNG 0 SANITY -- min_trades: observed 153, required gte 400, short by 247
- tsmom_dual_horizon_mf3293339 on XAUUSD H4: REJECT at RUNG 0 SANITY -- min_trades: observed 198, required gte 400, short by 202
- tsmom_dual_horizon_m47768d76 on AUDUSD H4: REJECT at RUNG 0 SANITY -- RUNG 0 SANITY: expectancy -1.03917 at declared defaults is not positive; there is no edge to validate.
- tsmom_dual_horizon_m47768d76 on DE40 H4: REJECT at RUNG 0 SANITY -- min_trades: observed 142, required gte 400, short by 258
- tsmom_dual_horizon_m47768d76 on EURUSD H4: REJECT at RUNG 0 SANITY -- min_trades: observed 346, required gte 400, short by 54
- tsmom_dual_horizon_m47768d76 on GBPUSD H4: REJECT at RUNG 0 SANITY -- min_trades: observed 369, required gte 400, short by 31
- tsmom_dual_horizon_m47768d76 on NZDUSD H4: REJECT at RUNG 0 SANITY -- RUNG 0 SANITY: expectancy -0.550958 at declared defaults is not positive; there is no edge to validate.
- tsmom_dual_horizon_m47768d76 on US500 H4: REJECT at RUNG 0 SANITY -- min_trades: observed 310, required gte 400, short by 90
- tsmom_dual_horizon_m47768d76 on USDCAD H4: REJECT at RUNG 0 SANITY -- min_trades: observed 333, required gte 400, short by 67
- tsmom_dual_horizon_m47768d76 on USDCHF H4: REJECT at RUNG 0 SANITY -- min_trades: observed 374, required gte 400, short by 26
- tsmom_dual_horizon_m47768d76 on USDJPY H4: REJECT at RUNG 0 SANITY -- min_trades: observed 358, required gte 400, short by 42
- tsmom_dual_horizon_m47768d76 on XAUUSD H4: REJECT at RUNG 0 SANITY -- min_trades: observed 388, required gte 400, short by 12
- tsmom_dual_horizon_md7df8207 on AUDUSD H4: REJECT at RUNG 0 SANITY -- RUNG 0 SANITY: expectancy -0.966344 at declared defaults is not positive; there is no edge to validate.
- tsmom_dual_horizon_md7df8207 on DE40 H4: REJECT at RUNG 0 SANITY -- min_trades: observed 216, required gte 400, short by 184
- tsmom_dual_horizon_md7df8207 on EURUSD H4: REJECT at RUNG 0 SANITY -- RUNG 0 SANITY: expectancy -0.375991 at declared defaults is not positive; there is no edge to validate.
- tsmom_dual_horizon_md7df8207 on GBPUSD H4: REJECT at RUNG 0 SANITY -- RUNG 0 SANITY: expectancy -0.897524 at declared defaults is not positive; there is no edge to validate.
- tsmom_dual_horizon_md7df8207 on NZDUSD H4: REJECT at RUNG 0 SANITY -- RUNG 0 SANITY: expectancy -0.91974 at declared defaults is not positive; there is no edge to validate.
- tsmom_dual_horizon_md7df8207 on US500 H4: REJECT at RUNG 0 SANITY -- RUNG 0 SANITY: expectancy -0.680019 at declared defaults is not positive; there is no edge to validate.
- tsmom_dual_horizon_md7df8207 on USDCAD H4: REJECT at RUNG 0 SANITY -- RUNG 0 SANITY: expectancy -0.255234 at declared defaults is not positive; there is no edge to validate.
- tsmom_dual_horizon_md7df8207 on USDCHF H4: REJECT at RUNG 0 SANITY -- RUNG 0 SANITY: expectancy -0.872746 at declared defaults is not positive; there is no edge to validate.
- tsmom_dual_horizon_md7df8207 on USDJPY H4: REJECT at RUNG 0 SANITY -- RUNG 0 SANITY: expectancy -0.79127 at declared defaults is not positive; there is no edge to validate.
- tsmom_dual_horizon_md7df8207 on XAUUSD H4: REJECT at RUNG 0 SANITY -- RUNG 0 SANITY: expectancy -0.607482 at declared defaults is not positive; there is no edge to validate.

## What was skipped

- tsmom_dual_horizon: mutation_rejected -- the parent declares no filters, so none can be removed
- tsmom_dual_horizon on AUDJPY H4: out_of_universe -- tsmom_dual_horizon declares universe ['EURUSD', 'GBPUSD', 'USDJPY', 'AUDUSD', 'USDCAD', 'USDCHF', 'NZDUSD', 'XAUUSD', 'US500', 'DE40'] and timeframes ['D1', 'H4']; running it on AUDJPY H4 would validate a strategy nobody wrote
- tsmom_dual_horizon on EURGBP H4: out_of_universe -- tsmom_dual_horizon declares universe ['EURUSD', 'GBPUSD', 'USDJPY', 'AUDUSD', 'USDCAD', 'USDCHF', 'NZDUSD', 'XAUUSD', 'US500', 'DE40'] and timeframes ['D1', 'H4']; running it on EURGBP H4 would validate a strategy nobody wrote
- tsmom_dual_horizon on EURJPY H4: out_of_universe -- tsmom_dual_horizon declares universe ['EURUSD', 'GBPUSD', 'USDJPY', 'AUDUSD', 'USDCAD', 'USDCHF', 'NZDUSD', 'XAUUSD', 'US500', 'DE40'] and timeframes ['D1', 'H4']; running it on EURJPY H4 would validate a strategy nobody wrote
- tsmom_dual_horizon on GBPJPY H4: out_of_universe -- tsmom_dual_horizon declares universe ['EURUSD', 'GBPUSD', 'USDJPY', 'AUDUSD', 'USDCAD', 'USDCHF', 'NZDUSD', 'XAUUSD', 'US500', 'DE40'] and timeframes ['D1', 'H4']; running it on GBPJPY H4 would validate a strategy nobody wrote
- tsmom_dual_horizon on UK100 H4: out_of_universe -- tsmom_dual_horizon declares universe ['EURUSD', 'GBPUSD', 'USDJPY', 'AUDUSD', 'USDCAD', 'USDCHF', 'NZDUSD', 'XAUUSD', 'US500', 'DE40'] and timeframes ['D1', 'H4']; running it on UK100 H4 would validate a strategy nobody wrote
- tsmom_dual_horizon on XAGUSD H4: out_of_universe -- tsmom_dual_horizon declares universe ['EURUSD', 'GBPUSD', 'USDJPY', 'AUDUSD', 'USDCAD', 'USDCHF', 'NZDUSD', 'XAUUSD', 'US500', 'DE40'] and timeframes ['D1', 'H4']; running it on XAGUSD H4 would validate a strategy nobody wrote
- tsmom_dual_horizon_m6ae48a52 on AUDJPY H4: out_of_universe -- tsmom_dual_horizon_m6ae48a52 declares universe ['EURUSD', 'GBPUSD', 'USDJPY', 'AUDUSD', 'USDCAD', 'USDCHF', 'NZDUSD', 'XAUUSD', 'US500', 'DE40'] and timeframes ['D1', 'H4']; running it on AUDJPY H4 would validate a strategy nobody wrote
- tsmom_dual_horizon_m6ae48a52 on EURGBP H4: out_of_universe -- tsmom_dual_horizon_m6ae48a52 declares universe ['EURUSD', 'GBPUSD', 'USDJPY', 'AUDUSD', 'USDCAD', 'USDCHF', 'NZDUSD', 'XAUUSD', 'US500', 'DE40'] and timeframes ['D1', 'H4']; running it on EURGBP H4 would validate a strategy nobody wrote
- tsmom_dual_horizon_m6ae48a52 on EURJPY H4: out_of_universe -- tsmom_dual_horizon_m6ae48a52 declares universe ['EURUSD', 'GBPUSD', 'USDJPY', 'AUDUSD', 'USDCAD', 'USDCHF', 'NZDUSD', 'XAUUSD', 'US500', 'DE40'] and timeframes ['D1', 'H4']; running it on EURJPY H4 would validate a strategy nobody wrote
- tsmom_dual_horizon_m6ae48a52 on GBPJPY H4: out_of_universe -- tsmom_dual_horizon_m6ae48a52 declares universe ['EURUSD', 'GBPUSD', 'USDJPY', 'AUDUSD', 'USDCAD', 'USDCHF', 'NZDUSD', 'XAUUSD', 'US500', 'DE40'] and timeframes ['D1', 'H4']; running it on GBPJPY H4 would validate a strategy nobody wrote
- tsmom_dual_horizon_m6ae48a52 on UK100 H4: out_of_universe -- tsmom_dual_horizon_m6ae48a52 declares universe ['EURUSD', 'GBPUSD', 'USDJPY', 'AUDUSD', 'USDCAD', 'USDCHF', 'NZDUSD', 'XAUUSD', 'US500', 'DE40'] and timeframes ['D1', 'H4']; running it on UK100 H4 would validate a strategy nobody wrote
- tsmom_dual_horizon_m6ae48a52 on XAGUSD H4: out_of_universe -- tsmom_dual_horizon_m6ae48a52 declares universe ['EURUSD', 'GBPUSD', 'USDJPY', 'AUDUSD', 'USDCAD', 'USDCHF', 'NZDUSD', 'XAUUSD', 'US500', 'DE40'] and timeframes ['D1', 'H4']; running it on XAGUSD H4 would validate a strategy nobody wrote
- tsmom_dual_horizon_md76b9594 on AUDJPY H4: out_of_universe -- tsmom_dual_horizon_md76b9594 declares universe ['EURUSD', 'GBPUSD', 'USDJPY', 'AUDUSD', 'USDCAD', 'USDCHF', 'NZDUSD', 'XAUUSD', 'US500', 'DE40'] and timeframes ['D1', 'H4']; running it on AUDJPY H4 would validate a strategy nobody wrote
- tsmom_dual_horizon_md76b9594 on EURGBP H4: out_of_universe -- tsmom_dual_horizon_md76b9594 declares universe ['EURUSD', 'GBPUSD', 'USDJPY', 'AUDUSD', 'USDCAD', 'USDCHF', 'NZDUSD', 'XAUUSD', 'US500', 'DE40'] and timeframes ['D1', 'H4']; running it on EURGBP H4 would validate a strategy nobody wrote
- tsmom_dual_horizon_md76b9594 on EURJPY H4: out_of_universe -- tsmom_dual_horizon_md76b9594 declares universe ['EURUSD', 'GBPUSD', 'USDJPY', 'AUDUSD', 'USDCAD', 'USDCHF', 'NZDUSD', 'XAUUSD', 'US500', 'DE40'] and timeframes ['D1', 'H4']; running it on EURJPY H4 would validate a strategy nobody wrote
- tsmom_dual_horizon_md76b9594 on GBPJPY H4: out_of_universe -- tsmom_dual_horizon_md76b9594 declares universe ['EURUSD', 'GBPUSD', 'USDJPY', 'AUDUSD', 'USDCAD', 'USDCHF', 'NZDUSD', 'XAUUSD', 'US500', 'DE40'] and timeframes ['D1', 'H4']; running it on GBPJPY H4 would validate a strategy nobody wrote
- tsmom_dual_horizon_md76b9594 on UK100 H4: out_of_universe -- tsmom_dual_horizon_md76b9594 declares universe ['EURUSD', 'GBPUSD', 'USDJPY', 'AUDUSD', 'USDCAD', 'USDCHF', 'NZDUSD', 'XAUUSD', 'US500', 'DE40'] and timeframes ['D1', 'H4']; running it on UK100 H4 would validate a strategy nobody wrote
- tsmom_dual_horizon_md76b9594 on XAGUSD H4: out_of_universe -- tsmom_dual_horizon_md76b9594 declares universe ['EURUSD', 'GBPUSD', 'USDJPY', 'AUDUSD', 'USDCAD', 'USDCHF', 'NZDUSD', 'XAUUSD', 'US500', 'DE40'] and timeframes ['D1', 'H4']; running it on XAGUSD H4 would validate a strategy nobody wrote
- tsmom_dual_horizon_m61ee3211 on AUDJPY H4: out_of_universe -- tsmom_dual_horizon_m61ee3211 declares universe ['EURUSD', 'GBPUSD', 'USDJPY', 'AUDUSD', 'USDCAD', 'USDCHF', 'NZDUSD', 'XAUUSD', 'US500', 'DE40'] and timeframes ['D1', 'H4']; running it on AUDJPY H4 would validate a strategy nobody wrote
- tsmom_dual_horizon_m61ee3211 on EURGBP H4: out_of_universe -- tsmom_dual_horizon_m61ee3211 declares universe ['EURUSD', 'GBPUSD', 'USDJPY', 'AUDUSD', 'USDCAD', 'USDCHF', 'NZDUSD', 'XAUUSD', 'US500', 'DE40'] and timeframes ['D1', 'H4']; running it on EURGBP H4 would validate a strategy nobody wrote
- tsmom_dual_horizon_m61ee3211 on EURJPY H4: out_of_universe -- tsmom_dual_horizon_m61ee3211 declares universe ['EURUSD', 'GBPUSD', 'USDJPY', 'AUDUSD', 'USDCAD', 'USDCHF', 'NZDUSD', 'XAUUSD', 'US500', 'DE40'] and timeframes ['D1', 'H4']; running it on EURJPY H4 would validate a strategy nobody wrote
- tsmom_dual_horizon_m61ee3211 on GBPJPY H4: out_of_universe -- tsmom_dual_horizon_m61ee3211 declares universe ['EURUSD', 'GBPUSD', 'USDJPY', 'AUDUSD', 'USDCAD', 'USDCHF', 'NZDUSD', 'XAUUSD', 'US500', 'DE40'] and timeframes ['D1', 'H4']; running it on GBPJPY H4 would validate a strategy nobody wrote
- tsmom_dual_horizon_m61ee3211 on UK100 H4: out_of_universe -- tsmom_dual_horizon_m61ee3211 declares universe ['EURUSD', 'GBPUSD', 'USDJPY', 'AUDUSD', 'USDCAD', 'USDCHF', 'NZDUSD', 'XAUUSD', 'US500', 'DE40'] and timeframes ['D1', 'H4']; running it on UK100 H4 would validate a strategy nobody wrote
- tsmom_dual_horizon_m61ee3211 on XAGUSD H4: out_of_universe -- tsmom_dual_horizon_m61ee3211 declares universe ['EURUSD', 'GBPUSD', 'USDJPY', 'AUDUSD', 'USDCAD', 'USDCHF', 'NZDUSD', 'XAUUSD', 'US500', 'DE40'] and timeframes ['D1', 'H4']; running it on XAGUSD H4 would validate a strategy nobody wrote
- tsmom_dual_horizon_mc04c00b2 on AUDJPY H4: out_of_universe -- tsmom_dual_horizon_mc04c00b2 declares universe ['EURUSD', 'GBPUSD', 'USDJPY', 'AUDUSD', 'USDCAD', 'USDCHF', 'NZDUSD', 'XAUUSD', 'US500', 'DE40'] and timeframes ['D1', 'H4']; running it on AUDJPY H4 would validate a strategy nobody wrote
- tsmom_dual_horizon_mc04c00b2 on EURGBP H4: out_of_universe -- tsmom_dual_horizon_mc04c00b2 declares universe ['EURUSD', 'GBPUSD', 'USDJPY', 'AUDUSD', 'USDCAD', 'USDCHF', 'NZDUSD', 'XAUUSD', 'US500', 'DE40'] and timeframes ['D1', 'H4']; running it on EURGBP H4 would validate a strategy nobody wrote
- tsmom_dual_horizon_mc04c00b2 on EURJPY H4: out_of_universe -- tsmom_dual_horizon_mc04c00b2 declares universe ['EURUSD', 'GBPUSD', 'USDJPY', 'AUDUSD', 'USDCAD', 'USDCHF', 'NZDUSD', 'XAUUSD', 'US500', 'DE40'] and timeframes ['D1', 'H4']; running it on EURJPY H4 would validate a strategy nobody wrote
- tsmom_dual_horizon_mc04c00b2 on GBPJPY H4: out_of_universe -- tsmom_dual_horizon_mc04c00b2 declares universe ['EURUSD', 'GBPUSD', 'USDJPY', 'AUDUSD', 'USDCAD', 'USDCHF', 'NZDUSD', 'XAUUSD', 'US500', 'DE40'] and timeframes ['D1', 'H4']; running it on GBPJPY H4 would validate a strategy nobody wrote
- tsmom_dual_horizon_mc04c00b2 on UK100 H4: out_of_universe -- tsmom_dual_horizon_mc04c00b2 declares universe ['EURUSD', 'GBPUSD', 'USDJPY', 'AUDUSD', 'USDCAD', 'USDCHF', 'NZDUSD', 'XAUUSD', 'US500', 'DE40'] and timeframes ['D1', 'H4']; running it on UK100 H4 would validate a strategy nobody wrote
- tsmom_dual_horizon_mc04c00b2 on XAGUSD H4: out_of_universe -- tsmom_dual_horizon_mc04c00b2 declares universe ['EURUSD', 'GBPUSD', 'USDJPY', 'AUDUSD', 'USDCAD', 'USDCHF', 'NZDUSD', 'XAUUSD', 'US500', 'DE40'] and timeframes ['D1', 'H4']; running it on XAGUSD H4 would validate a strategy nobody wrote
- tsmom_dual_horizon_mf3293339 on AUDJPY H4: out_of_universe -- tsmom_dual_horizon_mf3293339 declares universe ['EURUSD', 'GBPUSD', 'USDJPY', 'AUDUSD', 'USDCAD', 'USDCHF', 'NZDUSD', 'XAUUSD', 'US500', 'DE40'] and timeframes ['D1', 'H4']; running it on AUDJPY H4 would validate a strategy nobody wrote
- tsmom_dual_horizon_mf3293339 on EURGBP H4: out_of_universe -- tsmom_dual_horizon_mf3293339 declares universe ['EURUSD', 'GBPUSD', 'USDJPY', 'AUDUSD', 'USDCAD', 'USDCHF', 'NZDUSD', 'XAUUSD', 'US500', 'DE40'] and timeframes ['D1', 'H4']; running it on EURGBP H4 would validate a strategy nobody wrote
- tsmom_dual_horizon_mf3293339 on EURJPY H4: out_of_universe -- tsmom_dual_horizon_mf3293339 declares universe ['EURUSD', 'GBPUSD', 'USDJPY', 'AUDUSD', 'USDCAD', 'USDCHF', 'NZDUSD', 'XAUUSD', 'US500', 'DE40'] and timeframes ['D1', 'H4']; running it on EURJPY H4 would validate a strategy nobody wrote
- tsmom_dual_horizon_mf3293339 on GBPJPY H4: out_of_universe -- tsmom_dual_horizon_mf3293339 declares universe ['EURUSD', 'GBPUSD', 'USDJPY', 'AUDUSD', 'USDCAD', 'USDCHF', 'NZDUSD', 'XAUUSD', 'US500', 'DE40'] and timeframes ['D1', 'H4']; running it on GBPJPY H4 would validate a strategy nobody wrote
- tsmom_dual_horizon_mf3293339 on UK100 H4: out_of_universe -- tsmom_dual_horizon_mf3293339 declares universe ['EURUSD', 'GBPUSD', 'USDJPY', 'AUDUSD', 'USDCAD', 'USDCHF', 'NZDUSD', 'XAUUSD', 'US500', 'DE40'] and timeframes ['D1', 'H4']; running it on UK100 H4 would validate a strategy nobody wrote
- tsmom_dual_horizon_mf3293339 on XAGUSD H4: out_of_universe -- tsmom_dual_horizon_mf3293339 declares universe ['EURUSD', 'GBPUSD', 'USDJPY', 'AUDUSD', 'USDCAD', 'USDCHF', 'NZDUSD', 'XAUUSD', 'US500', 'DE40'] and timeframes ['D1', 'H4']; running it on XAGUSD H4 would validate a strategy nobody wrote
- tsmom_dual_horizon_m47768d76 on AUDJPY H4: out_of_universe -- tsmom_dual_horizon_m47768d76 declares universe ['EURUSD', 'GBPUSD', 'USDJPY', 'AUDUSD', 'USDCAD', 'USDCHF', 'NZDUSD', 'XAUUSD', 'US500', 'DE40'] and timeframes ['D1', 'H4']; running it on AUDJPY H4 would validate a strategy nobody wrote
- tsmom_dual_horizon_m47768d76 on EURGBP H4: out_of_universe -- tsmom_dual_horizon_m47768d76 declares universe ['EURUSD', 'GBPUSD', 'USDJPY', 'AUDUSD', 'USDCAD', 'USDCHF', 'NZDUSD', 'XAUUSD', 'US500', 'DE40'] and timeframes ['D1', 'H4']; running it on EURGBP H4 would validate a strategy nobody wrote
- tsmom_dual_horizon_m47768d76 on EURJPY H4: out_of_universe -- tsmom_dual_horizon_m47768d76 declares universe ['EURUSD', 'GBPUSD', 'USDJPY', 'AUDUSD', 'USDCAD', 'USDCHF', 'NZDUSD', 'XAUUSD', 'US500', 'DE40'] and timeframes ['D1', 'H4']; running it on EURJPY H4 would validate a strategy nobody wrote
- tsmom_dual_horizon_m47768d76 on GBPJPY H4: out_of_universe -- tsmom_dual_horizon_m47768d76 declares universe ['EURUSD', 'GBPUSD', 'USDJPY', 'AUDUSD', 'USDCAD', 'USDCHF', 'NZDUSD', 'XAUUSD', 'US500', 'DE40'] and timeframes ['D1', 'H4']; running it on GBPJPY H4 would validate a strategy nobody wrote
- tsmom_dual_horizon_m47768d76 on UK100 H4: out_of_universe -- tsmom_dual_horizon_m47768d76 declares universe ['EURUSD', 'GBPUSD', 'USDJPY', 'AUDUSD', 'USDCAD', 'USDCHF', 'NZDUSD', 'XAUUSD', 'US500', 'DE40'] and timeframes ['D1', 'H4']; running it on UK100 H4 would validate a strategy nobody wrote
- tsmom_dual_horizon_m47768d76 on XAGUSD H4: out_of_universe -- tsmom_dual_horizon_m47768d76 declares universe ['EURUSD', 'GBPUSD', 'USDJPY', 'AUDUSD', 'USDCAD', 'USDCHF', 'NZDUSD', 'XAUUSD', 'US500', 'DE40'] and timeframes ['D1', 'H4']; running it on XAGUSD H4 would validate a strategy nobody wrote
- tsmom_dual_horizon_md7df8207 on AUDJPY H4: out_of_universe -- tsmom_dual_horizon_md7df8207 declares universe ['EURUSD', 'GBPUSD', 'USDJPY', 'AUDUSD', 'USDCAD', 'USDCHF', 'NZDUSD', 'XAUUSD', 'US500', 'DE40'] and timeframes ['D1', 'H4']; running it on AUDJPY H4 would validate a strategy nobody wrote
- tsmom_dual_horizon_md7df8207 on EURGBP H4: out_of_universe -- tsmom_dual_horizon_md7df8207 declares universe ['EURUSD', 'GBPUSD', 'USDJPY', 'AUDUSD', 'USDCAD', 'USDCHF', 'NZDUSD', 'XAUUSD', 'US500', 'DE40'] and timeframes ['D1', 'H4']; running it on EURGBP H4 would validate a strategy nobody wrote
- tsmom_dual_horizon_md7df8207 on EURJPY H4: out_of_universe -- tsmom_dual_horizon_md7df8207 declares universe ['EURUSD', 'GBPUSD', 'USDJPY', 'AUDUSD', 'USDCAD', 'USDCHF', 'NZDUSD', 'XAUUSD', 'US500', 'DE40'] and timeframes ['D1', 'H4']; running it on EURJPY H4 would validate a strategy nobody wrote
- tsmom_dual_horizon_md7df8207 on GBPJPY H4: out_of_universe -- tsmom_dual_horizon_md7df8207 declares universe ['EURUSD', 'GBPUSD', 'USDJPY', 'AUDUSD', 'USDCAD', 'USDCHF', 'NZDUSD', 'XAUUSD', 'US500', 'DE40'] and timeframes ['D1', 'H4']; running it on GBPJPY H4 would validate a strategy nobody wrote
- tsmom_dual_horizon_md7df8207 on UK100 H4: out_of_universe -- tsmom_dual_horizon_md7df8207 declares universe ['EURUSD', 'GBPUSD', 'USDJPY', 'AUDUSD', 'USDCAD', 'USDCHF', 'NZDUSD', 'XAUUSD', 'US500', 'DE40'] and timeframes ['D1', 'H4']; running it on UK100 H4 would validate a strategy nobody wrote
- tsmom_dual_horizon_md7df8207 on XAGUSD H4: out_of_universe -- tsmom_dual_horizon_md7df8207 declares universe ['EURUSD', 'GBPUSD', 'USDJPY', 'AUDUSD', 'USDCAD', 'USDCHF', 'NZDUSD', 'XAUUSD', 'US500', 'DE40'] and timeframes ['D1', 'H4']; running it on XAGUSD H4 would validate a strategy nobody wrote

## Mutations refused before any compute

- `remove_filter` on tsmom_dual_horizon: the parent declares no filters, so none can be removed

## Rejections by rung

- RUNG 0 SANITY: 80

## Holdout

```
{
  "consumptions": [],
  "segments": [
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

This campaign carried 80 candidate cell(s) through the validation ladder over 10 instrument(s) (AUDUSD, DE40, EURUSD, GBPUSD, NZDUSD, US500, USDCAD, USDCHF, USDJPY, XAUUSD) on 1 timeframe(s) (H4), against gate set v2.0.0-audit. The true trial count for the whole search is 8496 (1120 planned in this campaign plus 7376 already spent on the same bars before it began), and that is the number the deflation used -- not the size of any one strategy's own parameter sweep. NOTHING SURVIVED. That is the expected outcome and it is a real result: it says these rule families, on this data, under these costs, do not clear a bar set for a search of this size. It does NOT say the underlying effects do not exist, that another instrument would behave the same way, or that a different cost model would give the same answer.
