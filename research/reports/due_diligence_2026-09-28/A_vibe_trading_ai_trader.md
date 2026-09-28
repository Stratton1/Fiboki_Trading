# Code-level due diligence: HKUDS/Vibe-Trading and HKUDS/AI-Trader for Fiboki V2

**Auditor stance:** principal quant systems auditor. Everything below was read from the cloned trees on 2026-09-28. Paths prefixed `VT/` are relative to `/home/claude/research/repos/Vibe-Trading/`, `AT/` to `/home/claude/research/repos/AI-Trader/`, and `FB/` to `/home/claude/v2/`. Line numbers are from those checkouts. README text is quoted only as *claims*, never as evidence.

**What was not done in this session:** neither repo's test suite was run (Vibe-Trading needs ~188 locked packages, including langchain, weasyprint and system libraries; installing them into Fiboki's environment would itself violate Fiboki's pins). No network calls were made to GitHub, so open-issue counts and release pages are **not verified**. Both clones are **shallow** (`git rev-parse --is-shallow-repository` → `true` for both), so contributor counts and cadence cover only the visible window stated below.

**Fiboki context used (read in this session):** `FB/src/fiboki/agents/__init__.py` (cardinal rule, five mechanisms), `FB/docs/v2/AI_AGENT_ARCHITECTURE.md`, `FB/docs/v2/ARCHITECTURE.md`, `FB/AGENTS.md`, symbol outlines of `agents/{capabilities,roles,tools,orchestrator,providers,workflows,audit}.py`, `strategy/{dsl,compiler,primitives,registry}.py`, `marketstate/*.py`, `risk/gateway.py`, and `FB/pyproject.toml` (numpy 2.2.6, pandas 2.2.3, scipy 1.14.1, pydantic 2.10.4, fastapi 0.115.6, click 8.1.8 pinned).

---

## Executive summary

| | Vibe-Trading | AI-Trader |
|---|---|---|
| What it is (from code) | A very large single-user LLM "finance research agent" (ReAct loop, ~100 tools, LangChain function-calling) with backtest engines, 18 broker connectors and an **autonomous live-trading runner in which the LLM is the trader** | A hosted social "signal/copy-trading" web service (FastAPI + React) for *external* agents; paper fills in its own DB; **no LLM agent and no broker adapter in the repo** |
| Licence | MIT (`VT/LICENSE`), plus Apache-2.0 Qlib material (`VT/NOTICE`) | **No LICENSE file** at HEAD; README badge says MIT (`AT/README.md:11`) but links to a file that does not exist |
| Does the LLM have order authority? | **Yes.** LLM chooses symbol, side, size, order type and paper/live profile; live is bounded by a deterministic notional/exposure/count "mandate" gate; paper is ungated | In-repo: an external agent's REST call directly mutates simulated cash/positions and is copied 1:1 to followers; no risk engine |
| FX/CFD/spread-bet brokers | No OANDA, no IG, no spread-bet. MT5 (Windows-only terminal bridge) and eToro are the only FX/CFD-capable paths | None. Markets are `us-stock`, `crypto`, `polymarket` only (`AT/service/server/routes_shared.py:63`) despite README claiming forex/options/futures |
| Performance evidence | None of trading performance in repo. Engineering evidence (tests, fail-closed design) is substantial | None of trading performance. "Research" is a behavioural A/B study of agents on the platform |
| Verdict | **PORT-PATTERNS** (a small number, carefully) | **REFERENCE-ONLY** (bordering REJECT) |

For Joe's stated goal — "agents running this, searching markets for entries, scanning news, auto trading" — neither repo supplies anything that should be imported. Vibe-Trading's live design is the exact architecture Fiboki's cardinal rule forbids (the LLM decides and sizes; code only caps). What is worth taking is **infrastructure around agents**: ledger hardening, run manifests, a numeric-grounding gate, scheduled playbooks with a machine-parsed verdict, an offline eval harness, and (from AI-Trader, as an idea only) a pre-registered forecast record plus a snapshot-based news pipeline.

---

# Part 1 — HKUDS/Vibe-Trading (HEAD `18988fb`, 2026-09-28 23:49 +0800)

## 1. What it actually is

A Python 3.11+ monorepo whose core is `VT/agent/src/agent/loop.py` (3,543 lines), a ReAct agent loop that binds every registered tool to an LLM via LangChain `bind_tools` (`VT/agent/src/providers/chat.py:465`, `:504`) and executes the returned tool calls (`loop.py:1553`, `:1569`, `:2165`). Tools are auto-discovered as `BaseTool` subclasses from `VT/agent/src/tools/` (`VT/agent/src/tools/__init__.py:34-70`). Entry points: the `vibe-trading` CLI (`pyproject.toml` `[project.scripts]` → `cli:main`; `VT/agent/cli/`, 43 files), a FastAPI server (`VT/agent/api_server.py:163`), an MCP server (`VT/agent/mcp_server.py`, 3,097 lines), a React 19 + Vite 8 web UI (`VT/frontend/package.json`) and an Electron 43 desktop shell (`VT/desktop/electron/package.json`). Runtime model: a long-lived local process per user; sessions persisted under `~/.vibe-trading/` (e.g. `VT/agent/src/memory/persistent.py:23`); optional multi-agent "swarms" from YAML presets (`VT/agent/src/swarm/presets/*.yaml`, 30 files); optional scheduled research; and an **autonomous live runner** started from the API (`VT/agent/src/api/live_routes.py:708`) that invokes the agent on a market schedule (`VT/agent/src/live/runtime/runner.py:1-33`). Backtests execute **LLM-generated Python** (`signal_engine.py`) in a subprocess after an AST denylist scan (`VT/agent/backtest/runner.py:216-227`, `:324-400`; `VT/SECURITY.md` "Generated backtest code").

**Size** (non-blank lines, custom counter over the working tree excluding `.git`/`node_modules` — `cloc` was not installed):

| Language | Files | Non-blank lines |
|---|---:|---:|
| Python (total) | 1,827 | 369,297 |
| — `agent/src` | 945 | 172,518 |
| — `agent/tests` | 735 | 153,125 (711 `test_*.py`) |
| — `agent/backtest` | 85 | 25,108 |
| — `agent/cli` | 43 | 12,793 |
| — `agent/evals` | 9 | 1,408 |
| TSX | 119 | 25,822 |
| TypeScript | 65 | 9,520 |
| JavaScript | 21 | 2,325 |
| Markdown | 419 | 53,540 (README alone 344 KB, seven translations) |

## 2. Licence, maintenance, dependencies, security

### Licence
- `VT/LICENSE`: MIT, "Copyright (c) 2026 Vibe-Trading Contributors". Obligation: retain copyright + permission notice in copies/substantial portions.
- `VT/NOTICE`: bundles **Microsoft Qlib feature definitions under Apache-2.0** (`agent/src/factors/zoo/qlib158/NOTICE`, `LICENSE.md`) — Apache-2.0 §4 requires carrying NOTICE and stating changes if that directory were copied. Also SIL OFL 1.1 fonts in `frontend/public/fonts/`. It states alpha formulas (Kakushadze 101, GTJA 191, Fama-French) are reimplemented from papers.
- No copyleft. Porting *ideas* carries no obligation; copying any file carries MIT attribution (and Apache NOTICE for `qlib158`).

