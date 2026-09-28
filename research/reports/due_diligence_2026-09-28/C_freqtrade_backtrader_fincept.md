# Report C — freqtrade, backtrader, FinceptTerminal: code-level due diligence for Fiboki V2

**Date:** 2026-09-28
**Scope:** Three open-source repositories, read from their source, for components or patterns Fiboki V2 could take.
**Repositories (read directly):**

| Repo | Path | HEAD | HEAD date |
|---|---|---|---|
| freqtrade | `/home/claude/research/repos/freqtrade` (branch `develop`) | `30c00ed63230` | 2026-09-28 |
| backtrader | `/home/claude/research/repos/backtrader` (branch `master`) | `b853d7c90b67` | 2023-04-19 |
| FinceptTerminal | `/home/claude/research/repos/FinceptTerminal` (branch `main`) | `b7d850b49dc0` | 2026-09-19 |

**Fiboki baseline read:** `docs/v2/ARCHITECTURE.md`, `EXECUTION_ARCHITECTURE.md`, `VALIDATION_STANDARD.md`, `DATA_ARCHITECTURE.md`; and, where a claim below says "Fiboki already has X", the Fiboki module is cited as well.

**Method.** Every material claim cites `file:line`. Claims about maintenance come from `git log`. Where I ran something to check a claim, the probe is recorded in Appendix A. README text is treated as marketing, not evidence. Anything not verified from code is marked as an assumption in §9.

**Changes made to the clones.** All three were shallow clones. To measure 90-day cadence honestly I ran `git fetch --shallow-since=2026-06-29` on freqtrade and FinceptTerminal and `git fetch --unshallow` on backtrader. This changed only `.git` metadata. No working-tree file was modified.

---

## 0. Executive summary

| Repo | What it really is | Licence | Verdict | Worth taking |
|---|---|---|---|---|
| **freqtrade** | Crypto-only (CCXT-bound) live bot. Mature and very actively maintained, but one person makes most of the commits. | GPL-3.0 | **PORT-PATTERNS** | 6 patterns, about 12 engineer-days in total. They are live-loop plumbing that Fiboki does not have yet: a bar-boundary-aligned OANDA polling feed with a late-candle grace window, retries for idempotent reads, bar-indexed cooldown/stop-streak locks, an inbound Telegram ops surface, config-registry validation, and log-once / cycle-budget warnings. **No code should be copied.** |
| **backtrader** | A frozen 2015–2023 event-driven backtester. Its OANDA and IB stores depend on libraries that no longer install or no longer work. | GPL-3.0 | **REFERENCE-ONLY** (effectively REJECT) | Nothing. Its gap-aware stop fill matches what Fiboki already does, and its live stores reproduce V1's "timeout = rejection" defect. |
| **FinceptTerminal** | A Qt6/C++20 desktop terminal (≈468k C++ lines) that shells out to ≈403k lines of Python data scripts. It is mostly thin wrappers around third-party APIs. | AGPL-3.0, with a **self-contradictory** commercial-use overlay | **REJECT** (code and sidecar) | No code. Its connector list is useful only as a map of which official free macro sources exist. Fiboki should write its own point-in-time clients for about 5 of them. |

The most important finding is that freqtrade is *less* safe than Fiboki at exactly the points Fiboki was redesigned around. It sends no client order ID. It persists a trade only *after* the order call returns. It maps a network failure on order creation to a retryable `TemporaryError`, which the entry loop catches and moves past. Fiboki's write-ahead intent, deterministic `client_ref` and `UNKNOWN` state are already stronger. freqtrade's value to Fiboki lies in the *operational glue around* that core, not in the core itself.

---

## 1. Fiboki baseline: what already exists (checked in code, not assumed)

A "Fiboki equivalent" verdict below depends on this table.

| Concern | Fiboki today | Evidence |
|---|---|---|
| Broker error taxonomy | `BrokerUnavailable` / `BrokerRejected` / `DuplicateClientRef`. A transport failure means an UNKNOWN outcome, never a rejection. | `src/fiboki/broker/base.py:58,66,70` |
| Write-ahead order intent | A PENDING intent is fsynced before dispatch. | `broker/execution_service.py:276` (`JsonlIntentStore`), `:303` (`os.fsync`), `:449` (`submit`) |
| Idempotency key | `client_ref_for(plan)` is deterministic in the plan. | `broker/execution_service.py:147` |
| Order-state machine | 8 states, including `UNKNOWN`. A terminal state can still block resubmission. | `broker/execution_service.py:99` |
| Retry and backoff | Bounded exponential retries for **idempotent amendments only**. Entries are never retried. | `broker/position_manager.py:404-429` |
| Retry on broker *reads* | **None.** Reads raise `BrokerUnavailable` on 429/5xx/transport failure with no retry. | `broker/oanda.py:377-383` |
| Rate limiting | `RateLimiter` treated as a correctness control. | `broker/oanda.py:179` |
| Worker supervision | Single-writer lease, heartbeat, `max_consecutive_failures=20`, exponential failure backoff capped at 60 s, exit code 75 for "do not restart-loop". | `workers/base.py:126-128,749-752` |
| Closed-candle discipline | The OANDA provider drops `complete:false` candles and counts them. | `data/providers/oanda.py:141-147` |
| **Live bar feed** | **None.** `BarFeed` is a Protocol. Only replay feeds exist. | `workers/live_worker.py:104-108`; `workers/runtime.py:9` ("no implementation: nothing pulled bars for a worker") |
| Risk checks | 18 fail-closed gateway checks, including daily/weekly loss and total drawdown. | `risk/gateway.py:316`, `risk/limits.py:79-81` |
| **Cooldown / stop-streak lock** | **None.** No per-instrument re-entry lock and no N-stop-outs lock. | grep of `risk/`, `lifecycle/`, `strategy/`, `backtest/`: no hits |
| Gateway in backtest | The backtest engine does **not** call `RiskGateway`. | no reference in `backtest/engine.py` |
| Mode isolation | 5 independent live controls plus 3 adapter-level controls. | `broker/mode_guard.py:71`; `EXECUTION_ARCHITECTURE.md` §6 |
| Alert delivery | Console/file/webhook/Telegram with retry, rate limiting and a dead-letter file. **Outbound only.** | `obs/delivery.py:101,135,188,538-634`; `obs/alerts.py:432` |
| Inbound ops commands | HTTP API only (`/kill-switch/arm`, `/disarm`, promote). No Telegram command surface. | `api/routers/system.py:359,410`; `api/routers/trading.py:573` |
| Config validation | Env-var parsing. An unknown `FIBOKI_EXECUTION_MODE` is fatal, but **other values are parsed leniently**: `_flag` maps any value outside `{"1","true","yes","on"}` to False, and unknown `FIBOKI_*` names are not detected. | `api/settings.py:39-44`, `:139-160` |
| Search / optimisation | Grid sweep of declared domains feeding a trial-counted deflation ladder. No Bayesian optimiser. | `validation/ladder.py`; `VALIDATION_STANDARD.md` §2 |
| Feature pipeline | `marketstate/features.py`, `regime.py`, `cross_asset.py`; purged/embargoed CPCV in `stats/cv.py`. | module listing |
| Economic calendar | Event-type taxonomy and blackout logic. **No dated events and no network.** The documented default is "not in blackout" when unpopulated. | `marketstate/calendar.py:1-24,57` |
| Partial fills | `PARTIALLY_FILLED` maps to terminal `FILLED`, with `filled_size` tracked. This is correct for market-only IOC/FOK orders. | `broker/execution_service.py:1103-1110` |

---

## 2. freqtrade

### 2.1 What it is, from the code

- **Language / size:** Python only. `freqtrade/` has 65,529 lines; `tests/` has 79,257 lines (`wc -l`).
- **Entry points:** `freqtrade/main.py:40-83` parses a subcommand and dispatches; its exceptions map to exit codes (`ConfigurationError`, `FreqtradeException` → 2, others → 1, `KeyboardInterrupt` → 130). `freqtrade trade` builds a `Worker` (`freqtrade/worker.py:31`).
- **Runtime model:** one process and one main thread in a polling loop:
  - `Worker.run` (`worker.py:76-81`) → `_worker` (`:83-143`) → `_throttle` (`:145-186`) → `FreqtradeBot.process` (`freqtradebot.py:306-362`).
  - The loop runs at least every `PROCESS_THROTTLE_SECS = 5` s (`constants.py:17`). It aligns its sleep so that it wakes 1 s after the next timeframe boundary (`worker.py:170-177`).
  - Side threads: the Telegram bot on its own asyncio loop (`rpc/telegram.py:170,257-267`), a uvicorn API server (`rpc/api_server/uvicorn_threaded.py`) and a WebSocket producer/consumer.
- **Per-cycle order of work** (`freqtradebot.py:314-361`): reload markets → fees → open trades → pair-list refresh → candle refresh → `bot_loop_start` → `strategy.analyze` (timed) → `manage_open_orders` → exits → position adjustment → entries → scheduled jobs → commit → RPC queue.

### 2.2 Licence, maintenance, releases

- **Licence:** GPL-3.0 (`LICENSE:1-2`; `pyproject.toml` `license = {text = "GPLv3"}`).
- **90-day window (2026-06-30 → 2026-09-28):** 777 commits.
  - By author: Matthias 532 (68%), dependabot 138, Stefano 64, Freqtrade Bot 30, and 9 others with 1–5 commits each. That is 13 authors, 11 of them human.
  - By month: Jul 252, Aug 303, Sep 222.
  - **Bus factor is effectively 1–2.**
