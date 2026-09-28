# B — TradingAgents (TauricResearch) due diligence for Fiboki V2

**Subject:** `TauricResearch/TradingAgents` v0.5.1, HEAD `35543d0` ("TradingAgents v0.5.1 release", 2026-09-24), cloned at `/home/claude/research/repos/TradingAgents` (shallow clone, 169 commits back to 2026-06-14).
**Paper:** Xiao, Sun, Luo, Wang, *TradingAgents: Multi-Agents LLM Financial Trading Framework*, arXiv:2412.20138 (v1 2024-12-28 … v7 2025-06-03).
**Auditor's frame:** Fiboki V2 at `/home/claude/v2` (agents package, `marketstate/`, `discovery/`, `portfolio/construction.py`).
**Date:** 2026-09-28. All file:line citations are to the clone above (prefixed `TA:`) or to Fiboki V2 (prefixed `FB:`).

---

## 0. Verdict up front

**PORT-PATTERNS (narrowly), with REFERENCE-ONLY for everything else. Do not integrate code.**

- There is nothing in TradingAgents that can be imported into Fiboki without breaking its rules: the whole framework is built on LangGraph and LangChain (neither is a Fiboki dependency), it will not co-install with Fiboki's exact pins (measured below: `pandas>=2.3.0` vs `pandas==2.2.3` is unsatisfiable), and it is written for US equities.
- The paper's evidence is a single three-month window (Jan–Mar 2024), three reported tickers, no transaction costs, no repeated runs, and no discussion of LLM training-data contamination. Independent work finds that the claimed LLM advantage does not survive longer windows or wider universes. **The paper is not evidence that the pattern adds value.**
- What *is* worth taking: the bull/bear debate shape, recast as an auditable Fiboki workflow that produces a schema-validated **conviction artefact**, plus about six small defensive idioms the maintainers have hardened well (explicit-absence markers, as-of clamping of tool dates, withholding live-only feeds from historical runs, a `REVIEW` sentinel instead of a silent Hold, a graph-shape run signature, scrubbing secrets from URLs).
- That artefact may reach the portfolio layer only as a **down-only, capped, time-limited size multiplier, switched off by default**, and should be shadow-logged forward for months before anyone switches it on. Historical LLM "convictions" cannot be backtested honestly (§5.4).

Estimated effort for the proposed design: **about 15 engineer-days (range 12–20)**, not counting a point-in-time news source (+3–5 days) or the months of forward shadowing, which is calendar time rather than engineering time (§6.6).

---

## 1. What it is, from the code

### 1.1 Entry points

| Entry | Location | What it does |
|---|---|---|
| Python API | `TA:tradingagents/graph/trading_graph.py:43` `TradingAgentsGraph` | Builds the LLM clients (`:75-89`), memory log (`:91`), conditional logic (`:93-96`) and graph (`:112-113`). `propagate(company_name, trade_date, asset_type="stock", portfolio=None)` at `:157-180` returns `(final_state, signal)`. |
| Example script | `TA:main.py:1-17` | `TradingAgentsGraph(debug=True, config=DEFAULT_CONFIG.copy()).propagate("NVDA", "2026-09-01")`. |
| CLI | `TA:pyproject.toml` `[project.scripts] tradingagents = "cli.main:app"`; `TA:cli/main.py:90` (`--asset-type stock|crypto`) | Typer/Rich interactive CLI; also `run`, `backtest` and summary subcommands (`TA:cli/main.py:112`). |
| Batch "backtest" | `TA:tradingagents/backtest.py:128-175` `run_backtest(tickers, dates, config, …)` and `:178-208` `summarize` | Runs the graph over a ticker × date grid into a separate decision log, then scores hit rate and mean alpha by rating. It is explicitly **not** a portfolio simulator (`:9-14`). |
| Docker | `TA:Dockerfile` | `ENTRYPOINT ["tradingagents"]`, non-root `appuser`. |

`propagate` refuses future dates and non-canonical dates (`TA:trading_graph.py:29-40`), settles earlier pending decisions for the same ticker on the way in (`:260-289`), runs the graph (`:301-344`), writes a JSON state log (`:346-386`), appends the decision to the memory log (`:291-299`) and returns `parse_rating(final_trade_decision)` (`:388-390`).

### 1.2 LangGraph topology (enumerated from `TA:tradingagents/graph/setup.py`)

Nodes (`setup.py:97-110`):

- For each selected analyst key in order `market, social, news, fundamentals` (default at `:65`; the plan is built in `TA:graph/analyst_execution.py:26-72`):
  - agent node: `Market Analyst` / `Sentiment Analyst` / `News Analyst` / `Fundamentals Analyst`
  - clear node: `Msg Clear Market` / `Msg Clear Sentiment` / `Msg Clear News` / `Msg Clear Fundamentals` (from `TA:agents/context.py:211-235`, which deletes messages and injects an instrument-anchored placeholder)
  - tool node: `tools_market`, `tools_news`, `tools_fundamentals`, each a LangGraph `ToolNode`. The Sentiment Analyst has **no** tool node because it pre-fetches its data (`analyst_execution.py:34-41`).
- `Bull Researcher`, `Bear Researcher`, `Research Manager`, `Trader`, `Aggressive Analyst`, `Neutral Analyst`, `Conservative Analyst`, `Portfolio Manager`.

That makes 19 nodes with all four analysts selected.

Edges (`setup.py:112-144`):

```
START -> Market Analyst
Market Analyst --(tool_calls?)--> tools_market | Msg Clear Market       (:115-119, router at :43-47)
tools_market -> Market Analyst
Msg Clear Market -> Sentiment Analyst                                     (:124-125)
Sentiment Analyst -> Msg Clear Sentiment                                  (:121, no tools)
Msg Clear Sentiment -> News Analyst
News Analyst <-> tools_news ; News Analyst -> Msg Clear News
Msg Clear News -> Fundamentals Analyst
Fundamentals Analyst <-> tools_fundamentals ; -> Msg Clear Fundamentals
Msg Clear Fundamentals -> Bull Researcher                                 (:124, "last analyst hands over")
Bull Researcher  --should_continue_debate--> Bear Researcher | Bull Researcher | Research Manager   (:128-133)
Bear Researcher  --should_continue_debate--> (same map)
Research Manager -> Trader                                                (:134)
Trader -> Aggressive Analyst                                              (:135)
Aggressive/Conservative/Neutral --should_continue_risk_analysis--> Aggressive | Conservative | Neutral | Portfolio Manager  (:137-142)
Portfolio Manager -> END                                                  (:144)
```

Routing logic (`TA:graph/conditional_logic.py`):

- Investment debate (`:12-21`): end when `count >= 2 * max_debate_rounds`, otherwise alternate by the prefix of `current_response` ("Bull…" goes to Bear). The Bull always opens. The inline comment "3 rounds of back-and-forth" (`:17`) is stale: the default `max_debate_rounds=1` (`TA:default_config.py:115`) gives exactly **one Bull turn and one Bear turn**.
- Risk debate (`:23-33`): end when `count >= 3 * max_risk_discuss_rounds`. Fixed rotation Aggressive → Conservative → Neutral. The default of 1 gives **one turn each**. The comment at `:27` is stale in the same way.
- `recursion_limit` defaults to 100 (`TA:graph/propagation.py:75`; config `max_recur_limit`, `default_config.py:117`).

Model tiering (`setup.py:78-93`): analysts, Bull, Bear, Trader and the three risk debaters use `quick_thinking_llm`. **Only the Research Manager and the Portfolio Manager** use `deep_thinking_llm`.

### 1.3 Agent roles and their prompts

All four analysts share a system preamble that ends *"Report what your tools support; another agent decides the trade."* (`TA:agents/analysts/market_analyst.py:172`; the same text at `news_analyst.py:41` and `fundamentals_analyst.py:42`).