### Maintenance signals (visible window only)
- Shallow clone; earliest visible commit 2026-08-29 12:59 +0800. **440 commits** visible, 438 of them in September 2026, **17 distinct author names** (top: "Haozhe Wu" 250, "Lanre Shittu" 44, "shadowinlife" 37).
- Releases per `VT/CHANGELOG.md` headings: 0.1.8 (2026-05-17), 0.1.9 (06-01), 0.1.10 (06-19), 0.1.11 (07-11), 0.1.12 (07-22), 0.1.13 (08-10), 0.1.14 (08-20), 0.1.15 (09-09) — **five releases inside the last 90 days**, plus a large `[Unreleased]` section. `git tag` shows only `v0.1.15` locally.
- Highest issue/PR number referenced in the changelog is `#1577`. Open-issue volume is **not visible from the repo and was not checked**.
- CI: `VT/.github/workflows/{test,docker-build,loader-health,desktop-windows,wiki,wiki-deploy}.yml`; Dependabot configured.
- Signal: extremely high churn, many external contributors, very large surface. Code quality in the parts read is careful (fail-closed comments citing issue numbers everywhere), but the velocity means any vendored file goes stale within weeks.

### Dependency surface
`VT/pyproject.toml` base dependencies (≈50) are **lower-bound only** (`numpy>=1.24.0`, `pandas>=2.0.0,<3.0.0`, `scipy>=1.10.0`), with heavy items: `langchain>=1.3.9`, `langgraph>=1.2.5,<1.3`, `langchain-openai`, `fastmcp>=2.14,<4`, `mcp<1.30`, `ccxt`, `akshare`, `tushare`, `yfinance`, `duckdb`, `scikit-learn`, `weasyprint` (needs system Pango/Cairo), `matplotlib`, `python-pptx`, `pypdfium2`, `Pillow`, `ddgs`, `cryptography`. Optional extras add 14 broker/chat SDKs.

The hashed lock `VT/requirements-lock.txt` (uv, 188 pinned packages) **conflicts directly with Fiboki's exact pins**:

| Package | Vibe-Trading lock | Fiboki pin | Consequence |
|---|---|---|---|
| numpy | 2.4.6 | 2.2.6 | Different numerics; Fiboki treats drift as an error |
| pandas | 2.3.3 | 2.2.3 | Same |
| scipy | 1.17.1 | 1.14.1 | Same |
| pydantic | 2.13.5 | 2.10.4 | Schema behaviour drift in Fiboki's `extra="forbid"` models |
| fastapi | 0.141.1 | 0.115.6 | API layer drift |
| **click** | **8.5.0** | **8.1.8** | `FB/pyproject.toml` records that click 8.5.0 makes every `fiboki --help` raise with typer 0.15.1 |
| rich / orjson / uvicorn | 15.0.0 / 3.11.9 / 0.52.4 | 13.9.4 / 3.10.13 / 0.34.0 | Drift |

Importing Vibe-Trading code into the Fiboki process is therefore not viable; a sidecar would be the only import path, and nothing below justifies one.

### Security observations
1. **Unrestricted host shell for the agent.** `BashTool` runs `subprocess.Popen(command, shell=True, ...)` (`VT/agent/src/tools/bash_tool.py:129-139`) with only a guard against killing Python processes by name (`VT/agent/src/tools/_shell_safety.py:37-58`). It is excluded by default (`VT/agent/src/tools/__init__.py:31`, `:156`), but the interactive CLI agent and CLI swarm runs enable it (`VT/agent/cli/_legacy.py:1251` in `_run_agent`, `:2400`, `:2452`), and the API enables it for every request when the host sets the env opt-in (`VT/agent/src/api/sessions_routes.py:743` → `VT/agent/src/api/security.py:561-563`, which simply returns `_env_shell_tools_enabled()`; env `VIBE_TRADING_ENABLE_SHELL_TOOLS`, `VT/agent/src/config/env_schema.py:352`). The FX desk preset gives its agents `bash` (`VT/agent/src/swarm/presets/macro_rates_fx_desk.yaml`, `tools: [bash, read_file, write_file, ...]`).
2. **The "structural" mandate guarantee does not survive a shell-enabled agent.** `commit.py` states the mandate writer is unreachable from the tool registry, "a structural guarantee, not a prompt-level one" (`VT/agent/src/live/mandate/commit.py:1-12`). That holds for `write_file`, which is confined to upload/run roots (`VT/agent/src/tools/path_utils.py:150-180`). It does **not** hold for `bash`, which runs as the same OS user that owns `<runtime_root>/live/<broker>/mandate.json` and the halt flag (`VT/agent/src/live/paths.py:23-30`); I found no path policy in `bash_tool.py` or `src/security/` protecting that directory. A shell-enabled session could, in principle, write a mandate or delete a halt flag. (Inference from code; not exploited.)
3. **LLM-generated Python is executed.** The backtest runner imports the model-written `signal_engine.py` (`VT/agent/backtest/runner.py:216-227`) after an AST denylist of network/process/dynamic-import modules and the `src.trading`/`src.live` prefixes (`:356-400`). The file's own comment is candid: "This is defense-in-depth, not a kernel-level guarantee: an AST denylist cannot be complete", listing measured residuals (`inspect`, `operator`, `threading`, `codeop`, `tempfile`, `os.makedirs`) (`:343-351`). The subprocess remains network-capable (`VT/SECURITY.md`).
4. **HMAC-guarded `pickle.loads`** for a local cache (`VT/agent/src/tools/alpha_bench_tool.py:217-249`) — acceptable as written, noted for completeness.
5. **Third-party proxying of URLs.** `web_reader_tool.py` fetches pages through `https://r.jina.ai/` (`VT/agent/src/tools/web_reader_tool.py:18`, warning at `:65`): every URL the agent reads is disclosed to Jina.
6. **Network surface.** ~60 distinct hosts are hard-coded under `agent/src` and `agent/backtest` (Eastmoney, Sina, Tencent, Yahoo, SEC, OKX, Binance, Polymarket, Finnhub, Tiingo, Alpha Vantage, `qveris.ai`, `api.gildata.com`, `chatgpt.com` for Codex OAuth, and read-only Iranian exchanges `apiv2.nobitex.ir`, `api.wallex.ir`). The last two may raise sanctions questions for a UK operator — flagged, not assessed.
7. **No telemetry found** (no posthog/sentry/analytics calls in `agent/` or `frontend/src`).
8. **Tool arguments are not schema-validated centrally.** `ToolRegistry.execute` calls `tool.execute(**params)` directly (`VT/agent/src/agent/tools.py:143`); JSON Schema in `parameters` is only advertised to the model. Each tool coerces its own inputs (e.g. `_num_or_none` in `trading_connector_tool.py`). Contrast Fiboki's `AgentSession.call`, which validates inputs and outputs against pydantic schemas.
9. **Model-suppliable credentials and endpoints.** eToro tools accept `api_key`/`user_key` as tool arguments (`VT/agent/src/tools/trading_connector_tool.py:226-233`, `:196`), and all trading tools accept `host`/`port`/`client_id` overrides (`:147-177`, `:213-223`).