- **Release cadence:** a monthly calendar-versioned release: `2026.4` (04-30), `2026.5` (05-31), `2026.5.1` (06-03), `2026.6` (06-29), `2026.7` (07-31), `2026.8` (08-31). HEAD is `2026.9-dev` (`freqtrade/__init__.py:3`).
- **Recency caveat:** the candle-finality logic discussed in §2.6.9 landed on **2026-09-22** (`git log -S _candle_is_final` → `674dfb80c`, `448415cf1`). It is unreleased and six days old.

### 2.3 Dependency surface and conflicts with Fiboki's pins

`requirements.txt` exact pins compared with Fiboki's `pyproject.toml`:

| Package | freqtrade | Fiboki | Conflict |
|---|---|---|---|
| numpy | 2.4.6 | 2.2.6 | **yes** |
| pandas | 3.0.6 | 2.2.3 | **yes — major version**, with copy-on-write and string dtype changes |
| scipy | 1.17.1 | 1.14.1 | **yes** |
| pyarrow | 25.0.1 | 18.1.0 | **yes** |
| pydantic | 2.13.5 | 2.10.4 | yes |
| SQLAlchemy | 2.0.54 | 2.0.36 | yes (minor) |
| fastapi / uvicorn | 0.141.1 / 0.53.0 | 0.115.6 / 0.34.0 | yes |
| rich / orjson | 15.0.0 / 3.11.9 | 13.9.4 / 3.10.13 | yes |
| httpx | `>=0.24.1` (unpinned; `requirements.txt` comment) | 0.28.1 | compatible |
| Plus | ccxt 4.5.84, **TA-Lib 0.8.0 (C library)**, python-telegram-bot 22.8, cryptography, aiohttp, websockets, questionary, schedule, sdnotify, pycoingecko | — | new surface |
| hyperopt extras | scikit-learn 1.9.1, optuna 5.0.0, cmaes (`requirements-hyperopt.txt`) | — | |
| freqai extras | lightgbm, xgboost, tensorboard, datasieve (`requirements-freqai.txt`); torch 2.14, stable-baselines3 (`requirements-freqai-rl.txt`) | — | |

`pyproject.toml` uses loose ranges (`numpy>2.0,<3.0`, `pandas>=2.2.0,<4.0`), so pip *could* co-resolve freqtrade with Fiboki's pins. However, freqtrade's CI runs against pandas 3.0.6, so that combination is untested. **freqtrade cannot be a dependency of Fiboki** without breaking the exact-pin determinism contract (`ARCHITECTURE.md` §9).

### 2.4 Security review

| Item | Finding | Citation |
|---|---|---|
| `eval` / `exec` | None in `freqtrade/` (hits are `torch` `.eval()`). | grep |
| Arbitrary code loading | Strategies, protections and pair lists load user `.py` files through `spec_from_file_location` / `exec_module`. | `resolvers/iresolver.py:93-99` |
| Base64 strategy injection | A strategy name `Name:<base64>` is decoded, written to a temp dir and imported. It is blocked on the REST API by `_no_base64_strategy`, but the **config-file path accepts it**. | `resolvers/strategy_resolver.py:281-289`; `rpc/api_server/api_schemas.py:21` |
| `subprocess` | `git log` for the dev version string; `sudo chown -R ftuser:` in a Docker directory helper. | `freqtrade/__init__.py:9-18`; `configuration/directory_operations.py:40-42` |
| Pickle / unsafe deserialisation | `cloudpickle.load` of FreqAI models, pipelines and historic predictions; `torch.load(..., weights_only=False)`; pickled hyperopt and backtest results. **Anyone who can write the model directory gets code execution.** | `freqai/data_drawer.py:183,194,698-700,710,720-723`; `optimize/hyperopt/hyperopt_optimizer.py:14-16,471-473`; `data/btanalysis/bt_fileutils.py:486-522` |
| Secret handling | Secrets live in the JSON config or `FREQTRADE__*` env vars (`configuration/environment_vars.py`). `sanitize_config` redacts a fixed key list for display. Keys are removed for backtest/hyperopt. | `configuration/config_secrets.py:6-24`, `:27-49` |
| API auth | JWT with a `compare_digest` login check. | `rpc/api_server/api_auth.py:24,34-48` |
| Telegram auth | The chat ID must match. **`authorized_users` is optional**: in a group chat, any member can issue `/forceexit`, `/stop`, `/reload_config` unless the list is set. | `rpc/telegram.py:120-134` |
| Force entry | Gated by `force_entry_enable` (default False). | `rpc/rpc.py:1139` |
| Telemetry | None found (grep for telemetry/analytics/sentry/posthog returns nothing). | grep |
| Outbound hosts | ccxt exchange hosts; `data.binance.vision`; CoinGecko via `pycoingecko` (`util/coin_gecko.py:1`); `api.github.com` for the FreqUI download (`commands/deploy_ui.py:60`); Telegram; user-configured webhook/Discord. | grep |

### 2.5 Asset-class fit (OANDA / IG spread betting)

freqtrade is **structurally crypto-only**:

1. `check_exchange` rejects any exchange that ccxt does not know (`exchange/check_exchange.py:42-47`). Every exchange object is a ccxt instance (`exchange/exchange.py:283-299`, `_init_ccxt` at `:392`).
2. ccxt 4.5.84, the version freqtrade pins, has **104 exchange modules and none is OANDA, IG or IBKR**. The only non-crypto broker is `alpaca` (Appendix A, probe 3).
3. `TradingMode` is `spot | margin | futures` (`enums/tradingmode.py`). There is no concept of a £-per-point stake, a spread-bet contract or a guaranteed stop.
4. There is no market-hours or session calendar in the exchange layer (grep: nothing). Missing candles are **forward-filled with flat zero-volume bars** (`data/converter/converter.py:126-160`, called with `fill_missing=True` on the live cache merge at `exchange/exchange.py:2965-2972`). For FX this would manufacture about 48 h of flat bars every weekend, and every indicator would then see them.
5. Staleness is judged in wall-clock terms: `timeframe*2 + 5` minutes (`strategy/interface.py:1301-1316`). This would report every Monday FX open as "outdated".
6. The annualisation constants are crypto calendar-days (365): `data/metrics.py:344`; `hyperopt_loss_sharpe_daily.py:39`.

**What an OANDA/IG port would need:** a non-ccxt `Exchange` subclass that re-implements about 4,380 lines of `exchange/exchange.py` behaviour; a sessionised calendar threaded through data refresh, protections and staleness checks; removal of gap-filling; stake-per-point sizing; and GSLO semantics. That is a rewrite, not a port. **Conclusion: take patterns only.**

### 2.6 Component by component

Each entry covers what it does, its quality, the Fiboki equivalent, a recommendation and an effort estimate in engineer-days.

#### 2.6.1 `freqtradebot.py` main loop and error handling

- **What it does.** See §2.1. The worker catches **only** `TemporaryError`: it logs, sleeps a flat `RETRY_TIMEOUT = 30` s and loops (`worker.py:200-202`; `constants.py:19`). `OperationalException` sends a Telegram traceback and sets `State.STOPPED` without exiting (`worker.py:203-212`). Anything else propagates to `main.py:73-75`, which gives exit code 1, and systemd restarts the bot (`freqtrade.service.watchdog`: `Restart=always`, `StartLimitBurst=5`, `WatchdogSec=20`, pinged through `sd_notify` at `worker.py:61-74,113,121`).
- **Quality.**
  - Good: a clear phase order per cycle, and an `_exit_lock` that stops Telegram force-exits racing the loop (`freqtradebot.py:166-167,335-352`).
  - Good: `MeasureTime` warns when strategy analysis exceeds 25% of the timeframe (`:206-214`).
  - Weak: the retry is a flat 30 s with no failure budget and no escalation.
  - Weak: the `schedule` library runs in *local* time, and the code says so itself: "schedule is in local time by default (!)" (`:193-196`).
- **Defect found: startup order refresh.** In `startup_update_open_orders`, the branch commented "Order is older than 5 days. Assuming order was fully cancelled" tests `order.order_date_utc - timedelta(days=5) < now` (`freqtradebot.py:603-611`). That condition is true for **every** order dated in the past, not only for orders older than 5 days. So *any* `InvalidOrderException` during startup refresh marks the order locally as cancelled. The condition is the wrong way round compared with its comment.
- **Fiboki equivalent.** `workers/base.py` is stronger: a lease, a heartbeat, a consecutive-failure budget, exponential backoff and distinct exit codes (`:126-128,749-752`). **Missing in Fiboki:** a wake-up aligned to the timeframe boundary. Fiboki has no live feed yet, so this is a gap.
- **Recommendation.**
  - **PORT-PATTERN**: the timeframe-aligned wake in `worker.py:170-177` (sleep until the next boundary + offset; never wake between the boundary and the offset). It belongs inside the new OANDA `BarFeed` (§2.6.9). 0.5 d, counted in 2.6.9.
  - **PORT-PATTERN**: the analysis-time budget warning (`MeasureTime` at 25% of the timeframe) as an `obs/metrics` gauge plus alert. 0.25 d.
  - **IGNORE** the rest.

#### 2.6.2 `exchange/`: retry/backoff decorators and ccxt wrappers