| Role | File | Key prompt fragments (quoted) | Output |
|---|---|---|---|
| Market (technical) Analyst | `TA:agents/analysts/market_analyst.py:130-162` | "choose up to **8 indicators**… close_50_sma, close_200_sma, close_10_ema, macd, macds, macdh, rsi, boll, boll_ub, boll_lb, atr, vwma"; "call get_verified_market_snapshot… treat it as the source of truth for any exact OHLCV, price-level, or indicator-value claim"; "Write a very detailed and nuanced report… Provide specific, actionable insights" | `market_report` (free text) |
| Sentiment Analyst | `TA:agents/analysts/sentiment_analyst.py:52-183` | Pre-fetched Yahoo news, StockTwits and Reddit blocks; "A 70/30 bullish/bearish split is moderately bullish; ≥90/10 may indicate over-extension"; "Past sentiment is not predictive." The module docstring concedes: "These feeds serve recent items and are not archived, so a historical run's sentiment inputs are not point-in-time" (`:10-13`). | `SentimentReport` (band, 0–10 score, confidence, narrative) (`TA:agents/schemas.py:311-363`) |
| News Analyst | `TA:agents/analysts/news_analyst.py:27-31` | "analyzing recent news and trends over the past week… get_macro_indicators… from FRED (e.g. 'cpi', 'core_pce'… 'yield_curve'), and get_prediction_markets… Polymarket" | `news_report` |
| Fundamentals Analyst | `TA:agents/analysts/fundamentals_analyst.py:27-32` | "analyzing fundamental information over the past week about a company… financial documents, company profile… `get_insider_transactions`" | `fundamentals_report` |
| Bull Researcher | `TA:agents/researchers/bull_researcher.py:31-49` | "You are a Bull Analyst advocating for investing in the {stock}… Growth Potential… Competitive Advantages… Bear Counterpoints… Present your argument in a conversational style" | appended to `investment_debate_state.history` |
| Bear Researcher | `TA:agents/researchers/bear_researcher.py:31-51` | "making the case against investing… Risks and Challenges… Competitive Weaknesses… Bull Counterpoints" | same |
| Research Manager (judge) | `TA:agents/managers/research_manager.py:23-51` | 5-tier scale Buy/Overweight/Hold/Underweight/Sell (`:29-34`); "conflict alone is not a reason to Hold. Commit to the side with the stronger case, sized by how decisively it wins" (`:36`); "**Strategic Actions**: concrete steps for the trader, sized against a standard allocation" (`:49`) | `ResearchPlan` (recommendation, rationale, strategic_actions) (`schemas.py:95-127`) |
| Trader | `TA:agents/trader/trader.py:48-85` | "provide a specific recommendation to buy, sell, or hold"; "State entry price and stop-loss as absolute price levels in the instrument's quote currency" (`:58-61`); "**Action**: exactly one of Buy / Hold / Sell… **Entry Price**, **Stop Loss**, **Position Sizing**: when you can state them" (`:78-82`) | `TraderProposal` (`schemas.py:146-188`) |
| Aggressive Risk Analyst | `TA:agents/risk_mgmt/aggressive_debator.py:32-46` | "actively champion high-reward, high-risk opportunities… Challenge each counterpoint to underscore why a high-risk approach is optimal" | risk history |
| Conservative Risk Analyst | `TA:agents/risk_mgmt/conservative_debator.py:32-46` | "protect assets, minimize volatility… why a conservative stance is ultimately the safest path for the firm's assets" | risk history |
| Neutral Risk Analyst | `TA:agents/risk_mgmt/neutral_debator.py:32-46` | "balanced perspective… why a moderate risk strategy might offer the best of both worlds" | risk history |
| Portfolio Manager (the paper's "fund manager") | `TA:agents/managers/portfolio_manager.py:45-79` | "synthesize the risk analysts' debate and deliver the final trading decision"; scale "Buy: Strong conviction to enter or add to position… Sell: Exit position or avoid entry" (`:53-58`); injects "Lessons from prior decisions and outcomes" (`:38-43`) | `PortfolioDecision` (rating, executive_summary, investment_thesis, optional price_target and time_horizon) (`schemas.py:221-265`) |

Observations that matter for porting:

1. **The debates are personas, not adversarial verification.** Each debater is told which conclusion to champion. No turn has to cite a specific piece of evidence, name a falsifier or say what would change its mind. The "risk team" is three rhetorical stances; **no risk calculation happens anywhere in the graph** (confirmed: no numeric risk code under `TA:tradingagents/agents/risk_mgmt/`).
2. **The Research Manager never sees the analyst reports.** Its prompt contains only `instrument_context` and the debate `history` (`research_manager.py:18-51`). Its judgement therefore depends on how faithfully the Bull and Bear relayed the evidence.
3. **Every debate turn re-sends all four reports in full**, plus the growing history (`bull_researcher.py:18-47`; `aggressive_debator.py:23-44`). This is the dominant cost term (§7).
4. **Structured output is rendered back to markdown and then re-parsed with regex.** `invoke_structured_or_freetext` returns `render(result)` as a string (`TA:agents/structured.py:73-81`). On any exception it falls back to a free-text call (`:82-89`). The final signal is extracted by `extract_rating` (`TA:agents/rating.py:49-78`) from the rendered text. The typed `PortfolioDecision` object is never returned to the caller. This is lossy, and it is the opposite of Fiboki's `sandbox.parse_payload` → schema → reject posture.
5. There are good defensive idioms (worth porting, see §8): `opponent_argument_or_opening` (`TA:agents/context.py:33-44`) and `report_or_absent` (`:180-191`), which give the model an explicit marker for an absent report instead of a blank one; and the `REVIEW` sentinel instead of defaulting to Hold (`rating.py:27-31, 81-93`).

### 1.4 Memory

- **Current mechanism:** `TradingMemoryLog`, a markdown file at `~/.tradingagents/memory/trading_memory.md` (`TA:tradingagents/decision_log.py:9-335`; path at `TA:default_config.py:75`).
  - Phase A: `store_decision` appends `[date | ticker | rating | pending]` plus the decision text (`decision_log.py:30-52`).
  - Phase B: `settle_pending` (`TA:graph/settlement.py:84-132`) fetches the close-to-close return over `holding_period_days=5` (`default_config.py:158`), computes alpha against a benchmark and calls the `Reflector` LLM for a 2–4 sentence lesson (`TA:graph/reflection.py:11-61`). The entry is then rewritten in place with `os.replace` (`decision_log.py:173-177, 226-230`).
  - Retrieval: `get_past_context` takes the last 5 same-ticker decisions (full text plus reflection) and 3 cross-ticker reflections (`:73-110`). They are injected **only into the Portfolio Manager** (`portfolio_manager.py:38-43, 63`).
  - Point-in-time filter: for historical runs, only lessons whose `resolved:` date is on or before the trade date are used (`decision_log.py:85-87`; `trading_graph.py:130-139`).
- **BM25 / ChromaDB:** **removed.** The paper-era `FinancialSituationMemory` (BM25) was replaced by the decision log (CHANGELOG `TA:CHANGELOG.md:369-372, 415-416`), and a test asserts its absence (`TA:tests/test_memory_log.py:859-872`). No vector store, no embeddings and no ChromaDB exist in the tree (grep: no matches outside the changelog and that test).
- **Not append-only in Fiboki's sense.** The docstring says "Append-only" (`decision_log.py:1, 10`), but settlement rewrites blocks, and rotation (`memory_log_max_entries`) deletes the oldest resolved entries (`:249-284`). There is no hash chain.

### 1.5 Data tools and vendors

Agent tools are LangChain `@tool`s in `TA:tradingagents/agents/tools.py:17-284`. Each dated tool receives the run's `trade_date` through `InjectedState` and clamps the model-supplied date to it with `as_of` / `as_of_window` (`TA:dataflows/date_window.py:72-95`). Routing goes through `route_to_vendor` (`TA:dataflows/router.py:184-291`), with vendor chains configured per category (`default_config.py:138-149`) and no silent fallback to unconfigured vendors (`router.py:195-209`).

| Vendor | Implementing file | Used by tool(s) | Key needed? | Point-in-time behaviour |
|---|---|---|---|---|
| Yahoo Finance (yfinance + direct `query2.finance.yahoo.com`) | `TA:dataflows/vendors/yahoo/{ohlcv,market,snapshot,fundamentals,news}.py` | `get_stock_data`, `get_indicators` (via `stockstats`), `get_verified_market_snapshot`, `get_fundamentals`, balance sheet/cashflow/income, `get_news`, `get_global_news`, `get_insider_transactions`, identity lookup | **No** | OHLCV cut at `curr_date`, stale-frame guard of 10 days (`ohlcv.py:18-21`). `Ticker.info` is withheld for past dates (`date_window.py:98-124`). News is recent-only; historical windows get a coverage-gap placeholder (`date_window.py:38-62`). |
| Alpha Vantage | `TA:dataflows/vendors/alpha_vantage/{common,stock,indicator,fundamentals,news}.py` | same categories (alternative vendor) | **Yes**, `ALPHA_VANTAGE_API_KEY` (`common.py:28-35`) | The key sits in the query string; errors are scrubbed (`TA:dataflows/net.py:6-24`). |
| SEC EDGAR XBRL | `TA:dataflows/vendors/sec_edgar.py` | balance sheet / cashflow / income (only if configured; the default is yfinance, `default_config.py:141`) | No key. SEC-mandated User-Agent `SEC_EDGAR_USER_AGENT`, which defaults to the placeholder `contact@example.com` (`:95-96`). | Serves facts as filed by the date, restatements at vintage (`:1-15`). **US filers only.** |
| FRED | `TA:dataflows/vendors/fred.py` | `get_macro_indicators` | **Yes**, `FRED_API_KEY` (`:93`) | Vintage-pinned realtime date (`:24-28`). |
| Polymarket Gamma | `TA:dataflows/vendors/polymarket.py` | `get_prediction_markets` | No | **Withheld for any past date**: "Polymarket serves only live odds… no historical vintage" (`:86-90`). |
| StockTwits | `TA:dataflows/vendors/stocktwits.py` | Sentiment pre-fetch | No | Recent stream only, trimmed to the window. |
| Reddit (RSS search) | `TA:dataflows/vendors/reddit.py` | Sentiment pre-fetch | No | Recent only, trimmed. Subreddits are wallstreetbets, stocks and investing (`sentiment_analyst.py:149-150`). |
| TypeSafe "Jev" | `TA:agents/post_screen.py:26, 67-97, 126-152` | Optional screening of social posts | `TYPESAFE_API_KEY` (optional; off without it) | Third-party classifier; sends each post's text to `api.typesafe.ai`. |
| Google News, Finnhub, EODHD, Bloomberg | — | — | — | **Not present in v0.5.1 code.** They are cited in the paper's dataset description (§5) but no vendor module exists. |

### 1.6 LLM providers supported

`TA:tradingagents/llm_clients/factory.py:36-56` covers native `anthropic`, `google`, `azure` and `bedrock` (the last via the optional `langchain-aws` extra, `pyproject.toml:38-40`). The OpenAI-compatible registry at `TA:llm_clients/openai_client.py:213-234` covers `openai` (Responses API), `xai`, `deepseek`, `qwen`, `qwen-cn`, `glm`, `glm-cn`, `minimax`, `minimax-cn`, `openrouter`, `mistral`, `kimi`, `groq`, `nvidia`, **`ollama`** (keyless, `OLLAMA_BASE_URL`) and a generic `openai_compatible` endpoint. Defaults are `llm_provider="openai"`, `deep_think_llm="gpt-6-sol"` and `quick_think_llm="gpt-6-luna"` (`default_config.py:81-83`).

### 1.7 Configuration surface

`TA:tradingagents/default_config.py:72-173` holds a single global dict with `TRADINGAGENTS_*` environment overrides (`:10-29`) that are type-coerced and fail loudly on bad values (`:36-69`). Keys: results and cache directories; memory log path and rotation; provider and models; `backend_url`; per-provider reasoning knobs; `temperature` (default `None`); `llm_max_retries`; `max_tokens`; `checkpoint_enabled` (default False); `output_language`; `max_debate_rounds=1`; `max_risk_discuss_rounds=1`; `max_recur_limit=100`; news limits and queries; `data_vendors` / `tool_vendors`; `holding_period_days=5`; `benchmark_ticker` / `benchmark_map`. The configuration is process-global with a per-run `ContextVar` overlay (`TA:dataflows/config.py:8-60`).

---

## 2. Licence, maintenance, dependencies, security

### 2.1 Licence

`TA:LICENSE` is Apache License 2.0, with the boilerplate `Copyright [yyyy] [name of copyright owner]` left unfilled (`LICENSE:189`). The licence is compatible with a proprietary Fiboki if we ported code with attribution and NOTICE handling. We are not proposing to port code verbatim, only patterns. The README disclaimer says it is "designed for research purposes… not intended as financial, investment, or trading advice" (`TA:README.md:74`).

### 2.2 Maintenance (measured from the clone; the clone is shallow)

- Commits in the last 90 days (since 2026-06-30): **140**, by month Jul 14 / Aug 16 / **Sep 110**. That is a burst around the 0.5.x releases.
- Author identity on those 140 commits: `Yijia-Xiao` 132, `Yijia Xiao` 6, `Tauric-Research` 1, `David Arias, CFA` 1. There are no `Co-authored-by` trailers. External contributions arrive as merged PR and issue numbers (e.g. `#1376`, `#1366`, `#1402`), but **commit authorship is effectively a single maintainer**. That is a bus-factor concern.
- Release tags in the clone: v0.3.0 (2026-06-22), v0.3.1 (07-05), v0.4.0 (08-31), v0.5.0 (09-18), v0.5.1 (09-24). The CHANGELOG lists releases back to 0.1.0 on 2025-06-05 (`TA:CHANGELOG.md:9-595`). v0.5.1 **moved import paths** (README line 35: "import paths moved"), so the API is not stable.
- Tests: **I ran them.** At 21:24Z, `pytest -q` in an isolated Python 3.11 venv printed **`1004 passed, 2 skipped, 22 warnings, 91 subtests passed in 24.43s`**. The skips were `langchain_aws` not installed and no live `DEEPSEEK_API_KEY`. CI runs Python 3.10–3.13 plus a non-UTC timezone job, a clean-install smoke test and strict ruff (`TA:.github/workflows/ci.yml`).
- GitHub page (via WebFetch, unverified precise figures): about 98.0k stars, 18.9k forks, 178 open issues, 180 open PRs. The contributor count could not be retrieved (the API was blocked for this session).

### 2.3 Dependency surface and conflicts with Fiboki's pins

Direct dependencies (`TA:pyproject.toml:11-28`) are all **floors, not pins**: `langchain-core>=0.3.81`, `langchain-anthropic`, `langchain-google-genai>=4.0.0`, `langchain-openai`, `langgraph>=0.4.8`, `langgraph-checkpoint-sqlite>=2.0.0`, `pandas>=2.3.0`, `python-dotenv`, `pytz`, `questionary`, `requests`, `rich>=14.0.0`, `typer>=0.21.0`, `stockstats`, `typing-extensions`, `yfinance>=1.4.1`. `requirements.txt` is just `.`.

**Measured conflict with Fiboki.** `uv pip compile -c deploy/constraints.txt` on `fiboki @ /home/claude/v2` plus `tradingagents @ clone` gives:

> `tradingagents==0.5.1 depends on pandas>=2.3.0 … fiboki==2.0.0 depends on pandas==2.2.3 … your requirements are unsatisfiable.`

Other hard conflicts, from the declared ranges: `rich>=14.0.0` vs Fiboki `rich==13.9.4`, and `typer>=0.21.0` vs `typer==0.15.1` (which Fiboki holds deliberately together with `click==8.1.8`, `FB:pyproject.toml` and `FB:deploy/constraints.txt`).

Resolving TradingAgents on its own (Python 3.11, today) produces **85 packages**, including `pandas==3.0.6`, `numpy==2.4.6`, `langgraph==1.2.12`, `langgraph-checkpoint==4.2.0`, `langgraph-checkpoint-sqlite==3.1.1`, `langchain-core==1.6.5`, `openai==3.20.0`, `anthropic==1.9.0`, `yfinance==1.7.0`, `curl-cffi`, `protobuf==7.36.2`, `sqlite-vec` and `tiktoken`. Every numerical library drifts from Fiboki's pins, which AGENTS.md §4 calls "always an error".

### 2.4 Security review

| Check | Finding |
|---|---|
| `eval` / `exec` / `compile` / `pickle` / `subprocess` / `os.system` / `shell=True` / `yaml.load` in first-party code | **None.** The only `compile(` hits are LangGraph `workflow.compile()` (`trading_graph.py:113, 199, 226`). |
| Transitive deserialisation risk | The checkpointer (opt-in, off by default, `default_config.py:110`) uses `langgraph.checkpoint.sqlite.SqliteSaver` (`TA:graph/checkpointer.py:14, 41-51`). Its serialiser has had RCE advisories: **CVE-2025-64439** (JsonPlusSerializer "json" mode RCE, `langgraph-checkpoint` < 3.0.0) and **CVE-2026-27794** (pickle fallback in the cache layer, < 4.0.0). There is also a SQL injection in the SQLite checkpointer's `list` filter, **CVE-2025-67644** (`langgraph-checkpoint-sqlite` < 3.0.1). The declared floor `langgraph-checkpoint-sqlite>=2.0.0` admits vulnerable versions. A fresh resolve today picks patched ones (4.2.0 / 3.1.1), but nothing prevents a stale lock. TradingAgents does not call `saver.list(filter=…)`, so the SQLi path is not obviously reachable. |
| Secrets | Read from the environment only. The CLI prompts for a missing key and **writes it to a `.env` in the current working directory** with mode 0600 (`TA:cli/prompts.py:588-639`, `set_key` at `:636`). Query-string keys are scrubbed from exception text (`TA:dataflows/net.py:6-24`). |
| Outbound hosts (all first-party URLs, grep) | LLM endpoints (`openai_client.py:214-227`); `query2.finance.yahoo.com`; `www.alphavantage.co`; `api.stlouisfed.org`; `gamma-api.polymarket.com`; `www.sec.gov`, `data.sec.gov`; `api.stocktwits.com`; `www.reddit.com`; **`api.typesafe.ai`** (third-party classifier, key-gated, sends post text, `post_screen.py:26, 86`); **`api.tauric.ai/v1/announcements`**, fetched by the CLI on every start (`TA:cli/config.py:3`, `TA:cli/announcements.py:16-34`, called at `TA:cli/selections.py:71`). The last is a vendor phone-home that renders remote content with Rich markup and can block for input (`require_attention`, `announcements.py:55-56`). The library path does not call it. |
| Prompt-injection surface | News, Reddit and StockTwits text is interpolated verbatim into prompts (`sentiment_analyst.py:131-154`), and tool-calling models act on it. The maintainers guard the filesystem consequence (ticker path traversal, `TA:dataflows/symbols.py:152-180`) but nothing else. No output from the graph is schema-constrained in a way that would contain an injected instruction; it is prose all the way down. |
| Determinism | None claimed. README `:355-371`: "Language model sampling is non-deterministic… no setting makes LLM output bit-identical." The default temperature is `None` (`default_config.py:98`). |

---

## 3. Asset-class fit

**It is an equities framework with an FX/commodity/index symbol translator added on.** I verified this by running the repo's own functions in the venv:

| Fiboki symbol | `normalize_symbol` (`TA:dataflows/symbols.py:103-144`) | Settlement benchmark (`TA:graph/settlement.py:13-35`) | CLI asset type (`TA:cli/prompts.py:79-85`) |
|---|---|---|---|
| EURUSD | `EURUSD=X` | **SPY** | stock |
| GBPJPY | `GBPJPY=X` | **SPY** | stock |
| XAUUSD | **`GC=F`** (COMEX front-month future, not spot) | **SPY** | stock |
| XAGUSD | `SI=F` | SPY | stock |
| DE40 | `^GDAXI` (cash index, not the CFD) | **SPY** | stock |
| US500 | `^GSPC` | SPY | stock |
| AU200 | **`AU200` (unmapped → no data)** | SPY | stock |
| FR40 | **`FR40` (unmapped)** | SPY | stock |
| WTIUSD | **`WTIUSD` (unmapped; only `WTI`, `WTICOUSD`, `USOIL` alias, `symbols.py:57`)** | SPY | stock |

So, for Fiboki's 41-instrument universe (measured: 20 FX crosses, 7 FX majors, 10 indices, 2 metals, 2 energy; `FB:src/fiboki/core/instruments.py`):

- **Every instrument's "alpha", and therefore every reflection lesson, would be measured against SPY.** The only suffix mapping is by exchange suffix (`default_config.py:160-171`). EURUSD vs SPY is not a meaningful alpha. Gold, as a futures contract, has roll gaps that spot XAUUSD does not.
- **Fundamentals are inapplicable**: `get_fundamentals`, balance sheet, cashflow, income statement, insider transactions and SEC EDGAR (US filers only) have no meaning for EURUSD, XAUUSD or DE40. The CLI drops the Fundamentals Analyst only for crypto (`TA:cli/prompts.py:88-97`). EURUSD is detected as "stock", so by default it would run a "company fundamentals" analyst.
- **Company identity** (`resolve_instrument_identity`, `TA:agents/context.py:57-96`) and every prompt are equity-worded: "growth potential, competitive advantages" (`bull_researcher.py:34-35`), "market saturation, … threats from competitors" (`bear_researcher.py:35-36`), "financial documents, company profile" (`fundamentals_analyst.py:28`).
- **StockTwits and Reddit** search by cashtag/ticker. `EURUSD=X` chatter on r/wallstreetbets is noise at best. The Jev screen asks "which way does it lean on the instrument's **stock**" (`post_screen.py:52-57`).
- **Reasonably applicable:** `get_stock_data` (OHLCV), `get_indicators`, `get_verified_market_snapshot`, FRED macro (policy rates, yields, CPI; highly relevant to FX), Yahoo global news (the query list already includes "ECB Bank of England BOJ central bank policy", `default_config.py:126-132`), and Polymarket (live only).
- **Intraday**: the framework is daily. Everything is keyed on `YYYY-MM-DD` (`trading_graph.py:29-40`), the holding period is 5 *trading days*, and there is no timeframe concept at all. Fiboki's strategies run on multiple timeframes with closed-candle evaluation.

**Adapting it to EURUSD/XAUUSD/DE40 with no fundamentals** would need, at minimum: an FX/macro analyst replacing Fundamentals (rate differentials, central-bank calendar, CFTC positioning, which it has no vendor for); asset-class-aware prompts for all eight debate/decision agents; a benchmark map per asset class (cash rate or carry-adjusted zero for FX, the underlying index for index CFDs, spot for gold); dropping or replacing StockTwits/Reddit; spot/CFD price sources instead of Yahoo futures and cash indices; and a timeframe dimension. In practice that is a rewrite of every prompt and half the data layer, with LangGraph retained as ballast.

---

## 4. Decision output: what the final node emits, and whether anything can execute

**Final node:** `Portfolio Manager` → `final_trade_decision`, a markdown string rendered from `PortfolioDecision`:

```
**Rating**: {Buy|Overweight|Hold|Underweight|Sell}
**Executive Summary**: …   ("entry strategy, position sizing, key risk levels, and time horizon")
**Investment Thesis**: …
**Price Target**: {float|not provided}
**Time Horizon**: {str|not provided}
```

(`TA:agents/schemas.py:221-288`; the description of `executive_summary` at `:240-245`).

**What `propagate` returns** is only the 5-tier string, or `"REVIEW"` when no rating can be parsed (`trading_graph.py:167-171, 344, 388-390`; `rating.py:81-88`).

**Sizes and stops** exist only upstream, in the Trader's `TraderProposal`: `entry_price: float|None`, `stop_loss: float|None` and `position_sizing: str|None` (free text, *"e.g. '5% of portfolio'"*, `schemas.py:164-183`). They are rendered as `FINAL TRANSACTION PROPOSAL: **BUY/HOLD/SELL**` (`schemas.py:211`). Nothing validates that a stop is on the correct side of entry, that a size is feasible, or that a stop exceeds a broker minimum. A percentage stop is silently set to `None` (`schemas.py:33-58`).

**Execution path: none.** A grep across the tree finds no broker SDK, order object, `place_order` / `submit_order` or venue adapter. The only "broker" hits are the symbol-normalisation comments. The code states it plainly:

> "Scope: this evaluates decision quality. It is not a portfolio simulator, and must not grow one. Turning a rating into a filled order needs a quantity, a fill price and a cash ledger, none of which the system has; inventing them here would put an execution model behind an evaluation tool." — `TA:tradingagents/backtest.py:9-14`

> "Broker-neutral by construction: quantities are generic units … so nothing here implies a venue or an execution path." — `TA:tradingagents/portfolio.py:9-11`

So TradingAgents is **purely advisory**. **However, its prompts are written as if the output will be executed**: "deliver a clear, actionable investment plan for the trader" (`research_manager.py:23`); "Based on your analysis, provide a specific recommendation to buy, sell, or hold" (`trader.py:52-53`); "the call and how to act on it" (`portfolio_manager.py:76`). That framing is incompatible with Fiboki's `CARDINAL_RULE` ("do not phrase a research output as an instruction to trade", `FB:src/fiboki/agents/roles.py:46-57`). No prompt text should be copied.

---

## 5. Evaluation evidence

### 5.1 The paper's backtest (arXiv:2412.20138 v7, HTML read via WebFetch)

| Item | What the paper says | Assessment |
|---|---|---|
| Window | "from January 1st to March 29th, 2024" | **About 3 months**, one regime (a strong large-cap tech rally). |
| Universe | Text: Apple, Nvidia, Microsoft, Meta, Google, Amazon. **Results table: AAPL, GOOGL, AMZN only.** | Three reported tickers, chosen with hindsight from the same sector. No survivorship control is discussed. |
| Models | Quick: `gpt-4o-mini`, `gpt-4o`. Deep: `o1-preview`. | Closed models that have since been retired or rotated, so the results cannot be re-run on the same weights. |
| Trials / repeated runs | Not stated. No seeds, no repeated sampling, no confidence intervals, no significance tests. | One sample path per ticker from a stochastic system. |
| Costs / slippage / sizing | Commission, slippage and position sizing are not specified. | Gross returns only. |
| Benchmarks | Buy & Hold, MACD, KDJ+RSI, ZMR, SMA | Weak rule baselines. No passive factor or sector benchmark beyond B&H of the same stock. |
| Debate rounds | "n rounds, as determined by the debate facilitator agent" | n not disclosed. |
| Headline results | AAPL CR 26.62%, SR **8.21**, MDD 0.91%; GOOGL CR 24.36%, SR 6.39; AMZN CR 23.21%, SR 5.60 | A Sharpe of 5–8 over 3 months on a single stock is not a credible estimate. The authors attribute it to "few pullbacks" in the period, which concedes regime dependence rather than addressing it. |
| Look-ahead | "Agents make decisions based solely on data available up to each trading day, ensuring no future data is used (eliminating look-ahead bias)." | Addresses **data** leakage only. |
| **LLM training-data contamination** | **Not discussed.** No knowledge-cutoff analysis, no anonymisation, no post-cutoff hold-out. | To be fair, gpt-4o and o1-preview had published knowledge cutoffs in late 2023 (my recollection of vendor documentation; not verified in this session), so Jan–Mar 2024 was probably just after their cutoffs. The paper does not make this argument, and it does not rule out knowledge from later fine-tuning or retrieval. **It does not generalise**: any re-run of that window with today's defaults (`gpt-6-sol/luna`, `default_config.py:82-83`) sees a model trained well after March 2024. |
| Limitations section | None. | — |

**Distinguishing claims from evidence.** The abstract claims "superiority over baseline models, with notable improvements in cumulative returns, Sharpe ratio, and maximum drawdown". The evidence is one gross-of-cost sample path on each of three hand-picked stocks over one quarter, with no uncertainty quantification. Against Fiboki's standards (minimum trade counts, DSR/PBO deflation, honest trial count, cost stress at 2× spread), **this would not pass rung 0**. The claim is untested, not refuted.

### 5.2 What the code itself says about its evidence

The maintainers are more candid than the paper:

- "Backtest results are not guaranteed to match any published figure… Treat the framework as a research scaffold… not as a strategy with a fixed, replicable return." (`TA:README.md:371`)
- The in-repo evaluator reports only hit rate and mean alpha by rating, and says: "One model sampling per cell, and text feeds are not archived, so these figures are indicative rather than repeatable." (`TA:tradingagents/backtest.py:120-124`)
- Alpha is stored rounded to 0.1 pp (`backtest.py:64-74`).
- Historical runs are starved by design. News, social and Polymarket are withheld or placeholdered for past dates (`date_window.py:38-62, 98-124`; `polymarket.py:86-90`; `sentiment_analyst.py:10-13`). This is correct for data leakage, but it means **a historical backtest of v0.5.1 is not the system that runs live**: live runs see news and social feeds, historical runs mostly see OHLCV, FRED and EDGAR. Backtest–live parity fails by construction.
- **Parametric leakage is not addressed anywhere in the code.** A grep for contamination, knowledge cutoff, training data or memorisation finds nothing relevant. The model is told the date (`"Today's date is {current_date}; treat it as 'now'"`, `market_analyst.py:174`) and the real ticker and company name (`context.py:99-159`), which is exactly the cue that lets a model recall what happened next.

### 5.3 Independent replications and critiques (WebSearch / WebFetch)

- **Li, Kim, Cucuringu, Ma (2025), "Can LLM-based Financial Investing Strategies Outperform the Market in Long Run?"**, arXiv:2505.07078, evaluates FinMem, FinAgent **and TradingAgents**: "most evaluations of LLM timing-based investing strategies are conducted on narrow timeframes and limited stock universes, overstating effectiveness"; "Systematic backtests over two decades and 100+ symbols reveal that previously reported LLM advantages deteriorate significantly"; "LLM strategies are overly conservative in bull markets… and overly aggressive in bear markets." This is the strongest independent evidence, and it is negative.
- **Yao and Zheng, "Beyond Agent Architecture: Execution Assumptions and Reproducibility in LLM-Based Trading Systems"**, arXiv:2606.08285. Audits 30 LLM trading studies including TradingAgents ("agent roles explicit; protocol transparency is mixed"). Finds execution timing is "often present only at a narrative level", only 18/30 had recoverable artefacts, and 10–25 bp costs remove a meaningful share of gross edge.
- **Nguyen and Pham, "Toward Reliable Evaluation of LLM-Based Financial Multi-agent Systems"**, PAKDD 2026 (Springer). Cites TradingAgents. Names "transaction cost neglect" among five pervasive evaluation failures, alongside look-ahead, survivorship, backtest overfitting and regime-shift blindness. Its "coordination primacy" hypothesis is explicitly **not** empirically validated.
- **Li, Wang, Ma, "Summoning the Oracle to Slay It: Mitigating Look-Ahead Bias in Financial Backtesting with LLMs"**, arXiv:2605.24564. Defines "parametric look-ahead bias": "An LLM trained in 2024 may already encode how stocks moved during 2018–2020", which is "invisible to data-pipeline audits." It does not name TradingAgents, but applies directly.
- **`Kantamaniprakash/trading-agents-lab`** (GitHub; 0 stars, 13 commits, **unvetted, anecdotal only**). A reproduction with anonymisation (masked ticker and dates, rebased prices) and 10 bp costs. It reports that an anonymised AAPL run over 64 trading days in 2026 lost 0.9% (Sharpe −1.21) against buy-and-hold +15.7%. One path, one ticker, so this is no stronger evidence than the paper. It is cited only to show that the obvious control (anonymisation) has been tried and did not reproduce the effect.

I found no peer-reviewed replication that confirms the paper's results.

### 5.4 Consequence for Fiboki

Because contamination lives in the weights, **any historical backtest of an LLM conviction signal over a window before the model's training cutoff is inadmissible** under Fiboki's rules. "Would this change let a number be believed that should not be?" (AGENTS.md §6): yes. The only honest evaluation is **forward**: record convictions in real time (Provenance `SHADOW`, `FB:src/fiboki/core/enums.py:136-145`), freeze them in the append-only store, and compare later. Anonymisation (as in 5.3) is a weaker partial mitigation for historical studies and must be reported as such.

---

## 6. Mapping onto Fiboki, and a concrete design

### 6.1 What Fiboki already has (verified in code this session)

- **19 capabilities**: 12 reads, 6 research writes and `SUBMIT_JOB` (`FB:src/fiboki/agents/capabilities.py:42-64`; `len(Capability)` printed 19). Note: `FB:docs/v2/AI_AGENT_ARCHITECTURE.md:37` says "20 members". That is a documentation error to fix.
- **25 tools** (`len(REGISTRY.names())` = 25), **12 roles** (`FB:agents/roles.py:30-42`), **8 job types** with no execution job (`FB:agents/orchestrator.py:50-65`), and **7 write domains** (`FB:agents/tools.py:77-90`).
- **Twelve roles:** `research_director`, `quant_researcher`, `strategy_engineer`, `strategy_mutation_agent`, `statistical_auditor`, `adversarial_quant_critic`, `market_regime_analyst`, `execution_analyst`, `portfolio_analyst`, `data_quality_analyst`, `failure_investigator`, `research_librarian` (`FB:agents/roles.py:127-476`).
- **Providers** (`FB:agents/providers.py`): `EchoProvider` (offline, scripted or digest, `:198`), `LocalHTTPProvider` (Ollama / llama.cpp, default `http://127.0.0.1:11434`, cost 0, `:284-300`), `OpenAICompatibleProvider` (`:362`), `AnthropicProvider` (`:425`). `ModelRouter` sorts by `(estimated cost, not local, name)` (`:525-533`). The remote adapters are inert without an injected HTTP client. Per the architecture doc §10, **no agent has yet run against a real model**.
- **Workflows**: `run_research_cycle` (Director → Researcher → Mutation → Auditor → queued backtest/validation → Critic → Librarian, `FB:agents/workflows.py:134-433`) and `run_failure_investigation` (read-only, `:436-491`).
- **Deterministic market state**: regime vector, cross-asset (currency strength, risk appetite, lead–lag), calendar blackouts (`FB:src/fiboki/marketstate/__init__.py:1-13`). It is exposed to agents via `query_regime`, which delegates wholesale to `marketstate` (`FB:agents/tools.py:556-565`).
- **Portfolio construction** already multiplies size by a regime scalar (`trend 1.0, range 0.7, high_vol 0.5, crisis 0.25, unknown 0.6`, `FB:src/fiboki/portfolio/construction.py:247-256`). This is step 12 of a 12-step multiplicative `PIPELINE` (`:507-520`), and every factor is recorded as an `AllocationReason` (`:560-572`).

### 6.2 Pattern-by-pattern comparison

| TradingAgents element | Fiboki equivalent today | New or redundant |
|---|---|---|
| Market / technical analyst (LLM chooses indicators, writes prose) | `marketstate` features and regime classifier (deterministic, causal-tested), plus the `market_regime_analyst` role, which is told to quote thresholds rather than invent them (`roles.py:334-353`) | **Redundant, and worse.** Fiboki must not let an LLM compute or select indicators for a signal (project rule: indicators centralised). |
| News analyst / macro | `search_web` and `fetch_research` are **stubs** (`tools.py:338-360`). The calendar is file-backed with no dated events (AGENTS.md §2). | **Gap.** This is the only place an LLM debate adds information Fiboki lacks: interpreting text. |
| Sentiment (StockTwits/Reddit) | None | Not wanted for FX, indices or gold (§3). |
| Fundamentals | None | Inapplicable. |
| Bull vs Bear debate | `adversarial_quant_critic` attacks *strategies and backtests*, with falsifiable objections enforced by a tool that rejects generic caution (`tools.py:1953-2005`) | **Partly new.** Fiboki debates *research artefacts*, not *market theses*. A thesis debate is new, but it should inherit the critic's discipline: each claim needs evidence refs and a decisive test. |
| Research Manager (judge) | None for market theses | New role, but it must **not** see only the debate transcript (TA's defect, §1.3.2). It judges against the deterministic brief. |
| Trader (entry/stop/size) | ALPHA (`Signal`) and PORTFOLIO (`size_trade`) are deterministic and single-call-site | **Must not be ported.** It would be a second, LLM-based sizer (AGENTS.md §1: "Never re-derive a position size"). |
| Risk debate (aggressive/conservative/neutral) | `risk/` gateway, limits and kill switch (deterministic), plus construction de-risking | **Must not be ported** as risk. At most it is a rhetorical stress-test of the thesis. |
| Portfolio Manager final rating | None | Must not exist as an order authority. The replacement is a bounded conviction artefact (below). |
| Reflection memory (5-day alpha lesson) | `research/memory.py`, append-only `ResearchStore` (`FB:src/fiboki/research/artefacts.py:312-340`) | The idea of scoring past theses forward is useful. The implementation (in-place markdown rewrite, SPY alpha, LLM-written lessons fed back into the next decision) is not. |

### 6.3 Design: `MarketBrief` → `ThesisDebate` → `ConvictionReading`

**Principle.** The LLM argues over a **deterministic evidence pack**. Its only output that anyone downstream may read is a **schema-validated, evidence-referenced, expiring conviction artefact**. The portfolio layer reads that artefact through a **policy-owned, down-only, capped scalar**. It is never an order, never a direction and never a size.

#### (a) `MarketBrief` — deterministic, no LLM

- A new read tool `build_market_brief(instrument, timeframe)` with `Capability.READ_REGIME` and `WriteDomain.NONE`. It assembles:
  - the regime vector and axes, with classifier and feature fingerprints (from `query_regime` internals);
  - cross-asset currency strength and risk appetite;
  - calendar blackout windows;
  - data-quality status;
  - recent bar statistics.
- Every field gets a stable `evidence_id` (e.g. `regime.axes.volatility`, `xasset.ccy_strength.EUR`).
- Optional text items from a point-in-time news provider, once `search_web` is real, each carrying `published_at <= as_of`.
- **`as_of` is pinned by the `ToolContext`, not supplied by the model.** Today `QueryRegimeIn.as_of` is model-controlled and defaults to the last bar (`FB:agents/tools.py:531, 583`), and `ToolContext` has no clock (`:362-391`). Port TradingAgents' `InjectedState(trade_date)` + `as_of` clamp idea (`TA:agents/tools.py:22, 34`; `TA:dataflows/date_window.py:72-95`) as a `ToolContext.as_of` that every dated tool clamps to.
- The brief is persisted as a research artefact with its content hash, so a debate is reproducible from the record (the stateless-orchestrator principle, AI_AGENT_ARCHITECTURE §6).

#### (b) `ThesisDebate` workflow (`workflows.run_thesis_debate`)

New roles, each with a narrow tool bundle and the standing `CARDINAL_RULE` and `EPISTEMIC_STANDARD`:

1. `thesis_advocate` (instantiated twice, `stance=long|short`; one role spec with the stance as input, to avoid role proliferation). Tools: `get_market_brief`, `record_debate_turn`.
2. `thesis_arbiter`. Tools: `get_market_brief`, `get_debate`, `record_conviction`. It reads **the brief itself as well as the transcript** (fixing TA §1.3.2).
3. Reuse `adversarial_quant_critic` optionally, for one attack on the arbiter's reading.

Tool schemas (pydantic, `extra="forbid"`, via `sandbox.parse_payload` → validation):

```python
class DebateTurnIn(_In):
    debate_id: str
    stance: Literal["long", "short"]
    round: int = Field(ge=1, le=2)                       # hard cap; cost is superlinear in rounds (§7)
    claims: tuple[ClaimIn, ...] = Field(min_length=1, max_length=5)

class ClaimIn(_In):
    statement: str = Field(min_length=20, max_length=600)
    evidence_ids: tuple[str, ...] = Field(min_length=1)  # must resolve in the brief, or refused
    falsifier: str = Field(min_length=20)                # what observation would kill this claim
    rebuts: str | None = None                            # claim id of the opponent's claim

class ConvictionIn(_In):
    debate_id: str
    brief_hash: str
    stance: Literal["long", "short", "none"]
    strength: Literal[0, 1, 2]                           # ordinal, not a probability; no floats to over-read
    decisive_evidence_ids: tuple[str, ...] = Field(min_length=1)
    invalidated_if: tuple[str, ...] = Field(min_length=1)
    valid_until: str                                     # <= next session boundary; enforced by the tool
```

- The handlers reuse the critic's generic-caution filter (`_VAGUE_PHRASES`, `FB:agents/tools.py:1976-2000`). They refuse unknown `evidence_ids`, and they refuse `valid_until` beyond the policy TTL.
- Everything is written to new write domains `RESEARCH_DEBATE` and `RESEARCH_CONVICTION` with capabilities `WRITE_DEBATE_TURN` and `WRITE_CONVICTION`. Both names pass `assert_no_execution_capability`, because the verb is WRITE and the nouns are not execution nouns. **A conviction is a research artefact, and nothing more.**
- Each step is one `AgentSession.think` plus one `.call`. The audit chain therefore holds, for every turn, the prompt, the model, tokens, cost and the parent action (`FB:agents/audit.py:83-112`). Reading the ledger by `workflow_id` reconstructs the debate.
- A step that fails to parse is recorded as failed, and the workflow carries on (the existing convention, `workflows.py:170-177`). **A failed debate produces no conviction**, and no conviction is the neutral state.

#### (c) The only bridge to the portfolio: `ConvictionOverlay`

The layering forbids `portfolio` (rank 60) importing `research` (95) or `agents` (`FB:tests/unit/test_layering.py`). So:

1. **Contract in `core/contracts.py`** (rank 0): `ConvictionReading(instrument, stance, strength, as_of, valid_until, artefact_id, policy_version)`, frozen, containing no text.
2. **Policy, not the model, maps it to a scalar**, in a `ConvictionPolicy` held in `ConstructionConfig` (versioned and stamped on every result, `construction.py:212-216`):
   - `enabled: bool = False` by default;
   - only **disagreement dampens**: if `stance` opposes the candidate `Signal`'s direction with `strength == 2`, the factor is `floor` (e.g. 0.75). With strength 1, the factor is e.g. 0.9. Otherwise it is `1.0`;
   - **it never increases size** (`factor <= 1.0` is asserted in `__post_init__` and in a property test);
   - a missing, stale (`as_of > valid_until`) or unapproved reading gives `1.0`, so an LLM outage cannot change risk. Note this differs from `regime_scalars["unknown"]=0.6`: here "unknown" must equal the pre-feature baseline, or availability of a third-party API would modulate book risk;
   - `floor >= 0.5`, so the LLM can at most halve the size and can never drop a candidate.
3. **A new `_step_conviction`**, appended after `regime` in `PIPELINE` (`construction.py:507-520`), recording an `AllocationReason("conviction", factor, "artefact=… stance=… strength=…")`. The composition root (`FB:src/fiboki/workers/runtime.py`, which already injects `regime_source` at `:417, 857`) supplies `conviction_source: Callable[[str], ConvictionReading | None]`. The runtime reads the latest approved artefact **by id**, so the audit trail links the allocation to the debate.
4. Nothing reaches `Signal.confidence` (ALPHA), `size_trade`, the risk gateway or execution. An AST test asserts that `ConvictionReading` is constructed only in the runtime adapter and consumed only in `_step_conviction`.

#### (d) Evaluation gate before `enabled=True`

- **Shadow first.** Compute the would-be factor on every allocation and log it with `Provenance.SHADOW` for at least N months, or until at least 80 affected trades, whichever is later (the project minimum for ranking).
- Then compare realised outcomes of dampened vs undampened trades with a pre-registered test, using `design_experiment` before data is seen.
- Declare the trial count honestly (number of policy variants tried).
- **No historical backfill of convictions** (§5.4). If a historical study is wanted, it must use anonymised briefs and be labelled as contaminated-risk evidence that cannot promote.

### 6.4 Why this shape rather than the TradingAgents shape

- It keeps the LLM's contribution **falsifiable and inspectable**: every claim points at a brief field and names a falsifier.
- It keeps all numbers the LLM might "hallucinate" out of the chain: there are no prices, stops or sizes in any schema.
- It makes the bridge **policy-owned, bounded and asymmetric**, so the worst case is a smaller position on a trade the deterministic system already chose.
- It is additive to the existing five mechanisms. It needs no new job type, and no execution noun enters any enum.

### 6.5 What the debate cannot fix

With only deterministic inputs (no news source), the debate is an expensive re-description of numbers Fiboki already computes. The regime scalar already consumes those numbers directly. **The pattern has positive expected information value only once a point-in-time text source exists.** Otherwise it is theatre with an audit trail. That is why the news provider is listed as a prerequisite, not an option.

### 6.6 Effort estimate (engineer-days)

| Work item | Days |
|---|---|
| `ToolContext.as_of` pinning plus clamping in all dated read tools, with tests | 1.5 |
| `build_market_brief` read tool, evidence-id scheme, artefact persistence with hash | 2.0 |
| New artefact types (`DebateTurn`, `Conviction`) in `ResearchStore` with append-only triggers; new `WriteDomain` members and capabilities; guard tests | 1.5 |
| `record_debate_turn` / `record_conviction` tools (evidence-id resolution, vague-phrase filter, TTL) | 2.0 |
| Two role specs (`thesis_advocate` with stance param, `thesis_arbiter`); bundle validation | 1.0 |
| `run_thesis_debate` workflow, `EchoProvider` scripts, integration test that replays the audit chain | 2.0 |
| `ConvictionReading` contract, `ConvictionPolicy`, `_step_conviction`, golden arithmetic test, down-only property test | 1.5 |
| Runtime `conviction_source` wiring; AST tests (single construction site, no agents↔portfolio edge) | 1.0 |
| Shadow logging (`Provenance.SHADOW`) and a comparison report | 1.5 |
| Docs (AI_AGENT_ARCHITECTURE, PORTFOLIO_RISK_STANDARD), operator view | 1.0 |
| **Total** | **≈ 15 (range 12–20)** |
| Point-in-time news provider behind `search_web` / `fetch_research` (prerequisite for value) | +3–5 |
| Forward shadow period | calendar months, not engineer-days |

---

## 7. Cost model

### 7.1 Measured structural token counts

I instrumented a full graph run with a scripted chat model and no network, reusing the fixture pattern from `TA:tests/test_graph_end_to_end.py` (script at `/tmp/claude-0/…/scratchpad/tokmodel/measure.py`). The script counts every LLM call and the characters sent and received, under synthetic report and tool-output sizes. Tokens are taken as characters ÷ 4.

**This is a lower bound on analyst tool loops.** The scripted model issues all of an analyst's tools in one parallel turn, whereas real models often take 3–6 sequential turns, each re-sending the growing history. It also excludes provider-side tool-schema tokens and hidden reasoning tokens.

| Scenario | Rounds (debate / risk) | LLM calls | Input tokens | Output tokens |
|---|---|---|---|---|
| Lean (reports ~1.5k tok, tool outputs ~1k tok, social ~0.75k tok per block) | 1 / 1 | 15 | ~83k | ~18k |
| **Base** (reports ~2.5k, tools ~2k, social ~1.5k) | **1 / 1** | **15** | **~134k** | **~29k** |
| Base | 2 / 2 | 20 | ~267k | ~42k |
| Base | 3 / 3 | 25 | ~432k | ~54k |
| Heavy (reports ~4k, tools ~4k, social ~2.5k) | 1 / 1 | 15 | ~216k | ~47k |

Two points follow. First, input grows **superlinearly in rounds** (×2.0 at 2 rounds, ×3.2 at 3), because every turn re-sends all four reports plus the accumulated history. Second, in the base run the single largest calls are the three risk debaters (about 12k, 17k and 22k input tokens) and the Portfolio Manager (about 12k). The deep model handles only about 17.5k input and 5k output tokens, split between the Research Manager and the Portfolio Manager. Settlement adds one small reflection call per pending prior decision.

### 7.2 Cost per run and per instrument-day

The prices are **illustrative list-price tiers per 1M tokens (input/output); assumptions, not quotes for the current default models.**

| Tier | Quick model | Deep model | Base run, r=1 | With 2× output for hidden reasoning |
|---|---|---|---|---|
| A (small) | $0.15 / $0.60 | $2.50 / $10 | **$0.13** | $0.19 |
| B (mid) | $2.50 / $10 | $3 / $15 | **$0.66** | $0.98 |
| C (frontier) | $3 / $15 | $15 / $75 | **$1.36** | $2.10 |

At one run per instrument per day, on Fiboki's **41 instruments**, over roughly 260 trading days:

- Tier A: about $5–8 per day, **about $1.4–2.0k per year**.
- Tier C: about $56–86 per day, **about $15–22k per year**.
- At 3 rounds: multiply by about 2.5–3.
- A 3-year historical sweep over 41 instruments is about 32k runs, or **$4k (A) to $67k (C)** at r=1. It is also scientifically inadmissible (§5.4).
- Each **Fiboki role session is capped at USD 0.25** (`FB:agents/roles.py:81`). A TradingAgents-shaped run blows through that at tiers B and C. The proposed `ThesisDebate` is budgeted to fit: a brief of about 2–3k tokens (not four 2.5k reports), 2 advocates × ≤2 rounds with capped claims, and 1 arbiter. That is roughly 20–30k input and 4–6k output per instrument-day, **about 5× cheaper than TA's base run**.
- Prompt caching of the shared brief would roughly halve the input cost further (assumption: the provider supports prefix caching).

### 7.3 Are local models viable?

- **For the TradingAgents graph as-is: marginal.** Its analyst turns reach about 40–60k-token contexts. The tool loops depend on reliable function calling, which is exactly where the maintainers have had to add guards against hallucinated tool calls from weaker models (`TA:agents/structured.py:31-39`; `NO_EXTERNAL_TOOLS`). Assume a local 30B-class model on Apple-silicon hardware at about 300–800 tok/s prompt processing and about 20–40 tok/s generation (**assumption, not measured here**). A 134k-in / 29k-out run then takes roughly 15–30 minutes, which is 10–20 hours for 41 instruments daily on a single machine.
- **For the proposed `ThesisDebate`: plausible.** It needs no tool loop (the brief is injected), a JSON-only output under Fiboki's `sandbox` parser, and about 25k input per instrument. That is roughly 2–4 minutes per instrument, or 1.5–3 hours per day for 41 instruments, which fits overnight on one machine. It matches `LocalHTTPProvider` and the router's local-first preference (`FB:agents/providers.py:284-300, 525-533`).
- It has not been measured. Per AI_AGENT_ARCHITECTURE §10, **no agent in Fiboki has yet run against any real model**, local or remote. The first step is a timed local run.

---

## 8. What NOT to take (and what small idioms to take)

**Do not take:**

1. **Any code dependency**: LangGraph, LangChain, `langgraph-checkpoint-sqlite` (with its CVE history, §2.4), yfinance or stockstats. The pins are unsatisfiable with Fiboki (§2.3), and LangGraph's long-lived graph state is the "conductor" the Fiboki architecture deliberately rejected (AI_AGENT_ARCHITECTURE §6).
2. **The Trader node and the `TraderProposal` schema.** LLM-authored entry, stop and "5% of portfolio" sizing (`TA:agents/schemas.py:164-183`) is a second sizer and a stop generator outside `Signal` validation.
3. **The risk "team"** as anything called risk. It contains no calculation.
4. **The Portfolio Manager's 5-tier rating as a signal**, and "actionable" or "how to act on it" prompt framing (§4).
5. **Structured-output-then-render-then-regex** (`structured.py:59-89`; `rating.py:49-78`), and the free-text fallback on any structured failure. Fiboki's rule is reject, not degrade.
6. **Reflection lessons injected back into the decision-maker** (`portfolio_manager.py:38-43`). This is a feedback loop of LLM-written text judged on a 5-day, SPY-relative, single-path outcome. It trains the prompt on noise, and it mutates the "append-only" log in place (`decision_log.py:173-177, 249-284`).
7. **Equity-specific vendors and prompts**: fundamentals, insider transactions, EDGAR, StockTwits, Reddit, the Jev screen, and company-identity anchoring.
8. **Sending the real ticker, company name and date to the model in any historical run.** This is the contamination cue (§5.2).
9. **CLI conveniences**: the announcements phone-home (`TA:cli/announcements.py:16-34`), and writing API keys to `.env` in the working directory (`TA:cli/prompts.py:630-637`).
10. **The paper's performance figures**, in any document, as evidence.

**Worth porting, as idioms and not code:**

- **Explicit-absence markers** in prompts: `report_or_absent` ("it is not available, not an empty finding", `TA:agents/context.py:180-191`) and `opponent_argument_or_opening` (`:33-44`). They fit Fiboki's "an absent result is not a zero result" and belong in prompt builders.
- **Tool-date clamping to the run's as-of** (`TA:agents/tools.py:22, 34`; `date_window.py:72-95`), implemented as `ToolContext.as_of` (§6.3a). **This closes a real gap in Fiboki today.**
- **Withholding live-only feeds from historical runs**, with a message that states why (`date_window.py:98-124`; `polymarket.py:86-90`), and **coverage-gap placeholders** for feeds that serve only recent items (`date_window.py:38-62`).
- **A `REVIEW` sentinel instead of defaulting to Hold** when output cannot be read (`rating.py:27-31`). Fiboki's sandbox already rejects, but the idea should carry into the conviction policy: unparsed means no conviction, not neutral stance.
- **A graph-shape signature in the idempotency key** (`TA:trading_graph.py:141-155`): a changed role set, round count or brief schema version must not reuse a prior debate's key.
- **Scrubbing secrets out of URL-bearing exceptions** (`TA:dataflows/net.py:6-24`), for when Fiboki wires real web providers.

---

## 9. Verdict

**PORT-PATTERNS (narrow), otherwise REFERENCE-ONLY. Reject code integration.**

Justification:

1. **Code: reject.** The dependencies are unsatisfiable with Fiboki's exact pins (measured). The framework is equities-first in every prompt and in most tools (§3). The output is prose, and its typed objects are discarded (§1.3.4). Integrating the code would add a second orchestrator with its own state model inside a platform whose central design choice is statelessness and a single enforcement point.
2. **Evidence: none admissible.** The only backtest is one quarter, three tickers, gross of costs, one sample path, with no contamination analysis. Independent long-horizon work finds the advantage does not persist (§5.3). Fiboki must not treat "multi-agent debate improves trading" as a premise.
3. **Patterns: yes, bounded.** The debate shape is a useful way to *organise* qualitative judgement, provided it is re-expressed as Fiboki tools with evidence references, falsifiers, an append-only audit trail, and a policy-owned, down-only, capped, expiring, off-by-default scalar at the portfolio layer (§6.3). Its value is conditional on a point-in-time text source, and it can be established only by forward shadowing.

---

## 10. Facts vs assumptions

**Verified in this session (code read or command run):**

- Graph nodes and edges, routing counts, and quick/deep model assignment (`TA:graph/setup.py`, `conditional_logic.py`). The default is one Bull turn, one Bear turn and one turn per risk debater.
- The Research Manager prompt excludes the analyst reports. The Portfolio Manager is the only consumer of memory lessons.
- BM25 and ChromaDB have been removed. Memory is a markdown decision log with in-place rewrite and rotation.
- The vendor list, key requirements and point-in-time guards (§1.5). Google News, Finnhub and EODHD are absent from the code.
- The LLM provider registry (§1.6).
- No execution path, and no eval/exec/pickle/subprocess, in first-party code.
- Outbound hosts, including `api.typesafe.ai` and `api.tauric.ai`.
- Apache-2.0 licence.
- 140 commits in 90 days, author identity dominated by one maintainer, and the tag dates listed.
- TradingAgents tests: **1004 passed, 2 skipped** (run at 21:24Z, Python 3.11 venv).
- **Dependency conflict with Fiboki is unsatisfiable** (`uv pip compile`). A standalone resolve gives 85 packages including pandas 3.0.6 and numpy 2.4.6.
- The FX, index and metal symbol mapping, SPY benchmark and "stock" classification for Fiboki symbols (the repo's functions, executed).
- Token counts and call counts from the instrumented scripted-model runs (§7.1), as a lower bound under stated synthetic sizes.
- Fiboki facts: 19 capabilities (the doc says 20, so the doc is wrong), 25 tools, 12 roles, 8 job types, 7 write domains, 41 instruments, the construction pipeline and regime scalars, the per-role $0.25 budget, `QueryRegimeIn.as_of` being model-controlled, and `ToolContext` having no clock.

**From external sources (read via WebFetch, not independently re-verified):**

- The paper's window, tickers, models, baselines, headline metrics, the absence of cost, trial and contamination discussion, and the absence of a limitations section. These were extracted by the fetch tool's summariser from the arXiv HTML page, so exact figures should be spot-checked against the PDF before being quoted externally.
- The findings of arXiv:2505.07078, arXiv:2606.08285, the PAKDD 2026 chapter and arXiv:2605.24564.
- The trading-agents-lab reproduction result (anecdotal, unvetted).
- CVE-2025-64439, CVE-2026-27794 and CVE-2025-67644, with their affected and fixed ranges.
- The GitHub star, fork, issue and PR counts.

**Assumptions (not verified):**

- Per-token prices in §7.2. They are illustrative tiers, not current prices for `gpt-6-*` or any specific model.
- Hidden reasoning tokens adding about 100% to output.
- Real models taking more tool-loop turns than the scripted model, which makes the §7.1 numbers a lower bound.
- Local-model throughput figures (§7.3).
- The published knowledge cutoffs of gpt-4o and o1-preview (recollection).
- That prompt caching would roughly halve the input cost.
- The engineer-day estimate (§6.6).
- That a point-in-time news source can be obtained for FX, indices and gold at acceptable cost.

### Sources

- Paper: [arXiv:2412.20138 abstract](https://arxiv.org/abs/2412.20138), [HTML v7](https://arxiv.org/html/2412.20138v7)
- [Li et al., arXiv:2505.07078](https://arxiv.org/html/2505.07078v3)
- [Yao & Zheng, arXiv:2606.08285](https://arxiv.org/pdf/2606.08285)
- [Nguyen & Pham, PAKDD 2026](https://link.springer.com/chapter/10.1007/978-981-92-2014-4_26)
- [Li, Wang, Ma, arXiv:2605.24564](https://arxiv.org/html/2605.24564)
- [trading-agents-lab](https://github.com/Kantamaniprakash/trading-agents-lab)
- [CVE-2025-64439 (GHSA-wwqv-p2pp-99h5)](https://github.com/advisories/GHSA-wwqv-p2pp-99h5)
- [CVE-2026-27794](https://www.sentinelone.com/vulnerability-database/cve-2026-27794/)
- [CVE-2025-67644](https://advisories.gitlab.com/pypi/langgraph-checkpoint-sqlite/CVE-2025-67644/)
- [GitHub repository page](https://github.com/TauricResearch/TradingAgents)