## 3. Architecture walk-through

**Agent loop.** `AgentLoop` (`loop.py:1078`) runs up to `max_iterations` (default 50, `:1094`). Per turn it streams a completion with the full tool list bound (`:1553-1569`), then `_process_tool_calls` (`:2401-2656`): handles a `compact` pseudo-tool, blocks identical calls that already failed repeatedly (`:2457-2475`), asks the grounding ledger to authorise the call (`:2476-2494`), replays read-only results lost to compaction from a bounded cache (`:2496-2548`, cap `MAX_READONLY_REPLAY_RECOVERIES = 6`, `:88`), refuses duplicate successful non-repeatable calls (`:2550-2572`), and serves deterministic tools from cache (`:2574-2605`). Execution batches consecutive read-only tools in parallel threads and runs writes serially (`_batch_execute`, `:2714-2761`). Read-only tools get a hard timeout; write tools are never killed, only warned (`_invoke_tool`, `:2948-2978`). Context management is five-layered (`:1-9`): microcompact, text collapse, LLM summary with a protected tail (`TAIL_TOKEN_BUDGET = 20_000`, `:76`), model-invoked compaction, iterative summary update.

**Tool design.** `BaseTool` carries `name`, `description`, JSON-Schema `parameters`, and flags `repeatable`, `is_readonly`, `deterministic`, `replay_after_compaction` (`VT/agent/src/agent/tools.py:13-66`). ~80 tool modules in `VT/agent/src/tools/`. MCP servers configured by the operator are appended; live-broker MCP servers are hidden and routed through `trading_*` tools (`VT/agent/src/tools/__init__.py:205-260`). Swarm workers receive a per-preset whitelist (`build_swarm_registry`, `:300-345`).

**Memory/state.** File-based cross-session memory at `~/.vibe-trading/memory` (`VT/agent/src/memory/persistent.py:23`) with FTS index, lifecycle, compression, semantic links (`VT/agent/src/memory/*.py`). Session store (`src/session/`), goal store (`src/goal/store.py`), hypothesis registry (`src/hypotheses/registry.py`), run traces (`src/agent/trace.py`), and a hash-chained governance ledger (`src/governance/ledger.py`).

**Market data.** Loader registry with per-market fallback chains (`VT/agent/backtest/loaders/registry.py`); forex chain is `["mt5", "akshare", "yfinance", "local"]` (`:243`). News is equity-centric: Eastmoney (China) and Yahoo (US/HK) headlines (`VT/agent/src/tools/stock_news_tool.py:1-40`); web search via `ddgs` (`web_search_tool.py`). No FX-specific news or economic-calendar source was found.

**Signals → orders.** Two routes. (a) Interactive: the model calls `trading_place_order` (§4). (b) Autonomous: `LiveRunner.run_once` checks halt → mandate expiry → reconciliation, then invokes the agent with a prompt that pins the mandate text and says "Trade freely INSIDE the limits below … Assess the current opportunity set, then act within the mandate" (`VT/agent/src/live/runtime/runner.py:218-263`). The runner is constructed inside the API process (`VT/agent/src/api/live_routes.py:690-721`) with a single `Trigger.market("us_equity")`.

**Broker/exchange integrations** (`VT/agent/src/trading/connectors/`): Alpaca, Binance, Dhan, eToro, Futu, IBKR (local + official MCP), KIS, Longbridge, MT5, OKX, Robinhood (MCP), Scalable, Shoonya, Tiger, Toss, Trading212, Upbit, Zerodha. Asset classes in the mandate model: US/HK/CN/IN equity, US ETF, crypto, forex, plus `CFD` as an instrument type (`VT/agent/src/live/mandate/model.py:17-43`). **FX/CFD:** MT5 via the Windows-only `MetaTrader5` package (extra `mt5`, `sys_platform == 'win32'`), classified FOREX vs CFD by symbol (`VT/agent/src/trading/service.py:675-680`); eToro CFDs. **No OANDA, no IG, no spread-bet semantics anywhere** (`grep -ri oanda` returns nothing in `agent/`; the only "IG" hits are in MCP OAuth code for IBKR).

**Backtest/eval harness.** `BaseEngine` is bar-by-bar with signals shifted one bar and filled at the next bar's open (`VT/agent/backtest/engines/base.py:244-248`, `:624-632`). Market engines for China A, China futures, crypto, forex, global equity/futures, India, Korea, Vietnam, options. The forex engine uses a static spread table (`VT/agent/backtest/engines/forex.py:24-41`, e.g. `"XAU/USD": 3.2` pips with a Dukascopy-median comment), 0.3 pip default slippage (`:106`), swap on, and **default leverage 100:1** (`:101`). Statistical validation offers a trade-order permutation test, bootstrap Sharpe CI and walk-forward (`VT/agent/backtest/validation.py:1-10`). DSR and CSCV/PBO exist as library functions (`VT/agent/src/quantlib/multipletesting.py`) and are used by the factor bench and an LLM-callable `quantlib_call` tool, **not** by the backtest or strategy-discovery path (only `src/factors/bench_runner.py` and `src/tools/quantlib_tool.py` import them). A separate offline **agent-run eval harness** checks persisted run artefacts and emits PASS / FAIL / NOT_EVALUABLE / INVALID_ARTIFACT (`VT/agent/evals/harness/README.md`); one case exists (`cases/identity/explicit_a_share.json`).

**LLM output parsing.** Native function calling through LangChain (`chat.py:465`, `:504`), with a fallback parser for DeepSeek's "DSML" tool-call text (`chat.py:244-321`). Free-text final answers pass through a "grounding" release gate: the model must declare every prose number in a fenced `figures` block with a role (`observed`, `derived`, `proposed`, `cited`, `count`) and the gate matches prose numbers to tool evidence within a 0.5% rounding band (`VT/agent/src/agent/grounding/figures.py:1-33`; `release.py`, `policies.py`, 6,031 lines in the package). Scheduled research playbooks end in a strictly regex-parsed `## Verdict` tail; malformed output becomes `contract_violation` rather than being guessed (`VT/agent/src/scheduled_research/verdict.py:1-30`). Swarm inter-agent hand-off is free text (`{upstream_context}` in preset prompts).

## 4. Does the LLM have order authority? — **Yes**

### Exact path, interactive and autonomous