- **What it does.**
  - `@retrier` and `@retrier_async` re-invoke on `TemporaryError`/`RetryableOrderError`, with `API_RETRY_COUNT = 4`. The delay is `(max − remaining)² + 1` seconds, applied **only** for `DDosProtection`/`RetryableOrderError`; other temporary errors retry immediately (`exchange/common.py:35-36,113-200`).
  - There is a KuCoin-specific hack for 429s (`:136-145`).
  - Every ccxt call maps ccxt exceptions onto freqtrade's taxonomy (`exceptions.py:36-77`).
  - `_ft_has_default` is a per-exchange capability map: stop-loss-on-exchange support, partial-candle behaviour, grace seconds, poll interval and so on (`exchange/exchange.py:131-170`).
- **The critical path: order creation.**
  - `create_order` is *not* decorated with `@retrier` (the retried methods are listed in Appendix A, probe 4; `create_stoploss` has `retries=0` at `:1625`).
  - But it maps `ccxt.OperationFailed | ccxt.ExchangeError` to **`TemporaryError`** (`exchange/exchange.py:1566-1569`). ccxt's `NetworkError`/`RequestTimeout` family descends from `OperationFailed` (assumption A3 in §9). **A timeout on order submission therefore becomes a "temporary error".**
  - The caller `enter_positions` catches `DependencyException`, of which `TemporaryError` is a subclass, logs "Unable to create trade" and carries on (`freqtradebot.py:844-845`).
  - **No client order ID is sent.** The only `clientOrderId` reference in the package is a Bitget read path (`exchange/bitget.py:93`).
  - The `Trade` row is created and committed only **after** `create_order` returns (`freqtradebot.py:1131` call vs `:1235-1236` `session.add`/`commit`).
  - Together these mean a lost response can leave an untracked live position. On the next loop the signal is still present, so a second entry is possible. This is exactly the V1 failure mode that `EXECUTION_ARCHITECTURE.md` §4 was designed against. (freqtrade's partial mitigation, `handle_onexchange_order` at `freqtradebot.py:684-790`, only re-finds orders for trades that already exist.)
- **Fiboki equivalent.** Fiboki's taxonomy is **more correct**: transport failure means UNKNOWN; the intent is written ahead; the `client_ref` is deterministic. Fiboki has **no retry on idempotent reads** (`broker/oanda.py:377-383` raises straight away on 429/5xx for account summary, `openTrades`, `pendingOrders` and `instruments` at `:416,434,461,490`).
- **Recommendation.**
  - **PORT-PATTERN** (not the code): a `retry_idempotent_read` decorator with exponential backoff, jitter and a per-call deadline. Apply it only to GET endpoints of `OandaAdapter` and the data providers. Add an AST test next to `test_no_gateway_bypass.py` asserting it is never applied to `place_order`, `close`, `close_partial` or `amend`. **1 d.**
  - **PORT-PATTERN** (optional): a declared per-adapter capability map in the style of `_ft_has` (for example `supports_client_ref`, `supports_partial_close`, `min_stop_source`, `candle_complete_flag`). **0.5 d.**
  - **Explicitly reject** freqtrade's order-creation error mapping.

#### 2.6.3 `persistence/`: trade and order models, order state, partial fills

- **What it does.**
  - `Order` mirrors the ccxt order: `status` string, `filled`, `remaining`, `average`, `ft_is_open` flag; unique on `(ft_pair, order_id)` (`persistence/trade_model.py:65-120`).
  - The state is **not an explicit machine**. `update_from_ccxt_object` copies the venue's status string and sets `ft_is_open = status not in NON_OPEN_EXCHANGE_STATES` (`:197-230`; `constants.py:133-134`).
  - The trade is re-derived as a fold over filled orders, giving a weighted-average entry, realised profit per exit leg and funding (`recalc_trade_from_orders`, `:1267-1310`). This makes partial fills and DCA consistent.
  - Partial fills of IOC/FOK entries are handled inline (`freqtradebot.py:1152-1176`). Unfilled limit remainders are cancelled on timeout with the reasons `PARTIALLY_FILLED` / `PARTIALLY_FILLED_KEEP_OPEN` (`constants.py:209-212`).
  - `PairLock`, `KeyValueStore` and `CustomData` are side tables (`persistence/pairlock.py`, `key_value_store.py`, `custom_data.py`). Migrations are hand-written column probes (`persistence/migrations.py`, 482 lines).
- **Quality.** The fold-over-orders is sound and well tested. There is no UNKNOWN or PENDING state: a local order row only exists once the venue has returned an ID.
- **Fiboki equivalent.** Fiboki has `IntentState` with UNKNOWN, a fsynced JSONL intent log, and a PositionBook shared across backtest, paper and venue. Fiboki maps `PARTIALLY_FILLED` to terminal `FILLED` (`execution_service.py:1105-1107`). That is correct today because Fiboki is market-only (`EXECUTION_ARCHITECTURE.md` §1). **It would become wrong the day a resting limit order is introduced.**
- **Recommendation.** **REFERENCE-ONLY.** If Fiboki ever adds resting orders, adopt the pattern of an order row carrying `filled`, `remaining` and `is_open`, with the position recomputed as a fold over fills, and add `PARTIALLY_FILLED_OPEN` as a non-terminal intent state. **0 d now; about 2 d if limit orders are ever scoped.**

#### 2.6.4 `plugins/protections/*`: cooldowns and locks

- **What it does.**
  - `IProtection` defines `global_stop` and `stop_per_pair`, each returning a `ProtectionReturn(lock, until, reason, lock_side)` (`plugins/protections/iprotection.py:17-22,97-122`).
  - `ProtectionManager` persists locks as `PairLock` rows (`plugins/protectionmanager.py:50-94`; `persistence/pairlock.py:11-31`).
  - The concrete protections are:
    - `StoplossGuard`: N stop-outs within a lookback, optionally per side (`stoploss_guard.py:44-89`).
    - `CooldownPeriod`: a lock after any close (`cooldown_period.py:28-50`).
    - `MaxDrawdown` (`max_drawdown_protection.py:60-108`).
    - `LowProfitPairs`.
  - After every closed trade, `handle_protections` applies a one-candle "Auto lock" and then evaluates the pair and global protections (`freqtradebot.py:2622-2644`).
  - The config validator rejects mixing minute and candle units (`protectionmanager.py:96-131`).
- **Quality: good abstraction, three defects for Fiboki's purposes.**
  1. **Durations are wall-clock minutes.** "Candles" are converted to minutes with `tf_in_min * n` (`iprotection.py:42`), and the lock end is `last_close + minutes` (`:124-141`). A "4 candles" lock on H1 set on a Friday at 21:00 expires over the weekend without a single trading bar passing.
  2. **Protections are opt-in in backtest** (`enable_protections` defaults to False at `optimize/backtesting.py:243`, checked at `:1326`) but **always on in live**. That is a parity gap by default.
  3. `MaxDrawdown` duplicates what Fiboki's gateway already does, and the legacy "ratios" mode sums per-trade ratios rather than using equity (`max_drawdown_protection.py:84-93`).
- **Fiboki equivalent.** Daily/weekly loss and total-drawdown checks exist (`risk/limits.py:79-81`). **Per-instrument cooldown and stop-streak locks do not.** The gateway is not called by the backtest engine, so a gateway-only lock would not appear in research.
- **Recommendation.** **PORT-PATTERN**, adapted:
  - Express `CooldownRule(bars_after_close)` and `StopStreakRule(n_stops, lookback_bars, lock_bars, scope=instrument|strategy|global)` as **bar-indexed** rules on the session calendar.
  - Declare them in the strategy document, so the engine and the paper broker enforce them identically.
  - Mirror them as a named gateway check (`_check_instrument_lock`), so a restart recovers locks from the intent/position ledger rather than from in-memory state.
  - Add a parity test in `tests/integration/test_paper_backtest_parity.py` style.
  - **3 d**, including tests.
  - Do not port `MaxDrawdown` or `LowProfitPairs`.

#### 2.6.5 Strategy interface and callbacks

- **What it does.** `IStrategy` exposes about 20 hooks, including:
  - `confirm_trade_entry` / `confirm_trade_exit` (`strategy/interface.py:359,395`);
  - `custom_stoploss` (`:446`), `custom_exit` (`:594`), `custom_stake_amount` (`:625`), `adjust_trade_position` (`:654`), `leverage` (`:832`);
  - `order_filled` (`:433`), `check_entry_timeout` / `check_exit_timeout` (`:305,336`), `adjust_order_price` (`:771`).
  - Every user hook runs through `strategy_safe_wrapper`, which deep-copies `trade` so the hook cannot mutate it and converts exceptions into `StrategyError` or a default value (`strategy/strategy_wrapper.py:31-62`).
- **Quality: flexible, but at odds with Fiboki's architecture in three places.**
  1. `custom_stake_amount`, `adjust_trade_position` (which returns a *stake*) and `leverage` let **the strategy size positions**. That violates Fiboki's "Signal carries no size; `size_trade` is the single authority" rule (`ARCHITECTURE.md` §3).
  2. In live trading, exit callbacks run every 5 s against a **ticker rate** (`handle_trade` → `exchange.get_rate` at `freqtradebot.py:1555`). In backtest they run per candle on OHLC. That is intrabar in one mode and bar-close in the other, a structural parity gap, and it breaks Fiboki's closed-candle-only rule.
  3. `confirm_trade_entry` is a per-strategy veto that belongs in the risk layer.
- **Fiboki equivalent.** DSL documents compiled by `strategy/compiler.py`; exits in `backtest/exits.py`; vetoes in `RiskGateway`.
- **Recommendation.** **IGNORE.** The one idea worth noting is `strategy_safe_wrapper`'s deep copy of mutable state before user code sees it. Fiboki's DSL does not run user code, so it does not need it.

#### 2.6.6 `rpc/telegram.py`: command surface

- **What it does.**
  - 39 command handlers (`rpc/telegram.py:274-` registration): `status`, `profit`, `balance`, `start`, `stop`, `forceexit`/`fx`, `forcelong`, `forceshort`, `reload_trade`, `trades`, `delete`, `cancel_open_order`, `performance`, `entries`, `exits`, `mix_tags`, `stats`, `daily`, `weekly`, `monthly`, `count`, `locks`, `unlock`, `reload_config`, `show_config`, `stopentry`/`pause`, `whitelist`, `blacklist`, `blacklist_delete`, `logs`, `health`, `help`, `version`, `marketdir`, `order`, `list_custom_data`, `tg_info`, `profit_long`, `profit_short`.
  - The bot runs in its own thread and event loop (`:170,257-267`). Authorisation is by chat ID, optional topic, and an **optional** user allow-list (`:99-147`).
  - The business logic lives in `rpc/rpc.py` (1,839 lines) and is shared with the REST API. That separation is good.
  - Webhook delivery is **synchronous inside the trading loop's thread**, with `retries` × `retry_delay` sleeps and a 10 s timeout (`rpc/webhook.py:36-38,115-140`; dispatched from `rpc/rpc_manager.py:66-84`). A slow webhook stalls trading.
- **Fiboki equivalent.** Outbound Telegram and webhook delivery, with better resilience than freqtrade's (rate limiting, dead-letter, severity routing: `obs/delivery.py`). **No inbound command surface.**
- **Recommendation.** **PORT-PATTERN**, a deliberately smaller and safer surface:
  - Read-only commands: `/status`, `/positions`, `/pnl`, `/health`, `/locks`, `/incidents`.
  - One mutating command: **`/killswitch_arm`**. There is **no disarm, no force-entry and no force-exit** over Telegram, because exits already go through the gateway.
  - Run it as its own supervised process using the existing `obs/transport` HTTP client (long-poll `getUpdates`), not python-telegram-bot threads.
  - Make the user-ID allow-list mandatory, fail-closed when empty.
  - Every command goes to `api/audit_trail.py`.
  - Reuse the API's service functions (the `rpc.py` separation idea).
  - **3 d.**

#### 2.6.7 `optimize/hyperopt*` and loss functions

- **What it does.**
  - Optuna study with a selectable sampler (TPE/GP/CMA-ES/NSGA-II/III/QMC), seeded with `random_state` (`optimize/hyperopt/hyperopt_optimizer.py:55-61,405-438`).
  - joblib `Parallel` workers with cloudpickle-registered strategy modules (`:14-16,156`; `hyperopt.py:15,136`). Data is pickled to disk (`hyperopt_optimizer.py:471-473`).
  - 12 loss functions (`optimize/hyperopt_loss/*`).
- **Quality: problems with the losses.**
  - `calculate_sharpe` divides *sum of per-trade returns ÷ days* by the **standard deviation of per-trade returns**, then multiplies by √365 (`data/metrics.py:455-475`, with `_calculate_annualized_ratio` at `:341-358`). The numerator is per day and the denominator is per trade, so the result is not a Sharpe ratio. Its bias depends on trade frequency.
  - `SharpeHyperOptLossDaily` hard-codes 0.05% "slippage" per trade and a 365-day year (`hyperopt_loss_sharpe_daily.py:38-39`).
  - The best epoch is selected with **no multiple-testing correction and no record of trials for deflation**.
- **Fiboki equivalent.** A declared-domain sweep; rung 1 is explicitly *not evidence*; DSR/PBO/SPA with the cross-trial variance kept (`VALIDATION_STANDARD.md` §2). This is strictly stronger for honesty.
- **Recommendation.** **IGNORE.** If Fiboki ever wants adaptive search, use Optuna directly behind `discovery/campaign.py` and write *every* trial into the append-only experiment ledger, so that effective-N is honest. Do not take freqtrade's wrapper or its losses.

#### 2.6.8 `freqai/`: feature pipeline and model lifecycle

- **What it does.**
  - `FreqaiDataKitchen` builds features from strategy-populated columns, including shifted and correlated-pair features (`freqai/data_kitchen.py:716-777`). It applies a `datasieve` pipeline (`:13,82-83`) and a sklearn `train_test_split` (`:124-175`).
  - `FreqaiDataDrawer` persists models and metadata (`data_drawer.py`). Retraining is triggered by `live_retrain_hours` and model expiry (`data_kitchen.py:517-560`).
  - Backtesting slides a `train_period_days` window with the backtest window "directly following" it (`freqai/freqai_interface.py:296-310`; `data_kitchen.py:316-372`).
- **Quality: fails Fiboki's validation standard.**
  - There is **no purge or embargo** between training and the following backtest window, although labels are built with forward shifts. `buffer_train_data_candles` is an optional, manual mitigation (`data_kitchen.py:985-1006`).
  - `shuffle_after_split` uses **unseeded** `random.randint` (`:167-169`), so results are not deterministic.
  - Models are persisted with cloudpickle and `torch.load(weights_only=False)` (§2.4).
  - Five "hedge-fund-style" model classes and RL environments add a very large dependency surface (torch, SB3).
- **Fiboki equivalent.** `marketstate/features.py` (1,290 lines) and `stats/cv.py` (CPCV with purge and embargo).
- **Recommendation.** **IGNORE.** The only idea of note is an explicit *model-expiry / retrain cadence* as data (`check_if_model_expired`), which becomes relevant only if an ML strategy ever reaches the lifecycle ladder.

#### 2.6.9 `data/dataprovider.py` and candle-completeness handling

- **What it does.**
  - `DataProvider` gives strategies one API across run modes. In backtest it slices informative data at `timeframe_to_prev_date(slice_date)` to prevent look-ahead (`data/dataprovider.py:371-400`) and caps analysed frames at the current index (`:402-425`).
  - Live candle finality (`exchange/exchange.py`):
    - `_process_ohlcv_df` drops the last candle if it is the one forming at *fetch start*, or if it is the just-closed candle and no newer candle has been issued (`:2908-2975`).
    - `_candle_is_final` treats the just-closed candle as final only after a grace period (`:3087-3106`; `ohlcv_late_candle_grace_secs=15` at `:144`).
    - `_now_is_time_to_refresh` keeps re-polling within the grace window when the venue has not yet published the last completed candle (`:3108-3133`).
  - Staleness: `get_latest_candle` rejects data older than `2×timeframe + 5 min` (`strategy/interface.py:1301-1316`). `process_only_new_candles` skips re-analysis for an already-seen candle (`:1216-1233`).
- **Quality.** The finality logic is careful and well commented, but **six days old and unreleased** (§2.2). Gap filling (`converter.py:126-160`) is wrong for session markets. Completeness is *inferred from time*, because many crypto venues give no flag.
- **Fiboki equivalent.** The OANDA provider uses the venue's **authoritative `complete` flag** and reports the count dropped (`data/providers/oanda.py:141-147`). That is better than inference. **But no live `BarFeed` exists** (`workers/runtime.py:9`).
- **Recommendation.** **PORT-PATTERN**: build `OandaPollingBarFeed` in `workers/runtime.py`:
  1. Wake aligned to the bar boundary + offset (§2.6.1).
  2. `complete:true` is authoritative. Timing is used only as a *second* check: a candle whose close is before now − grace but which is still `complete:false` raises a data-quality event.
  3. Re-poll within a bounded grace window when the last closed bar is missing; this is freqtrade's `_now_is_time_to_refresh` idea.
  4. **Never gap-fill.** Consult `data/calendars.py`, so a weekend is "closed" rather than "stale".
  5. Report per-instrument data age into `RiskContextBuilder` for the gateway's `data_freshness` and `stale_price` checks.
  - Test with `RecordedTransport` fixtures, including late publication and a Friday-to-Sunday gap.
  - **3 d.**

#### 2.6.10 `configuration/`: schema validation

- **What it does.**
  - A JSON-Schema Draft-4 validator extended to inject defaults (`configuration/config_validation.py:26-43`).
  - The *required* key set changes by run mode: trade, backtest (preliminary or final), webserver, minimal (`:46-70`).
  - About a dozen cross-field consistency validators run after the strategy loads (`:73-99`).
  - `FREQTRADE__SECTION__KEY` env vars are typed via JSON parsing (`environment_vars.py:14-82`).
- **Quality.** Solid and battle-tested. The schema lives in `config_schema/config_schema.py` (from `:34`).
- **Fiboki equivalent.** `api/settings.py` parses the environment by hand. The execution mode is strict, but booleans are lenient (a typo such as `"ture"` becomes False, `:39-44`) and unknown `FIBOKI_*` names are silently ignored.
- **Recommendation.** **PORT-PATTERN** (in Fiboki's idiom, not JSON Schema):
  - a declared registry of every `FIBOKI_*` variable with its type, default and the process roles that require it;
  - strict boolean parsing (accept only true/false tokens, anything else is fatal);
  - a warning on any unknown `FIBOKI_*` variable, and a *startup error* in DEMO/LIVE;
  - per-role required sets, mirroring freqtrade's per-run-mode `required`.
  - **1 d.**

#### 2.6.11 `edge/`

Removed from freqtrade in 2025.6. Configuring it now raises `ConfigurationError` (`configuration/config_validation.py:160-168`; commit `ff06d58ac`). **N/A.**

#### 2.6.12 `wallets.py`

- **What it does.** Keeps multi-currency crypto balances (live via `get_balances` or simulated for dry-run) and computes stake as `available_capital`, `tradable_balance_ratio` or "unlimited" (`wallets.py:107-262,314-458`).
- **Fiboki equivalent.** `risk/accounting.py`, `core/money.py`, `portfolio/sizing.py`.
- **Recommendation.** **IGNORE.** It is crypto balance semantics, and the stake-sizing logic would compete with `size_trade`.

#### 2.6.13 `util/` and mixins

| Helper | What it is | Recommendation |
|---|---|---|
| `LoggingMixin.log_once` (`mixins/logging_mixin.py:25`) | TTL-deduplicated logging, used heavily in the loop | **PORT-PATTERN**, 0.25 d. Fiboki deduplicates *alerts* but not repeated log lines in a 5 s loop. |
| `MeasureTime` (`util/measure_time.py`) | Context manager that warns over a time budget | Covered in §2.6.1. |
| `PeriodicCache` (`util/periodic_cache.py`, 19 lines) | TTL cache aligned to period boundaries | IGNORE (trivial) |
| `FtPrecise` (`util/ft_precise.py`) | Wraps ccxt `Precise` | IGNORE (Fiboki has `core/money.py`) |
| `FtScheduler` (`util/ft_scheduler.py`) | `schedule` wrapper, **local time** | IGNORE, and avoid |
| `datetime_helpers`, `formatters`, `rich_tables` | small utilities | IGNORE |

#### 2.6.14 Other items relevant to a 24/5 bot

| Item | Finding | Recommendation |
|---|---|---|
| Dry-run (`exchange/exchange.py:1206-1260`, gated at `:1511`) | Simulates fills against the *live order book*, so it diverges from the backtest fill model | IGNORE. Fiboki's PAPER shares `sim/` with the backtest (`ARCHITECTURE.md` §2). |
| Live/dry switch | A single boolean, `dry_run` (`config_schema/config_schema.py:91`) | IGNORE. Fiboki's 12-control live path is far stronger. |
| Startup reconciliation | `startup_update_open_orders` runs before the first loop (`freqtradebot.py:264-282,570-616`) | **PORT-PATTERN** (ordering only): call Fiboki's existing `reconcile` in the worker's startup before the first cycle. This closes the "reconciliation is not scheduled" gap in `EXECUTION_ARCHITECTURE.md` §7. **0.5 d** of wiring, excluding the known defect at `:604`. |
| systemd watchdog | `sd_notify` `WATCHDOG=1` every cycle (`worker.py:61-74`) | IGNORE for launchd. Keep in mind if `deploy/systemd/` becomes live. |
| Strategy plugin loading (`resolvers/*`) | Imports arbitrary user `.py` files, including base64 | IGNORE. Fiboki's JSON DSL removes this whole class of risk. |

### 2.7 freqtrade scorecard

| Component | Quality | Fiboki has it? | Recommendation | Engineer-days |
|---|---|---|---|---|
| Main loop / worker | Good | Stronger (`workers/base.py`) | PORT-PATTERN (boundary-aligned wake, analysis budget) | 0.25 (+0.5 in 2.6.9) |
| Exchange retrier | Adequate for reads, **unsafe mapping on writes** | Partly (amend retry only) | PORT-PATTERN (read-only retry + AST guard) | 1 |
| Capability map `_ft_has` | Good | No | PORT-PATTERN (optional) | 0.5 |
| Persistence / orders | Good fold; no UNKNOWN or write-ahead | Stronger | REFERENCE-ONLY | 0 |
| Protections | Good abstraction; wall-clock, backtest opt-in | **No** | PORT-PATTERN (bar-indexed, parity) | 3 |
| Strategy callbacks | Flexible; conflicts with sizing authority and closed-candle rule | N/A by design | IGNORE | 0 |
| Telegram RPC | Rich; optional user auth; webhook blocks the loop | Outbound only | PORT-PATTERN (read-only + arm-only) | 3 |
| Hyperopt / losses | Sharpe unit error; no deflation | Stronger | IGNORE | 0 |
| FreqAI | Leakage, non-determinism, unsafe pickles | Stronger | IGNORE | 0 |
| DataProvider / candle finality | Careful but new; gap-fill wrong for FX | Partly (flag-based); **no live feed** | PORT-PATTERN (OANDA polling `BarFeed`) | 3 |
| Config validation | Solid | Partial | PORT-PATTERN (env registry, strict bools) | 1 |
| Edge | Removed | — | N/A | 0 |
| Wallets | Crypto semantics | Yes | IGNORE | 0 |
| `util/` | Small helpers | Partly | PORT-PATTERN (`log_once`) | 0.25 |
| Startup reconcile ordering | Good pattern (one defect) | Method exists, unwired | PORT-PATTERN | 0.5 |
| **Total** | | | | **≈ 12.5 d** (plus 0.5 d optional) |

---

## 3. backtrader

### 3.1 What it is, from the code

- **Language / size:** pure Python, 35,415 lines in `backtrader/`.
- **Entry points:** `Cerebro.run` (`backtrader/cerebro.py`) and the CLI `btrun` (`backtrader/btrun/btrun.py`).
- **Runtime model:** a metaclass-driven "lines" engine (`metabase.py`, `lineiterator.py`, `linebuffer.py`) stepping bars through Strategy → Broker → Analyzers/Observers. Live stores run their own threads and queues (`stores/oandastore.py:319-399`).
- **Packaging:** `setup.py` has **no `install_requires`** (`:109-112`) and Python classifiers only up to 3.7 (`:87-93`). The version comes from `exec(compile(...))` of `version.py` (`setup.py:37`); `__version__ = '1.9.78.123'`.

### 3.2 Licence, maintenance

- **Licence:** GPL-3.0-or-later (`LICENSE:1-2`; header in `setup.py:9-10`).
- **Maintenance:** HEAD is 2023-04-19. **0 commits in the last 90 days; 0 in the last 3.4 years.**
  - Full history: 2,404 commits since 2015-01-10, of which 2,164 (90%) are by `mementum`.
  - 2020–2022: 37 commits; 2019: 30.
  - The last tagged release is `1.9.74.123` (2019-05-30). The final `1.9.78.123` commit is untagged.
- **Frozen.**

### 3.3 Python-version state (verified)

- `collections.Iterable` is used at `backtrader/lineiterator.py:229,237`; it was removed in Python 3.10. Running a strategy whose indicator calls `bindlines(owner=0)` on Python 3.11 raises `AttributeError: module 'collections' has no attribute 'Iterable'` (Appendix A, probe 1).
- `cerebro.py:30` was patched to `collections.abc`, so the fix is partial.
- Simple SMA + analyzer runs do work on 3.11 with numpy 2.4.4 / pandas 3.0.2 in this sandbox. **Not tested against Fiboki's numpy 2.2.6 / pandas 2.2.3 pins.**

### 3.4 Broker and data feeds

| Feed / store | State | Evidence |
|---|---|---|
| OANDA (`stores/oandastore.py`, `brokers/oandabroker.py`, `feeds/oanda.py`; 1,465 lines) | **Unusable.** It imports `oandapy` and subclasses `oandapy.API`/`oandapy.Streamer` (`oandastore.py:30,65,99`), which is the retired v1 REST client API (assumption A5). The package installable from PyPI as `oandapy` (0.0.9) is a *different* v20 client that exports only `APIv20` and fails to import on Python 3 (`ModuleNotFoundError: No module named 'status'`). `backtrader.stores` hides the failure with `try/except ImportError` (`stores/__init__.py:37-39`), so `hasattr(bt.stores, 'OandaStore')` is False. | Appendix A, probe 2 |
| OANDA order path (for the record) | **Any exception from `create_order`, including a timeout, is recorded as a rejection, and then the order thread `return`s**, so every later order is silently never sent. This is V1's defect plus a thread death. | `stores/oandastore.py:508-514`; also `:529-530` |
| IB (`stores/ibstore.py`, `brokers/ibbroker.py`, `feeds/ibdata.py`; 2,791 lines) | Depends on **IbPy** (`from ib.ext.Contract import Contract`, `import ib.opt`: `ibstore.py:33-34`), an unofficial, long-unmaintained wrapper (assumption A6). Also hidden by `try/except ImportError` (`stores/__init__.py:27-29`). | code |
| VisualChart, Quandl, Yahoo, Sierra, MT4 CSV, InfluxDB, Blaze, pandas (`feeds/*`) | Mostly legacy vendors. `PandasData` works. | `backtrader/feeds/` |

### 3.5 What (if anything) is worth reading

| Component | Assessment | Fiboki equivalent | Recommendation |
|---|---|---|---|
| `brokers/bbroker.py` stop fills (`:921-944`) | A stop gapped through at the open fills at the open; otherwise at the trigger, with optional slippage. The same principle as Fiboki's `_worse_exit`. | `sim/fills.py` (stronger: both-leg costs, STOP_FIRST policy, stale-spread widening) | REFERENCE-ONLY (it confirms Fiboki's choice) |
| `brokers/bbroker.py` `coc` / `coo` (`:179-190`) | "Cheat-on-close/open": explicit look-ahead switches | — | **Do not take** |
| `order.py` status enum (`:250-257`) | Created/Submitted/Accepted/Partial/Completed/Canceled/Expired/Margin/Rejected. **No UNKNOWN.** | `IntentState` (stronger) | IGNORE |
| Analyzers (`analyzers/*.py`) | `SharpeRatio` defaults to `timeframe=Years`, `riskfreerate=0.01`, `annualize=False` (`analyzers/sharpe.py:84-95`), a trap for anyone quoting it. `SQN` (`sqn.py:73-84`), `VWR`, `DrawDown` and `TradeAnalyzer` are textbook. | `stats/sharpe.py`, `backtest/metrics.py` (annualised from the real elapsed span) | IGNORE |
| Observers (`observers/*.py`) | Plot-time series for matplotlib | Plotly / KLineChart on the frontend | IGNORE |
| `tradingcal.py` (280 lines) | Trading-calendar adapter, including `pandas_market_calendars` | `data/calendars.py` | IGNORE |
| `btrun/btrun.py` | `eval('dict(' + user_string + ')')` on CLI arguments (`:82,137,164,348,392`) | — | **Do not take** |

**Verdict: REFERENCE-ONLY.** In practice it is REJECT, because nothing in it improves on Fiboki and its live paths repeat V1's worst execution defect.

---

## 4. FinceptTerminal

### 4.1 What it is, from the code

- **Languages / size** (`fincept-qt/`):

  | Kind | Files | Lines |
  |---|---|---|
  | C++ `.cpp` | 1,041 | 391,508 |
  | Headers `.h` | 975 | 76,952 |
  | Python `.py` | 1,368 | 403,229 |
  | `.ts` | 11 | 453,797 (Qt Linguist XML translation files, not TypeScript: `translations/fincept_*.ts`) |

- **UI architecture:** a **Qt 6.8 Widgets desktop application in C++20** (`CMakeLists.txt:36,47,474-513`; Qt WebEngine and WebSockets are optional). It is not a web app. `src/screens/` alone has about 250k lines. Entry point: `src/app/main.cpp:208`.
- **Runtime model:** a Qt event loop with an in-process pub/sub `DataHub` (`src/datahub/`, e.g. topic `econ:fincept:upcoming_events` at `services/economics/MacroCalendarService.cpp:17`). Python scripts run as **`QProcess` subprocesses** in two uv-managed venvs, `venv-numpy1` and `venv-numpy2`, chosen by script name (`src/python/PythonRunner.cpp:408-428`).
- **Brokers in C++** (`src/trading/brokers/`): about 23, mostly Indian retail brokers, plus Alpaca, Tradier, IBKR (Client Portal Gateway `https://localhost:5000/v1/api/iserver/...`, `ibkr/IBKRBroker.cpp:28,159-164,221`), Saxo and MetaApi (MT4/5). **No OANDA and no IG.**

### 4.2 Licence: internally contradictory (read carefully)

At the same commit (`931494a`, 2026-08-11, "version 4.4.0 update") the repository says two incompatible things:

- **`LICENSE`:**
  - "DUAL LICENSING NOTICE … The AGPL-3.0 option is NOT available for Commercial Use" (`LICENSE:20,41-44`).
  - "Internal corporate use is Commercial Use" (`:130`).
  - A commercial licence is needed if you "Are a startup at any stage, including pre-revenue and pre-product" (`:187`).
  - A "Trade Dress" clause covers "screen layouts, color palette, … dashboard widget vocabulary" (`:95-101`).
  - A contributor clause *assigns copyright* to Fincept (`:142-`).
- **`docs/COMMERCIAL_LICENSE.md` v3.0** ("In effect from August 11, 2026; supersedes v2.0, which is withdrawn in full", `:3-6`):
  - The repo "is licensed under … AGPL-3.0-or-later, **and under no other licence**" (`:13`).
  - "There is no paid exemption from these terms" (`:34`).
  - Its §3 now describes a separate closed-source *Enterprise* product, so the "Section 3" definition that `LICENSE:41-44` points to no longer exists.
- **`README.md:170-176`**: "AGPL-3.0-or-later … Fincept no longer sells a separate commercial or academic licence for this repository."

**Consequence for Fiboki.** Fiboki is run by a two-person operation that plausibly falls within `LICENSE:187`. The licensor asserts commercial-use restrictions in one file and disclaims them in another. AGPL §7 lets a recipient remove "further restrictions", but relying on that means relying on a legal argument against a licensor that has written it expects "the full range of remedies" (`LICENSE:42-44`). This is a risk to avoid, not one to manage.

### 4.3 Maintenance

- **Window 2026-07-01 → 2026-09-19** (HEAD, 9 days before this report): 73 commits.
  - `tilakpatel22` / "Tilak Patel": 50 (68%). `anan-1027`: 12. `github-actions`: 5. 7 others: 1 each.
  - By month: Jul 45, Aug 21, Sep 7, a falling trend.
- **Releases:** v4.3.0 (07-26), v4.4.0 (08-11), v4.4.1 (08-20), v4.5.0 (08-31).
- Commits are large squash-style updates ("version 4.4.0 update", "quantcept update"). A diff-stat churn figure is not meaningful because the shallow boundary counts the whole tree as added.

### 4.4 Dependency surface

- **Python:** `resources/requirements-numpy2.txt` has 116 non-comment requirement lines. They include `pandas>=2.3.3,<3.0` (**conflicts with Fiboki's 2.2.3**), `numpy>=2.2.3,<3.0`, `scipy>=1.13.0,<1.17.0`, `yfinance==0.2.66` and `akshare>=1.14.0`. `requirements-numpy1.txt` has 19 lines and pins `numpy<2.0` (it conflicts outright).
- **Build:** a Qt 6.8 C++ toolchain.
- **Conclusion:** it cannot share an environment with Fiboki. If run at all, it would have to be fully isolated.

### 4.5 Security review

| Item | Finding | Citation |
|---|---|---|
| `exec` of strategy code | `exec(compile(source, path, "exec"), module_globals)` in the live strategy runner; `exec(code, exec_globals)` in the backtesting.py provider. A path-traversal guard exists in `_loader.py`. | `scripts/strategies/live_runner.py:174`; `scripts/Analytics/backtesting/backtestingpy/backtestingpy_provider.py:971`; `scripts/strategies/_loader.py:5,32` |
| `exec` in tests | Dynamic `exec(f"from ..{module} import …")` | `scripts/agents/hedgeFundAgents/renaissance_technologies_hedge_fund_agent/tests/test_glm_model.py:190,323,…` |
| Pickle / subprocess | 7 Python files use `pickle`/`joblib`/`torch.load`; 22 use `subprocess`/`os.system`; no `shell=True` found | grep |
| Browser automation | The ForexFactory calendar is **scraped with Selenium/Chrome** | `scripts/economic_calendar.py:3-7,12-29` |
| Credential hygiene | The Python child environment has `*_API_KEY`, `*_SECRET`, `*_TOKEN` and similar variables stripped unless they are managed | `src/python/PythonRunner.cpp:175-193` |
| Telemetry | `CloudTelemetryProvider` posts only if the user sets `telemetry.cloud_endpoint` (opt-in) | `src/core/telemetry/CloudTelemetryProvider.cpp:149-164` |
| First-party endpoints | `api.fincept.in/v1`, `markets.fincept.in/api/v1`, `api.fincept.in/research/llm`; the macro calendar comes from the **Fincept backend with a session token** | grep; `services/economics/MacroCalendarService.cpp:19-22` |
| Order idempotency | The client order reference is a **fresh UUID per attempt**. The code says itself that this "deduplicates nothing across a *retry*". IBKR `cOID` uses it. | `src/trading/brokers/BrokerClientOrderId.h:1-24,37-40`; `ibkr/IBKRBroker.cpp:215` |
| Update channel | Signed `updates.json` (openssl signature workflow) | `services/updater/UpdateService.cpp:47-51`; `updates.json` |

### 4.6 Data connectors relevant to Fiboki (FX, gold, indices, macro, news)

All are standalone Python scripts in `fincept-qt/scripts/`. "Key" means the script reads that environment variable. "Terms risk" is my assessment (assumption A8).

**Official macro and central-bank sources (the only group of real interest):**

| Source | File (lines) | Host | Key? | Point-in-time handling |
|---|---|---|---|---|
| FRED | `fred_data.py` (376) | api.stlouisfed.org | `FRED_API_KEY` (free) | **No vintages.** The word `realtime` appears 0 times, so it returns revised history (look-ahead in backtests) |
| ECB SDW / SDMX | `ecb_data.py` (363), `ecb_sdmx_data.py` (224) | data-api.ecb.europa.eu | none | — |
| Bank of England IADB | `boe_data.py` (1,454) | bankofengland.co.uk | none | — |
| Bank of Japan | `boj_fetcher.py` (159) | stat-search.boj.or.jp (bs4) | none | — |
| Fed / NY Fed | `federal_reserve_data.py` (715) | markets.newyorkfed.org, federalreserve.gov | none | — |
| ONS | `ons_data.py` (123) | api.ons.gov.uk | none | — |
| BLS / BEA | `bls_data.py` (802), `bea_data.py` (911) | api.bls.gov, apps.bea.gov | `BLS_API_KEY` optional; `BEA_API_KEY` free | — |
| US Treasury fiscal data | `treasury_data.py` (150) | api.fiscaldata.treasury.gov | the script reads `TREASURY_API_KEY` (`:12`); the API itself is keyless (A8) | — |
| OECD / IMF / BIS (SDMX) | `oecd_data.py` (1,246), `imf_data.py` (632), `bis_data.py` (1,445) | sdmx.oecd.org, dataservices.imf.org, stats.bis.org | none | — |
| CFTC COT | `cftc_data.py` (726) | publicreporting.cftc.gov (Socrata) | `CFTC_APP_TOKEN` optional | Filters and orders by `Report_Date_as_YYYY_MM_DD` (`:262,273`), the Tuesday as-of date. **No release-lag stamping.** Using it as an availability time is about 3 days of look-ahead (A9). |
| ECB reference FX | `frankfurter_data.py` (182) | api.frankfurter.dev | none | — |

**FX, metals and index prices:**

| Source | File (lines) | Key | Notes |
|---|---|---|---|
| exchangerate / currency-api | `exchangerate_data.py` (188) | none | open.er-api.com, jsDelivr CDN |
| metals.dev | `metals_prices_data.py` (126) | `METALS_DEV_API_KEY` | |
| CME / COMEX | `comex_data.py` (100) | `CME_API_KEY` | |
| LME | `lme_data.py` (99) | `LME_API_KEY` | |
| Cboe VIX | `cboe_vix_data.py` (146) | reads `CBOE_API_KEY`; host cdn.cboe.com | |
| Yahoo | `yfinance_data.py` (1,495) | none | Unofficial; terms risk |
| Stooq | `stooq_data.py` (132) | reads `STOOQ_API_KEY` (`:14`) | |
| Commercial keyed | `twelve_data.py`, `alphavantage_data.py`, `finnhub_data.py`, `polygon_io_data.py`, `fmp_data.py`, `eodhd_data.py`, `tiingo_data.py`, `databento_*.py` | vendor keys | Thin request wrappers (84–894 lines) |

**Economic calendar** (Fiboki's documented gap, `marketstate/calendar.py:57`):

| Source | File | Method | Assessment |
|---|---|---|---|
| ForexFactory | `economic_calendar.py:12-29` | Selenium + headless Chrome scrape | Fragile; terms risk; not reproducible |
| TradingView | `tradingview_data.py:66-67` | Unofficial `economic-calendar.tradingview.com/events` | Undocumented endpoint; terms risk |
| Investing.com / Trading Economics | `investing_calendar_data.py`, `trading_economics_data.py` | `INVESTING_API_KEY`, `TRADING_ECONOMICS_API_KEY` | Keyed and commercial |
| Fincept backend | `MacroCalendarService.cpp:19-22` | `api.fincept.in/macro/upcoming-events` with a Fincept session | Account-bound; licence entanglement |

**News and sentiment:**
- `services/news/NewsService_Feeds.cpp` has 54 hard-coded RSS/Atom URLs (BBC, Guardian, NYT, DW, FXStreet, Investing.com, the Fed, the ECB, the BoE, CNBC, WSJ feeds and others).
- `fetch_company_news.py` uses the `gnews` package (`:10`).
- `news_nlp.py` does VADER plus a hand-tuned finance lexicon (`:348-395`).
- `NewsClusterService` and `NewsCorrelationService` are in C++.
- Assessment: headline-level, lexicon sentiment, with no timestamp-of-first-availability discipline. Not research-grade.

### 4.7 Analytics worth porting

`scripts/Analytics/` is almost entirely **wrappers** around third-party libraries: quantstats, PyPortfolioOpt, skfolio, riskfolio, statsmodels, gs_quant, py_vollib, pmdarima, gluonts, functime, tsmoothie, vnpy and backtesting.py (directory listing). Fiboki's `stats/` (DSR, PBO, SPA, CPCV, bootstrap, stress) already exceeds what these wrappers expose for strategy validation. **Nothing to port.**

The IBKR adapter's handling of Client Portal "reply" confirmation prompts is a useful *fact* for Fiboki's IBKR fallback: a `message` element means the order was **not** transmitted until you POST to `/iserver/reply/{id}` (`ibkr/IBKRBroker.cpp:251-263`). Read it as documentation. Do not copy it, both for licence reasons and because it is C++.

### 4.8 Should any of it run as a sidecar for Fiboki's market-intelligence layer?

**No.** Four reasons:
1. The licence overlay (§4.2) asserts that internal startup use needs a commercial licence, which the README says is no longer sold.
2. Its connectors are not point-in-time (FRED without vintages, COT without release lag) and would silently inject look-ahead into any research use. That directly contradicts `DATA_ARCHITECTURE.md`'s "providers declare what they are" contract.
3. The dependency environment is incompatible, and the product is a desktop GUI, not a service.
4. The calendar sources it offers are either scraped or account-bound.

**Instead**, write thin first-party providers under `src/fiboki/data/providers/` (or `marketstate/`) for the official, free sources, each declaring its point-in-time semantics:

| Provider | Point-in-time requirement | Effort |
|---|---|---|
| FRED **ALFRED** (`realtime_start`/`realtime_end` vintages) | Vintage-aware | 1.5 d |
| CFTC COT (Socrata) | Stamp `available_at` = the official Friday release time, not the Tuesday as-of date | 1 d |
| ECB SDMX, BoE IADB, ONS | Revision flags where published | 1 d each |
| NY Fed markets API (SOFR, repo) | — | 0.5 d |

That is about 6 d in total, all Fiboki's own code and Fiboki's own pins. The dated economic-calendar feed stays a **user decision on a licensed source** (for example a paid Trading Economics key), followed by about 2 d of integration into the existing `InMemoryEconomicCalendar` loader.

**Verdict: REJECT** (code and sidecar).

---

## 5. Licence compatibility matrix

Fiboki is a private repository run by two operators (Joe, Tom) on one machine (`ARCHITECTURE.md` §1, §11). "Conveying" below means giving a copy to anyone outside the legal person that holds the copy.

| | freqtrade (GPL-3.0) | backtrader (GPL-3.0-or-later) | FinceptTerminal (AGPL-3.0 + contested overlay) |
|---|---|---|---|
| Copy code into a private repo for internal use only? | Permitted. GPL §2 lets you "make, run and propagate covered works that you do not convey" without source obligations. | Same | AGPL permits the same **except** §13 (below). The `LICENSE` overlay *claims* internal and startup use needs a commercial licence (`LICENSE:130,187`). |
| Does running Fiboki's web UI for Tom trigger obligations? | No. GPL has no network clause. | No | **Yes.** AGPL §13: users interacting with a *modified* version over a network must be offered the corresponding source of the **whole combined work**. |
| Does giving Tom (or a future partner, investor or customer) a copy trigger obligations? | Yes. It is conveying, so the whole combined work must be offered under GPL-3.0 with source (§§5–6). Tom would receive GPL rights to all of Fiboki. | Same | Same, plus the overlay and trademark / trade-dress claims |
| Does a future commercial sale or licensing of Fiboki work? | Only if Fiboki is GPL-licensed as a whole. **Copying freqtrade code effectively forecloses proprietary licensing.** | Same | Foreclosed, and legally contested |
| Attribution | Keep copyright notices and the licence text; mark modified files (§5a). | Same | Same, plus "retain all copyright notices … and this license file" (`LICENSE:118-120`) |
| Re-implementing *patterns* (ideas, algorithms) from reading | Ideas are not copyrightable. Re-express from a written spec, not side by side. | Same | Same for ideas. The trade-dress clause (`LICENSE:95-101`) asserts rights over layouts and "dashboard widget vocabulary". Avoid imitating its UI. |
| Running it unmodified as a separate process (sidecar) | Permitted | Permitted | AGPL permits it; the overlay claims internal use needs a paid licence. **Avoid.** |
| **Recommended posture** | **PORT-PATTERN only. No verbatim copy.** | Nothing to take | **No code, no sidecar, no UI imitation** |

A practical rule for the team: **no file in `src/fiboki/` may contain code transcribed from these repositories.** Where a pattern is ported, the Fiboki docstring should say "pattern after freqtrade `<file>` (GPL-3.0, not copied)", so provenance is visible to a future reviewer. That note is courtesy and provenance only, not a licence obligation.

---

## 6. What NOT to take

1. freqtrade's **order-submission error mapping** (`exchange/exchange.py:1566-1569`) together with the **catch-and-continue** in `enter_positions` (`freqtradebot.py:844`). It turns an unknown outcome into "try again later".
2. freqtrade's **absence of a client order ID** and its **persist-after-dispatch** ordering (`freqtradebot.py:1131` vs `:1235-1236`).
3. freqtrade's **`startup_update_open_orders` 5-day branch** (`freqtradebot.py:604`), whose condition is the wrong way round.
4. freqtrade's **callbacks that let strategies size or lever** (`custom_stake_amount`, `adjust_trade_position`, `leverage`), and **live intrabar callbacks** evaluated against a ticker rate (`freqtradebot.py:1555`).
5. freqtrade's **Sharpe and hyperopt losses** (`data/metrics.py:455-475`; `hyperopt_loss_sharpe_daily.py:38-39`) and its **undeflated best-epoch selection**.
6. **FreqAI's** split without purge or embargo, unseeded shuffles, and **cloudpickle / `torch.load(weights_only=False)`** model persistence.
7. freqtrade's **forward-fill of missing candles** (`data/converter/converter.py:126-160`). For FX it fabricates weekend bars.
8. freqtrade's **wall-clock protections** and **protections that are off by default in backtest**.
9. freqtrade's **local-time scheduler** (`freqtradebot.py:193`) and **synchronous webhook in the trading thread** (`rpc/webhook.py:115-140`).
10. freqtrade's **user-code resolver**, including base64 strategies (`resolvers/strategy_resolver.py:281-289`).
11. backtrader's **OANDA and IB stores** (unimportable, and "exception = rejection, then thread exits": `stores/oandastore.py:508-514`), its **cheat-on-open/close** switches, and `btrun`'s **`eval`**.
12. **Anything from FinceptTerminal**: code, UI layout or "widget vocabulary", scraped calendars, non-vintage FRED, COT without release lag, and its per-attempt random client order ID (`BrokerClientOrderId.h:17-24`).
13. **Any of these packages as a dependency.** They conflict with Fiboki's exact pins on numpy, pandas, scipy, pyarrow, pydantic, fastapi and more (§2.3, §4.4).

---

## 7. Verdicts

| Repo | Verdict | One-line reason |
|---|---|---|
| freqtrade | **PORT-PATTERNS** | Mature operational glue for a polling live bot. The execution core is weaker than Fiboki's; GPL-3.0 and incompatible pins rule out code reuse. |
| backtrader | **REFERENCE-ONLY** (practically REJECT) | Frozen since 2023; broken live stores; nothing better than Fiboki's `sim/`. |
| FinceptTerminal | **REJECT** | Contradictory AGPL-plus-commercial overlay; a desktop C++ product; connectors lack point-in-time discipline. |

### Recommended Fiboki backlog from this review (ranked by risk reduction)

| # | Item | Pattern source | Fiboki location | Days |
|---|---|---|---|---|
| 1 | `OandaPollingBarFeed`: boundary-aligned wake; `complete` flag authoritative; late-candle grace re-poll; session-aware, never gap-fills; data age into `RiskContextBuilder` | freqtrade `worker.py:170-177`, `exchange/exchange.py:2908-2975,3087-3133` | `workers/runtime.py` | 3 |
| 2 | Startup reconcile before the first cycle, plus a periodic reconcile timer | freqtrade `freqtradebot.py:264-282` (ordering) | `workers/live_worker.py` / `workers/base.py` | 0.5 |
| 3 | Idempotent-read retry decorator, plus an AST test forbidding it on order paths | freqtrade `exchange/common.py:113-200` (reads only) | `broker/oanda.py`, `data/providers/oanda.py`, `tests/unit/` | 1 |
| 4 | Bar-indexed cooldown and stop-streak locks, enforced in engine, paper and gateway, with a parity test | freqtrade `plugins/protections/*` | `strategy/dsl.py`, `backtest/engine.py`, `risk/gateway.py` | 3 |
| 5 | `FIBOKI_*` env registry: strict booleans, unknown-variable detection, per-role required sets | freqtrade `configuration/config_validation.py:46-99` | `api/settings.py` | 1 |
| 6 | Inbound Telegram ops process: read-only plus `/killswitch_arm`; mandatory user allow-list; audited | freqtrade `rpc/telegram.py` (surface), `rpc/rpc.py` (service split) | new `obs/ops_bot.py` + `api/audit_trail.py` | 3 |
| 7 | `log_once` and cycle-time budget warning | freqtrade `mixins/logging_mixin.py:25`; `freqtradebot.py:206-214` | `obs/logging.py`, `obs/metrics.py` | 0.5 |
| 8 | (Optional) adapter capability declaration | freqtrade `exchange/exchange.py:131-170` | `broker/base.py` | 0.5 |
| — | (Separate, not from these repos' code) point-in-time macro providers: ALFRED, COT with release lag, ECB, BoE, ONS, NY Fed | FinceptTerminal only as a list of sources | `data/providers/` | ≈6 |

Items 1–7 total ≈ **12 engineer-days**; with item 8, ≈ 12.5.

**Old data:** none of this affects stored research results. Item 4 changes strategy behaviour, so any strategy document that opts into locks gets a new content hash and must be re-validated.

---

## 8. Cross-cutting observation for Fiboki

freqtrade shows by contrast why Fiboki's execution-layer decisions matter. freqtrade has none of: an idempotency key, write-ahead intent, an UNKNOWN state, or parity between the dry-run and backtest fill models. It compensates with a large community and fast bug-fixing (777 commits in 90 days). Fiboki cannot rely on that kind of volume, so it should keep its structural controls and borrow only freqtrade's *liveness* machinery: feed cadence, read retries, locks, ops UX and config hygiene.

---

## 9. Facts vs assumptions

### Verified facts (from code or git in this session)

- All `file:line` citations above were read in this session at the stated HEADs.
- freqtrade 90-day statistics: 777 commits; 13 authors; Matthias 532. Releases 2026.4–2026.8 with the dates stated (`git log`, `git tag`).
- The freqtrade candle-finality code landed 2026-09-22 (`git log -S`).
- ccxt 4.5.84 contains 104 exchange modules and no OANDA, IG or IBKR (wheel listing; Appendix A, probe 3).
- backtrader: last commit 2023-04-19; 0 commits since; the Python 3.11 `collections.Iterable` crash was reproduced (probe 1).
- backtrader's `OandaStore` is absent at import time, and the PyPI `oandapy` 0.0.9 fails to import (probe 2).
- FinceptTerminal: the contradictory licence texts at the same commit; LOC counts; 73 commits since 2026-07-01; tags v4.3.0–v4.5.0.
- Fiboki: every "Fiboki has / lacks" claim in §1 was checked by grep or read in `src/fiboki/`.

### Assumptions (not verified from code here)

- **A1.** Fiboki is operated as, or on behalf of, a for-profit venture. This affects §4.2 and §5. Not stated in the repo docs I read; the project instructions describe "serious monitored use" by Joe and Tom.
- **A2.** Joe and Tom are either one legal person or two separate recipients. This decides whether sharing a copy is "conveying" under the GPL. Needs a founder or legal answer.
- **A3.** ccxt's `NetworkError` / `RequestTimeout` inherit from `OperationFailed`, so they are mapped to `TemporaryError` at `exchange/exchange.py:1566`. Taken from knowledge of ccxt's exception hierarchy; I did not read ccxt's `base/errors.py` in this session.
- **A4.** OANDA market orders default to FOK/IOC, so Fiboki's "partial = terminal FILLED" is safe. External knowledge of the v20 API, consistent with `EXECUTION_ARCHITECTURE.md`'s market-only scope. Not tested against a venue.
- **A5.** backtrader's `oandapy` is the retired OANDA v1 REST client from GitHub (`oanda/oandapy`) and the v1 API is no longer served. Inferred from the v1-style calls (`get_history`, `create_order(account, …)`, `X-Accept-Datetime-Format` header, `oandastore.py:229-232,376`) and external knowledge. Not tested against OANDA.
- **A6.** IbPy is unmaintained. External knowledge; not verified here.
- **A7.** GPL/AGPL interpretation in §5 is a technical reading of the licence texts, **not legal advice**.
- **A8.** Terms-of-service risks for Yahoo, ForexFactory, TradingView and Investing.com scraping; and "US Treasury fiscal data API is keyless". External knowledge; I did not read their terms in this session.
- **A9.** CFTC COT reports are as-of Tuesday and released on Friday afternoons (US Eastern). External knowledge; the absence of lag handling in `cftc_data.py` is verified.
- **A10.** Engineer-day estimates assume one engineer familiar with Fiboki's codebase, including tests. They are order-of-magnitude figures, not quotes.
- **A11.** freqtrade's test suite passes at HEAD. Not run; I did not execute freqtrade.

---

## Appendix A — probes run

1. **backtrader on Python 3.11.15 (numpy 2.4.4, pandas 3.0.2 in this sandbox):**
   - A simple `Cerebro` + `SMA` + `SharpeRatio`/`DrawDown` run succeeds.
   - An indicator calling `bindlines(owner=0)` gives `AttributeError: module 'collections' has no attribute 'Iterable'` (`lineiterator.py:229`).
2. **backtrader OANDA store:**
   - `import backtrader; hasattr(bt.stores,'OandaStore')` → False; the same for IBStore.
   - `pip download oandapy` gets 0.0.9, which exports only `APIv20` (`oandapy/__init__.py`).
   - Importing `backtrader.stores.oandastore` with it on the path → `ModuleNotFoundError: No module named 'status'`.
   - Temporary files were removed.
3. **ccxt 4.5.84 wheel:** 104 top-level exchange modules; listing grepped for oanda, ig, ibkr, interactive, saxo, fxcm, capital, cmc and plus500 with no match; the only non-crypto broker is `alpaca`. Wheel deleted afterwards.
4. **freqtrade retried methods** (`grep -n "@retrier" -A1 exchange/exchange.py`): `create_stoploss` (retries=0), `fetch_order`, `cancel_order`, `get_balances`, `fetch_positions`, `_fetch_orders`, `fetch_trading_fees`, `fetch_bids_asks`, `get_tickers`, `fetch_ticker`, `fetch_funding_rate`, `fetch_l2_order_book`, `get_trades_for_order`, `get_fee`, `_async_get_candle_history`, `_async_fetch_trades`, `_get_funding_fees_from_exchange`, `get_leverage_tiers`, `get_market_leverage_tiers`, `_set_leverage`, `set_margin_mode`. **`create_order` is absent.**
5. **Clone deepening:** `git fetch --shallow-since=2026-06-29` (freqtrade: 152 → 779 commits visible; FinceptTerminal: window from 2026-07-01), and `git fetch --unshallow` (backtrader: 69 → 2,404 commits).