1. The LLM receives `trading_place_order` in its bound tool list (auto-discovered; no `check_available` override). Its schema lets the model choose everything that matters (`VT/agent/src/tools/trading_connector_tool.py:739-765`):

```python
name = "trading_place_order"
...
"symbol": {"type": "string", ...},
"side": {"type": "string", "enum": ["buy", "sell"]},
"quantity": {"type": "number", "description": "Order size in units/shares/contracts. ..."},
"notional": {"type": "number", "description": "Order size as an account-currency amount. ..."},
"order_type": {"type": "string", "enum": ["market", "limit"], "default": "market"},
```

   plus the common `connection` parameter: *"Trading connector profile id, e.g. ibkr-paper-local or robinhood-live-mcp. Defaults to the selected profile."* (`:148-151`). **The model selects paper vs live per call**, or persistently via `trading_select_connection` (`:268-293`).
2. `AgentLoop._process_tool_calls` → `_execute_single` → `_invoke_tool` → `self.registry.execute(tool_name, args)` (`loop.py:2855`, `:2974`) → `tool.execute(**params)` (`VT/agent/src/agent/tools.py:143`).
3. `TradingPlaceOrderTool.execute` converts numerics and calls the service (`trading_connector_tool.py:789-801`):

```python
return _json_result(
    place_order(
        str(kwargs["symbol"]),
        _connection(kwargs.get("connection")),
        side=str(kwargs.get("side") or ""),
        quantity=quantity,
        notional=notional,
        ...
```

4. `place_order` (`VT/agent/src/trading/service.py:697-761`). **Paper is ungated**:

```python
if profile.environment == "paper":
    return _with_profile(profile, module.place_order(config, **place_kwargs))
```

   Live builds an `OrderIntent` — note `notional_usd=float(notional)` taken straight from the model's account-currency figure (`:744-752`) — and calls `execute_live_order`.
5. `execute_live_order` (`VT/agent/src/live/sdk_order_gate.py:62-181`) denies on: no/unknown-schema mandate, expired mandate, halt flag, unpriceable quantity; reads positions/balance; takes a daily-order lock; runs `check_mandate`; on `None` calls `_allow`.
6. `check_mandate` (`VT/agent/src/live/enforcement.py:458-620`) checks, in order: exclude list, allowed instrument types, asset-class buckets, single-order notional ≤ `max_order_notional_usd`, post-trade gross exposure, gross leverage vs `account_funding_usd`, trades per UTC day, funding, and market-cap/liquidity floors.
7. `_allow` places the order (`sdk_order_gate.py:461-462`):

```python
try:
    result = connector_module.place_order(config, **place_kwargs)
```

   `place_kwargs` are the model's arguments verbatim (`service.py:726-734`).

### Structural or prompt-guarded?

- **Structural (code, deterministic):** the *ceiling* on live orders — mandate existence and expiry, halt flag, notional/exposure/leverage/count caps, universe filters (all in `enforcement.py`/`sdk_order_gate.py`, fail-closed). The mandate itself can only be written by `POST /mandate/commit` with a surface-originated consent acknowledgement (`VT/agent/src/live/mandate/commit.py:1-12`; `VT/agent/src/api/live_routes.py:761-796`). This is genuinely well-built bounded-autonomy machinery.
- **Not structural:** *what* is traded, *when*, in *which direction*, at *what size within the cap*, and *whether to use the live profile at all* — all LLM decisions. There is no stop-loss requirement, no risk-per-trade from stop distance, no signal-level validation, no strategy lifecycle gate. The runner prompt itself says the gate is the "hard backstop" and the model should "defer" rather than breach (`runner.py:240-244`) — i.e. staying *well inside* the caps is prompt-guarded.
- **Paper orders:** no gate at all (`service.py:736-737`), apart from connector-level MT5 size guards (`VT/agent/src/trading/connectors/mt5/orders.py:1-16`).
- **Currency hole (code reading, not tested):** for a notional-only order, the model's account-currency amount is checked against USD caps without conversion (`service.py:747`; `sdk_order_gate.py:898-906` acknowledges "FX normalization is a follow-up"). The docstring argues this over-denies for HKD/CNH; for a GBP- or EUR-denominated account it would **under-state** USD exposure by the GBPUSD/EURUSD rate.
- **Shell escape:** see §2 security item 2.

Against Fiboki's cardinal rule this is a categorical mismatch: in Fiboki, ALPHA is a compiled DSL, PORTFOLIO sizes once (`portfolio.sizing.size_trade`), RISK permits, and `Order` is constructed only in `ExecutionService.submit`; the agent layer cannot import any of it. Vibe-Trading's live stack is an LLM-as-PM with a deterministic cap layer.

## 5. Evidence of performance

- **Trading performance: none in the repository.** No backtest results, benchmark tables, leaderboards or papers with returns are committed. The README links an external blog post ("Which of the 191 GTJA alphas still work in 2026?", `VT/README.md:243`); it is not in the repo and was not read. Treat as a claim.
- **Engineering evidence: substantial.** 711 test files / 153k lines under `agent/tests`; a `pytest-socket` network kill-switch is declared (`pyproject.toml` `dev`); the eval harness refuses to count missing instrumentation as success. None of this was executed here.
- **Methodology issues found in the validation code (inference from reading, not run):**
  - The permutation test shuffles **trade P&L order** (`VT/agent/backtest/validation.py:64-85`). Mean and standard deviation of per-trade returns are almost invariant under reordering (only the equity denominator in `_path_metrics`, `:124-128`, changes), so `p_value_sharpe` tests path order, not the null of "no edge". It cannot detect a strategy with no alpha.
  - `_path_metrics` annualises **per-trade** returns with `sqrt(bars_per_year)` (`:132`). If trades are fewer than bars, this inflates Sharpe — the same class of defect Fiboki V1 fixed.
  - The first trade's P&L drops out of the return series (`np.diff(equity)`, `:127`).
  - No trial-count deflation in the backtest path; DSR/PBO exist only as library calls (§3).
  - Forex default leverage 100:1 (`forex.py:101`) exceeds UK/EU retail CFD limits (30:1 majors, 20:1 gold and major indices under the FCA's 2019 CFD rules) — a UK operator must override it.
  - Positive: next-bar-open execution (`base.py:244-248`) and an explicit refusal to use the current bar's close for price-limit checks (`:624-632`); the changelog records multiple forward-fill "0% return" fixes (`VT/README.md:98`, claim).
- **Strategy discovery** reports per-regime evidence, including a sizing-corrected breakeven fee `ln(1+R)/(2·N·s)·10⁴` bps (`VT/agent/src/strategy_discovery/models.py:384-409`), and refuses to present insufficient evidence as a recommendation (`VT/agent/src/strategy_discovery/guard.py:23-38`). Regimes are labelled by trailing benchmark return with `sqrt(252)` annualisation assumed daily (`evidence_harness.py:1-30`).

## 6. Extractable ideas for Fiboki, ranked

Costs are engineer-days for one person familiar with Fiboki, including tests. "Port" means re-implement the idea in Fiboki's style; nothing below is recommended as an import or sidecar.

| # | Pattern (source) | Fiboki home | Cost | Risk | Mode |
|---|---|---|---|---|---|
| 1 | **Ledger hardening: exclusive `flock` across read-tail+append, verify the whole chain before appending and refuse to extend a broken one, fsync the parent dir on first create** (`VT/agent/src/governance/ledger.py:1-45`, `verify_chain` `:309`, `_lock_exclusive` `:327`, `_fsync_dir` `:354`, `append_record` `:368`) | `FB/src/fiboki/agents/audit.py` `JsonlAuditLedger.append` (`:283-292`) currently appends against an in-memory chain loaded at `__init__`/`reload` with no inter-process lock, so two writers (API + research worker) can fork the chain; same treatment for `api/audit_trail.py` | **S** (1–2) | Low. O(n) verify per append — use a cached tail hash plus a lock, with full verify at startup | Port |
| 2 | **Run manifest hash**: `sha256` over system prompt, skill bodies, tool-registry names and a curated set of package versions, stamped on each run (`VT/agent/src/governance/manifest.py:1-60`, `build_run_manifest` `:356`) | `agents/audit.py` (new field on `AuditRecord` or a workflow-start record) + `agents/workflows.py` `run_research_cycle`. Include `ROLES[*].system_prompt`, `REGISTRY` tool names/schemas and the numpy/pandas/scipy versions | **S** (1–2) | Low | Port |
| 3 | **Numeric grounding gate**: agent prose must declare every number with a role and cite the tool call that produced it; the gate matches and rejects undeclared or unmatched numbers (`VT/agent/src/agent/grounding/figures.py:1-33`, `release.py`, `policies.py`) | `agents/sandbox.py` output validation for `WRITE_RESEARCH_NOTE`, `WRITE_CRITIQUE`, `WRITE_HYPOTHESIS`; enforce in `AgentSession.call` for those write tools. Fiboki's audit already has call ids, so "cite by call id" is simpler than text matching. Directly serves AGENTS.md "never quote a number without its provenance" | **M** (5–8) | Medium: false rejections; keep the rule narrower than Vibe's 6k-line version (declare-and-cite, exact match on cited value) | Port |
| 4 | **Scheduled playbooks with a machine-parsed verdict tail** and a closed per-playbook state vocabulary; malformed → `contract_violation`, never guessed (`VT/agent/src/scheduled_research/verdict.py:1-30`; playbooks in `scheduled_research/playbooks/`) | `agents/workflows.py` (new `run_market_brief` workflow) scheduled by `orchestrator.ScheduledJob`; output is a JSON research note (Fiboki already parses with `json.loads` + `extra="forbid"`, which is stricter than regex). Host roles: `market_regime_analyst` + `research_librarian`. Vocabulary must be descriptive (`TRENDING`, `EVENT_RISK`, `DATA_SUSPECT`), never `BUY`/`SELL` | **S–M** (3–5) | Low if the vocabulary cannot be read as a trade instruction | Port |
| 5 | **Offline eval harness over recorded runs**: deterministic, read-only checker of persisted artefacts, PASS/FAIL/NOT_EVALUABLE/INVALID_ARTIFACT, missing instrumentation never a pass (`VT/agent/evals/harness/README.md`, `assertions.py`, `runner.py`) | New `FB/src/fiboki/agents/evals/` reading the JSONL audit ledger + research store; cases per workflow (e.g. "critic cited the validation report id", "no step phrased a trade instruction"). Needed before the first real-model run (AI_AGENT_ARCHITECTURE §10: "No agent has ever run against a real model") | **M** (4–6) | Low | Port |
| 6 | **Loop hygiene for multi-turn sessions**: identical-call blocking after repeated failure, duplicate-success refusal, read-only parallel / write serial batching, read-only timeouts but never killing writes, bounded replay of compacted read results (`loop.py:2441-2605`, `:2714-2761`, `:2948-3023`) | Only relevant once Fiboki has a multi-turn tool loop driving `AgentSession`; would live beside `agents/session.py`. Fiboki's current workflows are single-step per role | **M** (5–10) | Medium: complexity; defer until a real model is wired | Port later |
| 7 | **Expiring, human-committed authorisation document** (mandate with `expires_at`, consent hash, proactive expiry check every tick) (`VT/agent/src/live/mandate/model.py:99-148`; `runner.py:198-216`) | Not for agents. For the **deterministic** live path: an operator-signed "live authorisation" per strategy/instrument with a 30-day expiry, checked by `RiskGateway._check_strategy_lifecycle` (`FB/src/fiboki/risk/gateway.py:519`). Complements, does not replace, the five live controls | **S–M** (3–4) | Low; must not become a sixth way to *enable* live — only a way to *lapse* it | Port |
| 8 | **Breakeven cost metric** `ln(1+R)/(2·N·s)` in bps, reported alongside "survival at 2× spread" (`VT/agent/src/strategy_discovery/models.py:384-409`) | `backtest/metrics.py` / `validation/` report field; informative, not a gate. Its multi-position caveat (`evidence_harness.py:23-28`) must be kept | **S** (1–2) | Low; a golden test with worked arithmetic is required | Port |
| 9 | **Write-ahead pending action keyed by client order id; recovery by exact identity, never by resubmission** (`VT/agent/src/live/pending_action.py`; `sdk_order_gate.py:434-539`, `:542-620`) | Fiboki already writes a fsynced PENDING intent and reconciles by broker ref/client_ref (`FB/docs/v2/ARCHITECTURE.md` §5). Use Vibe's recovery evidence checks as a review checklist only | — | — | Reference |

What maps to Joe's goal:
- **"Searching markets for entries"** stays deterministic: compiled DSL strategies evaluated on closed bars by `workers/live_worker.py`. The agent contribution is upstream (hypotheses → DSL → ladder). Pattern 4 gives a daily structured *context* brief, not entries.
- **"Scanning news"**: nothing in Vibe-Trading fits FX (Eastmoney/Yahoo equity headlines). See AI-Trader pattern A below for the pipeline shape.
- **"Auto trading"**: Fiboki's existing live path, gated by the validation ladder; patterns 1, 2 and 7 strengthen its audit and authorisation.

## 7. What NOT to take, and why

1. **The live-trading architecture** (`trading_place_order`, `sdk_order_gate`, `LiveRunner`, mandate-pinned autonomous prompt). The LLM chooses instrument, direction, timing, size and paper/live; code only caps. This inverts Fiboki's ALPHA→PORTFOLIO→RISK→EXECUTION layering and would break `test_no_gateway_bypass.py` and the agents-import AST test by design.
2. **An API process that constructs and drives a trading runner** (`VT/agent/src/api/live_routes.py:690-730`) — the exact V1 failure mode Fiboki's topology rule ("the API never starts a worker") exists to prevent.
3. **The `bash` tool and LLM-authored Python backtests.** Incompatible with `assert_no_dynamic_execution` and the DSL-as-data principle; Vibe's own comments admit the AST denylist is incomplete.
4. **Broker connectors.** No OANDA/IG/spread-bet; MT5 is Windows-only; the rest are equity/crypto venues outside Fiboki's universe.
5. **Backtest engines and validation.** Daily-bar-centric, static spread table, 100:1 default FX leverage, a permutation test that does not test edge, per-trade Sharpe annualised per bar, no deflation in the path. Fiboki's `sim/` + 7-rung ladder is stronger on every axis read.
6. **Alpha Zoo** (462 equity cross-sectional factors). Irrelevant to FX/gold/indices and would inflate Fiboki's honest trial count if mined.
7. **Any dependency.** The lockfile conflicts with every numerically relevant Fiboki pin and with the `click` pin that keeps Fiboki's CLI working.
8. **`r.jina.ai` web reader** — leaks every researched URL to a third party.
9. **Swarm presets.** Free-text hand-offs, `bash`-enabled roles, equity/China-market-centric prompts.

## 8. Verdict — **PORT-PATTERNS**

Vibe-Trading is a serious, fast-moving, well-commented codebase whose live-trading core is an LLM portfolio manager boxed in by deterministic caps. That is the opposite of Fiboki's cardinal rule, so none of the trading, broker, backtest or agent-loop code should enter Fiboki, and its dependency lock is incompatible with Fiboki's pins. Its value is in the scaffolding it built to make agents auditable: a locked, verify-before-append hash ledger; a run manifest; a numeric-grounding release gate; strictly parsed playbook verdicts; and an offline eval harness that treats missing evidence as not-evaluable. Patterns 1–5 are worth roughly 14–23 engineer-days in total and each strengthens an existing Fiboki mechanism rather than adding a new authority. MIT licensing makes porting ideas cost-free; if any file is copied verbatim, retain the MIT notice.

---

# Part 2 — HKUDS/AI-Trader (HEAD `d03ff6c`, 2026-06-11 17:26 +0800)

## 1. What it actually is

A **hosted multi-tenant web service** for third-party AI agents, not an agent. Backend: FastAPI (`AT/service/server/main.py:1-101`, `routes.py`) with SQLite or PostgreSQL (`database.py`, 1,695 lines), optional Redis cache (`cache.py`), and a separate background worker (`worker.py`; API background tasks default **off**, `tasks.py:1247-1249`). Frontend: React 18 + Vite 5 (`AT/service/frontend/package.json`; `App.tsx` was 4,712 lines at the first visible commit). Agents integrate by being told to read `https://ai4trade.ai/SKILL.md` (`AT/README.md`) — six Markdown "skills" in `AT/skills/*/SKILL.md` (2,259 lines) describing REST endpoints for registration, publishing "signals" (trades, strategies, discussions), following/copy-trading, heartbeats, Polymarket and market intel. The server records **simulated** trades against a per-agent $100,000 cash balance (`AT/skills/ai4trade/SKILL.md:949-978`), mirrors them 1:1 to followers, awards points, runs challenges/leaderboards, and runs A/B "experiments" on agent behaviour (`experiments.py`, `research/`). There is **no LLM agent loop in the repo**; the only LLM call is an optional OpenRouter one-paragraph summary for the market-intel dashboard (`AT/service/server/market_intel.py:583-619`).

**Size** (same counter):

| Language | Files | Non-blank lines |
|---|---:|---:|
| Python | 74 (21 `test_*.py`) | 26,069 |
| TSX | 10 | 7,543 |
| TypeScript | 2 | 327 |
| CSS | 1 | 2,957 |
| JSON (incl. 25 research schemas) | 32 | 6,378 |

## 2. Licence, maintenance, dependencies, security

### Licence
- **No `LICENSE`, `NOTICE` or `COPYING` file exists at HEAD** (checked with `ls` and `find -iname "*licen*"`, which finds only README files). `AT/README.md:11` shows an MIT badge linking to `LICENSE`, which 404s in this tree. Absent a licence file, the default is all rights reserved; a badge is not a grant. **Legally, nothing here should be copied** until HKUDS adds a licence. Porting general ideas is unaffected.

### Maintenance signals
- Shallow clone; earliest visible commit 2026-04-08 (a whole-tree snapshot, so earlier history — including any older "benchmark" incarnation of AI-Trader — is **not visible**). 89 visible commits: April 26, May 18, June 45. **Zero commits in the last 90 days** (since 2026-06-30); last commit 2026-06-11. Seven author names, two dominant ("Tianyu Fan" 69, "chaohuang-ai" 15). Highest PR number referenced in history: `#255`. No tags, no CI workflows directory. Open issues not checked.
- The production deployment (ai4trade.ai) may be maintained elsewhere; the repo is not.

### Dependency surface
`AT/service/requirements.txt`: all lower-bound, unpinned — `fastapi>=0.109.0`, `pydantic>=2.5.3`, `web3>=6.15.1`, `openrouter>=1.0.0`, `psycopg[binary]>=3.2.1`, `redis>=5.0.8`, `yfinance`, `aiohttp`, `requests`, `pytest`. `AT/research/requirements.txt`: `pandas>=2.2.0`, `numpy>=1.26.0`, `scipy>=1.12.0`, `statsmodels`, `networkx`, `matplotlib`. Ranges happen to admit Fiboki's pins, but nothing is locked, so a reproduction is not guaranteed. `web3`/`eth_account` is used for wallet-signature agent recovery (`AT/service/server/utils.py:83-84`).

### Security observations
1. **Bearer tokens stored and compared in plaintext**: `SELECT * FROM agents WHERE token = ?` (`AT/service/server/services.py:17-26`; `token TEXT UNIQUE NOT NULL`, `database.py:566`).
2. **Weak password hashing**: single-round `sha256(password + salt)` (`AT/service/server/utils.py:18`, `:30`) — not a KDF.
3. **Remote, mutable prompt supply chain**: skills instruct agents to `curl` SKILL.md files from ai4trade.ai and install them (`AT/skills/ai4trade/SKILL.md` "Save Files Locally"; `AT/skills/tradesync/SKILL.md:14-30`). Whoever controls that host controls the agent's instructions — a prompt-injection channel by construction.
4. **Client-supplied historical trade times are accepted** (see §5) — an integrity, not confidentiality, flaw.
5. No `eval`/`exec`/`subprocess`/`pickle`/`shell=True` in server code outside tests. External hosts: Alpha Vantage, Hyperliquid, Polymarket Gamma/CLOB, Adanos, OpenRouter, yfinance (`AT/.env.example`). No telemetry found.
6. CORS: configured origins with `allow_credentials=True` (`AT/service/server/routes.py:30-33`) — acceptable if origins are tight.

## 3. Architecture walk-through

- **Agent loop / tools / memory:** none in the repo. External agents (OpenClaw, Claude Code, etc. per README) read the SKILL.md files and call REST endpoints. "Heartbeat" is a polling protocol for notifications (`AT/skills/heartbeat/SKILL.md`).
- **Market data:** `price_fetcher.py` — Alpha Vantage 1-minute bars then yfinance fallback for US stocks (`:853`, `:819`), Hyperliquid 1-minute candle or mid for crypto (`:620-676`, `:930-935`), Polymarket mid/best bid-ask (`:461-547`). `market_intel.py` pulls Alpha Vantage `NEWS_SENTIMENT` into snapshot tables on a worker schedule; the API reads only snapshots (`:1-8`, `:671-697`, `:1292`).
- **Signals → "orders":** `POST /api/signals/realtime` (`AT/service/server/routes_signals.py:106-555`): authenticate token; validate market ∈ {us-stock, crypto, polymarket}; quantity ≤ 1e6; fetch server price at `executed_at` (or, for markets outside that set when sync fetch is off, **use the client's `price`**, `:163-223`; `routes_shared.py:138-142`); check cash/position; insert signal; update position; debit/credit cash with a 0.1% fee (`fees.py`); score "signal quality"; award points; then **copy the identical quantity to every active follower** inside savepoints (`:391-527`).
- **Broker/exchange integrations:** none. The "Sync External Trade" method is self-reported: the agent posts a trade it claims to have made elsewhere (`AT/skills/ai4trade/SKILL.md:776-796`). **No FX, CFD, spread-bet, OANDA or IG support** in code (`SUPPORTED_MARKETS = {'us-stock', 'crypto', 'polymarket'}`, `routes_shared.py:63`), contradicting the README's "Stocks, Crypto, Forex, Options, Futures".
- **Backtest/eval:** none for strategies. `challenge_scoring.py` replays challenge portfolios with mark-to-market (`:1-60`). `research/` computes A/B, DiD, regression, bootstrap and FDR tables on platform behaviour (`AT/research/README.md`); `research/exports/{tables,figures}` are **empty** in the repo.
- **LLM output parsing:** the platform receives structured JSON over REST (pydantic request models). Free-text signal `content` is mined by **keyword/regex heuristics** — "buy/long/bull" → up, a number after "target" → target price (`AT/service/server/signal_quality.py:77-127`).

## 4. Does the LLM have order authority?

**Within this repo, yes over simulated money; no over real money.** The external LLM agent chooses market, symbol, side, quantity and (for historical sync) the execution time; the server executes against simulated cash with only schema, cash, position and size-magnitude checks (`routes_signals.py:130-283`). The code path, quoted:

```python
# routes_signals.py:271-283 (cash check), 308-319 (position update)
if action_lower in ['buy', 'short']:
    total_deduction = trade_value + fee
    ...
    if current_cash < total_deduction:
        raise HTTPException(status_code=400, detail=(f'Insufficient cash. ...'))
...
_update_position_from_signal(agent_id, symbol, market, side, qty, price, executed_at, cursor=cursor, ...)
```

and the follower fan-out, same quantity, no follower-side risk sizing (`:436-448`):

```python
_update_position_from_signal(
    follower_id, symbol, market, side, qty, price, executed_at,
    leader_id=agent_id, cursor=cursor, ...
```

Guarding is **structural only in the trivial sense** (input validation, cash sufficiency). There is no risk engine, no exposure limit, no kill switch. Real-money exposure exists only outside the repo: the copytrade skill documents OpenClaw settings `autoFollow` / `autoCopyPositions` (`AT/skills/copytrade/SKILL.md:49-51`, "Currently uses 1:1 ratio (fully automatic copy)", `:208`) — whatever the external plugin does with that is not auditable here.

## 5. Evidence of performance

- **None for trading.** No returns, benchmarks or backtests are committed. The research log (`AT/research/experiment_process_log.md`) documents an A/B experiment on ~5,289 platform agents (control / competition / cooperation / hybrid reward variants) — a study of *agent social behaviour*, with operational steps logged but no result tables in the repo.
- **Leaderboard integrity is structurally compromised (code reading):**
  - `validate_executed_at` accepts any past UTC timestamp inside US market hours for us-stock, and any timestamp at all for crypto; there is **no maximum age and no future bound** (`AT/service/server/routes_shared.py:658-705`). The server then fills at the historical price (`routes_signals.py:199-221`; `price_fetcher.py:705-719`). An agent can therefore "trade" yesterday at yesterday's price after seeing today's — textbook look-ahead — and the documented "Sync External Trade" method encourages supplying historical times (`AT/skills/ai4trade/SKILL.md:776-796`).
  - For crypto, a missing historical candle falls back to the **current** mid (`price_fetcher.py:706`, `:935`), so a backdated trade can be mispriced either way.
  - Survivorship/selection: challenge ranking includes only participants who traded (commit `e8a66c1`, "Rank only traded challenge participants").
  - Cost model: flat 0.1% fee (`fees.py`), no spread, no slippage; shorts are margin-free (`cover_credit = (2*entry - price)*qty - fee`, `routes_signals.py:328`).
  - Incentives reward volume: 10 points per published signal (`AT/service/server/config.py:39`); points can be exchanged for more simulated cash (`AT/skills/ai4trade/SKILL.md:978`).
- **"Signal quality" is not outcome-based.** `score_signal_quality` weights verifiability/evidence/specificity/novelty from keyword presence and text length (`signal_quality.py:200-212`); `horizon_end_at` is always `None` (`:123`), so no prediction is ever scored against what happened.
- The market-intel LLM prompt instructs: "Do not mention AI, models, or uncertainty disclaimers" (`market_intel.py:595`) — the opposite of Fiboki's `EPISTEMIC_STANDARD`.

## 6. Extractable ideas for Fiboki, ranked

No code may be copied (no licence). Ideas only.

| # | Pattern | Fiboki home | Cost | Risk | Mode |
|---|---|---|---|---|---|
| A | **Snapshot-based news pipeline**: a background job pulls a news/sentiment feed into an append-only snapshot table with `as_of`, dedupes by URL/title (`market_intel.py:622-631`), summarises deterministically (`_build_news_summary`, `:634-668`), and serves read-only snapshots; the API never calls the provider (`:1-8`). An optional LLM paragraph has a deterministic fallback (`:583-619`) | Deterministic ingestion as a platform-internal `JobType` (e.g. `NEWS_INGEST`) drained by the research worker into a versioned store under `data/`; a new read capability (e.g. `READ_NEWS_SNAPSHOT`, passes `assert_no_execution_capability`) and a `query_news` read tool in `agents/tools.py`; consumers `market_regime_analyst` and `research_director`. Point-in-time `as_of` is mandatory so research never sees news published after a bar. Provider choice for FX/gold/index news is **open and unverified** (Alpha Vantage `NEWS_SENTIMENT` is equity-ticker-centric) | **M–L** (6–10, excluding provider contract) | Medium: prompt injection via headlines — headlines must reach models as quoted data, and no news-derived field may feed `RiskGateway` directly | Port |
| B | **Pre-registered forecast record** — `direction`, `target_price`/`target_probability`, `confidence`, `horizon_start_at`, `horizon_end_at`, `invalid_if`, `evidence` (`signal_quality.py:113-127`; `research/schemas/predictions.schema.json`) — done properly: structured JSON from the agent (not regex), mandatory horizon end, and a deterministic scorer | New `WriteDomain` (e.g. `RESEARCH_FORECAST`) and capability `WRITE_FORECAST` in `agents/{capabilities,tools}.py`; a platform-internal scoring job reading `data/` bars after `horizon_end_at` and writing Brier/hit-rate per role and model. Gives Fiboki a measured answer to "is the news/regime agent any good?" before its output is trusted anywhere, and feeds the model router (`providers.ModelRouter`). The vocabulary must not read as an order ("EURUSD higher over 5 sessions, p=0.6", not "buy EURUSD") | **M** (4–6) | Low–medium: forecasts must be kept out of the strategy ranking and counted in the honest trial count if ever mined | Port |
| C | **Experiment discipline for agent variants**: fixed cohort frozen before intervention, dry-run before send, a timestamped process log of every step and decision (`AT/research/experiment_process_log.md`; `experiments.py`) | Comparing prompt/model variants for Fiboki roles via `agents/providers.ModelRouter` and pattern B's scores; process log in `docs/`. Two operators and a dozen roles do not need assignment infrastructure — a written protocol is enough | **S** (1–2, docs + a small config) | Low | Port (idea only) |
| D | **Point-in-time price guard** — reject a client-supplied price when the server quote is unavailable (`AT/service/server/tests/test_realtime_trade_price_guard.py`) | Already Fiboki's stance (absence raises). Reference only | — | — | Reference |

## 7. What NOT to take, and why

1. **The platform itself** — social signal publishing, copy-trading, points, challenges. Copy-trading at 1:1 fixed quantity with no follower-side sizing or risk is incompatible with "sizing happens once in `portfolio.sizing.size_trade`" and with portfolio-aware risk.
2. **Leaderboards/challenge scoring** as evidence of anything — the backdating hole and the cost model make the numbers unbelievable (AGENTS.md §6: "would this change let a number be believed that should not be?" — yes).
3. **Signal-quality heuristics** — keyword and length scores reward verbosity, not accuracy.
4. **Remote SKILL.md bootstrapping** — a mutable third-party prompt channel.
5. **Auth code** — plaintext tokens and single-round SHA-256 passwords.
6. **Anything copied verbatim** — no licence.
7. **The "no uncertainty" summarisation prompt.**

## 8. Verdict — **REFERENCE-ONLY**

AI-Trader at HEAD is not a trading agent and contains no broker, risk or backtest machinery; it is a simulated social trading venue for external agents, with no FX support, an unlicensed tree, no commits in the last 90 days, and a leaderboard that accepts backdated fills. It would be a REJECT on code grounds alone. It stays at REFERENCE-ONLY because two ideas are useful when rebuilt in Fiboki's style: a snapshot-based, point-in-time news ingestion pipeline (A) and a pre-registered forecast record with deterministic outcome scoring (B). Together those are the honest route to Joe's "scanning news" goal: agents read and classify news, their calls are measured, and nothing they say reaches execution.

---

## Recommended sequence for Fiboki (both repos combined)

1. Vibe #1 ledger hardening and #2 run manifest (S+S) — before any real model runs.
2. Vibe #5 eval harness (M) — the acceptance test for the first real-model run.
3. AI-Trader B forecast record + scorer (M) — the measurement layer.
4. AI-Trader A news snapshot pipeline (M–L) — the data, only once B can score what agents do with it.
5. Vibe #4 scheduled market brief (S–M) — first consumer of A and B.
6. Vibe #3 numeric grounding gate (M) — tighten once there is real output to test against.
7. Vibe #7 expiring live authorisation, #8 breakeven cost metric — deterministic side, independent of agents.

None of these adds an execution capability, a `JobType` that can express an order, or an import from `broker/`, `risk/` or `portfolio/` into `agents/`. Each should ship with the AST/unit tests Fiboki already uses to hold those properties.

---

## Facts vs assumptions

**Facts (read or run in this session):**
- Both clones are shallow; the visible windows are 2026-08-29→2026-09-28 (Vibe-Trading, 440 commits, 17 authors) and 2026-04-08→2026-06-11 (AI-Trader, 89 commits, 7 authors, 0 in the last 90 days).
- Vibe-Trading is MIT with an Apache-2.0 NOTICE for Qlib material; AI-Trader has no licence file at HEAD.
- LOC and file counts in the tables above (custom non-blank counter; `cloc` unavailable).
- Vibe-Trading's lockfile pins numpy 2.4.6, pandas 2.3.3, scipy 1.17.1, pydantic 2.13.5, click 8.5.0, all differing from Fiboki's pins.
- Vibe-Trading's `trading_place_order` lets the model set symbol, side, quantity/notional, order type and connection profile; paper orders bypass any gate; live orders pass the deterministic mandate gate in `sdk_order_gate.py`/`enforcement.py`.
- Vibe-Trading's `LiveRunner` is constructed in the API process and prompts the agent to trade inside the mandate.
- No OANDA or IG adapter exists in either repo; AI-Trader supports only us-stock, crypto and polymarket.
- AI-Trader accepts historical `executed_at` with no age bound and fills at historical prices; tokens are plaintext; passwords are single-round salted SHA-256.
- Neither repo contains trading performance results; AI-Trader's research export directories are empty.
- Fiboki's `JsonlAuditLedger.append` has no inter-process lock and no verify-before-append (`FB/src/fiboki/agents/audit.py:283-292`).

**Inferences from code, not executed:**
- A shell-enabled Vibe-Trading session could overwrite `mandate.json` or remove the halt flag (no path policy found; not attempted).
- Vibe-Trading's notional-only live orders under-state USD exposure for a GBP/EUR account (reading `service.py:747` with `sdk_order_gate.py:898-906`; no test run).
- Vibe-Trading's permutation test is near-invariant for Sharpe and so does not test edge; per-trade Sharpe is annualised with `sqrt(bars_per_year)` (reading `validation.py:64-136`; not run numerically).
- AI-Trader's backdating path is exploitable for look-ahead on the leaderboard (reading `routes_shared.py:658-705` and `routes_signals.py:199-221`; not exercised).

**Assumptions / not verified:**
- Open issue counts and GitHub release pages for either repo (no network checks).
- Whether the ai4trade.ai production service runs code that differs from this repo.
- Whether the Vibe-Trading test suite passes (not run).
- FX/gold/index coverage of any news provider named above (Alpha Vantage `NEWS_SENTIMENT` coverage for FX not checked).
- Sanctions implications of Vibe-Trading's Iranian exchange connectors for a UK operator (flagged, not assessed).
- Engineer-day estimates are judgement, assuming one engineer familiar with Fiboki's agents package and test conventions.
