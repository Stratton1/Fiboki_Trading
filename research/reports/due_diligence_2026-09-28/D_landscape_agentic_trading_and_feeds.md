# D — Landscape: agentic AI trading, LLM signal evidence, and data feeds for Fiboki V2

**Prepared:** 2026-09-28 · **Scope:** whether and how Fiboki V2 (UK, research-first, FX / gold / indices, spread betting via OANDA Europe, cardinal rule "an LLM is never the final authority on an executable order") should add autonomous agents that scan markets, news and data and feed a deterministic trading system.

**Citation convention.** Inline links carry the access date in brackets, e.g. `(2026-09-28)`. Every source was accessed on 2026-09-28. Evidence strength is tagged:

- **[P]** I read the primary source myself: the paper abstract or body, the regulator page, the vendor's own documentation, or the repository code at a stated commit.
- **[S]** Secondary or third-party source, or a vendor page whose details I could not read in full.
- **[R]** A cloned repository, inspected at the commit hash given.

Vendor prices are list prices as shown on the date of access. They change often, so re-check before buying.

**How the web material was read.** Much of it came through an automated page-summariser. Where a summary made a claim I could not check against the page itself, the report says so. Treat any single number tagged [S] as provisional.

---

## 0. Executive summary

1. **The evidence does not support letting an LLM make trading decisions in FX, gold or indices.**
   - **Equities.** Most headline results for LLM agents come from short, flattering, pre-cutoff windows with few or no costs. One example: TradingAgents reports a Sharpe of 5.6–8.2 over three months on three US mega-caps.
   - **Broader tests.** When the test covers more stocks and a longer period, the advantage disappears. FINSABER (KDD '26) tested 20 years and 100+ symbols.
   - **Contamination is large and measurable.** A model can "remember" what happened after the dates it is tested on. Studies that measure this include LAP, Profit Mirage, FinCAD, Look-Ahead-Bench and HindsightBench.
   - **FX, gold and indices.** I found **no credible study that is post-cutoff, includes costs and works at H1–D1** showing that LLM news signals carry alpha.
2. **What the evidence does support.**
   - LLMs are good at classifying financial text, such as central-bank stance and news tone.
   - Scheduled macro news moves FX, gold and indices hard. However, the move happens within **minutes**, well before an H1 bar closes.
   - So the most defensible use of news in an H1–D1 system is **risk avoidance**: event blackouts and volatility awareness. It is not direction prediction.
   - The best-designed positive LLM result I found uses the LLM as a **bounded modifier** of a deterministic strategy, and was tested after the model's cutoff with costs (Anic et al. 2025). That is exactly Fiboki's architecture. It is also equities-only, and its out-of-sample period is 15 months.
3. **Fiboki's highest-value "news" gap does not need an LLM.**
   - `marketstate/calendar.py` has a complete blackout implementation, but ships **no dated events**. Its own `USER_ACTION_NOTE` says that until a calendar is loaded, "every blackout query returns False … backtests and paper bots will trade straight through FOMC and NFP."
   - A deterministic, point-in-time economic calendar is worth more than any agent.
4. **Recommended now:**
   - A deterministic calendar feed.
   - Wiring the existing read-only research, failure-investigation and data-quality roles to a real model, local-first.
   - An **unscheduled-event classifier that can only veto new entries**, run in **shadow mode** against a pre-registered evaluation.
5. **Recommended later:** LLM-derived features as bounded inputs, first **downsizing-only**. Only after the shadow evaluation shows value net of costs.
6. **Never:** order, sizing or risk-limit authority. Fiboki already enforces this structurally, with five mechanisms (see §3.3). That is stronger than any open-source or commercial system surveyed.
7. **UK regulation does not stand in the way of a private individual automating their own spread betting.**
   - RTS 6 / MAR 7A apply to investment firms.
   - The Consumer Duty applies to the broker.
   - The real constraints are:
     - UK MAR, which applies to any person.
     - The regulatory perimeter, **if anyone else's money or any signal-sharing is involved**. Joe and Tom are both operators, so this needs legal advice.
     - Broker and API terms.
     - **Data licences**. Several popular sources forbid automated extraction.

---

## 1. Evidence on LLM / agent trading performance (2023–2026)

### 1.1 LLM trading agents: headline claims

**TradingAgents** (Xiao et al.; arXiv 2412.20138, v1 Dec 2024, v7 Jun 2025) [P] — [arXiv abs](https://arxiv.org/abs/2412.20138), [HTML v4](https://arxiv.org/html/2412.20138v4) (2026-09-28)
- Multi-agent design: analyst, bull/bear researcher, trader and risk roles.
- Backtest: **1 Jan – 29 Mar 2024 (three months)** on AAPL, GOOGL, AMZN and others.
- Reported results: AAPL 26.62% cumulative return / Sharpe 8.21; GOOGL 24.36% / 6.39; AMZN 23.21% / 5.60.
- The authors themselves call the Sharpe "exceptionally high" because of "few pullbacks". They limited the test to three months "due to intensive LLM and tool use".
- The excerpt I read does not model transaction costs.
- The window sits inside the training period of the o1-preview and GPT-4o models used.

**FinMem** (arXiv 2311.13743, AAAI Symposium 2024) and **FinAgent** (arXiv 2402.18485) [P] — [FinMem](https://arxiv.org/abs/2311.13743), [FinAgent](https://arxiv.org/abs/2402.18485) (2026-09-28)
- These are single-stock or single-asset decision agents.
- FinAgent claims "over 36% average improvement on profit" across six stock and crypto datasets.
- Both evaluate over short, pre-cutoff windows.

**AI-Trader benchmark** (Fan et al., HKUDS; arXiv 2512.10971, Dec 2025) [P] — [arXiv](https://arxiv.org/abs/2512.10971) (2026-09-28)
- A *live*, contamination-free design: agents trade in real time, so there is no hindsight.
- Covers US stocks, A-shares and crypto, with six mainstream LLMs.
- Finding: "general intelligence does not automatically translate to trading effectiveness; most agents showed poor returns and weak risk management". Risk control was the determining factor for robustness across markets.

### 1.2 Critical results, replication failures and contamination

**FINSABER** — Li, Kim, Cucuringu, Ma, *"Can LLM-based Financial Investing Strategies Outperform the Market in Long Run?"* (KDD '26) [P] — [arXiv HTML v6](https://arxiv.org/html/2505.07078v6), [DOI](https://doi.org/10.1145/3770854.3785702) (2026-09-28)
- Re-tested FinMem and FinAgent over **2000–2024 on 100+ symbols**, including delisted names, with commissions.
- The previously reported LLM advantages "deteriorate significantly under broader cross-section and over longer-term evaluation".
- The LLM strategies were "overly conservative in bull markets … overly aggressive in bear markets, incurring heavy losses".
- **This is the most important negative replication in the area.**

**Lookahead Propensity (LAP)** — Gao, Jiang, Yan, *"Detecting Lookahead Bias in LLM Forecasts"* (arXiv 2512.23847, Dec 2025, rev. Jun 2026) [P] — [arXiv](https://arxiv.org/abs/2512.23847) (2026-09-28)
- A cheap statistical test for whether a model already "knows" an outcome.
- LAP is substantially positive inside the training period and "collapses essentially to zero right after the training-data cutoff".
- Forecast accuracy rises with LAP inside the training window, which is direct evidence that in-sample LLM backtests are inflated.

**Glasserman & Lin (2023)** — *"Assessing Look-Ahead Bias in Stock Return Predictions Generated by GPT Sentiment Analysis"* [P] — [arXiv 2309.17322](https://arxiv.org/abs/2309.17322) (2026-09-28)
- Anonymising company identifiers changes results.
- In-sample, a "distraction" effect (general knowledge about the company) outweighed look-ahead, especially for large firms.
- Recommends anonymisation for de-biased backtests.
- Caveat for FX: currencies and central banks cannot be meaningfully anonymised.

**Profit Mirage** (Li et al., arXiv 2510.07920, Oct 2025) [P] — [arXiv](https://arxiv.org/abs/2510.07920) (2026-09-28)
- LLM-agent backtest results "evaporate once the model's knowledge window ends".
- Introduces FinLake-Bench to measure the effect.

**FinCAD** — Li, Wang, Ma (Edinburgh), *"Summoning the Oracle to Slay It"* (arXiv 2605.24564, v2 Aug 2026) [P] — [arXiv HTML](https://arxiv.org/html/2605.24564) (2026-09-28)
- Names the problem "parametric look-ahead bias": the bias lives in model weights, not in the data pipeline.
- Their correction reduced mean in-sample (2010–2020) performance by up to **−67.1%** for the largest model tested.

**Look-Ahead-Bench** (Benhenda, arXiv 2601.13770, Jan 2026) [P] — [arXiv](https://arxiv.org/abs/2601.13770) (2026-09-28)
- Standard open models (Llama 3.1 8B/70B, DeepSeek 3.2) show "significant lookahead bias", measured as alpha decay.
- Purpose-built point-in-time models generalised better.

**HindsightBench** (Jia, arXiv 2607.18867, Jul 2026) [P] — [arXiv HTML](https://arxiv.org/html/2607.18867v1) (2026-09-28)
- Uses vintage-correct macro data.
- "Measured cutoffs span 22 months across vendors and precede vendor-reported dates by up to eight months."
- Date-trigger recall is present in every 2026-generation model tested.
- **Implication for Fiboki: you cannot trust a vendor's stated cutoff when choosing a pseudo-out-of-sample window.**

**Hedge-fund-perspective review** (Zhang & Zhang, arXiv 2605.05211, May 2026) [P] — [arXiv HTML](https://arxiv.org/html/2605.05211v1) (2026-09-28)
- Most studies use about one calendar year of data, weak baselines and metrics such as MSE or accuracy rather than risk-adjusted net returns.
- Sentiment is regime-dependent: "the *same* news can imply opposite market directions under different macro regimes".
- Leakage often comes through news text that literally describes the price move ("TSLA tumbled").

**LLMs as general forecasters** [P]
- Halawi et al. (arXiv 2402.18563) approached, but did not reach, crowd-level forecasting accuracy — [arXiv](https://arxiv.org/abs/2402.18563) (2026-09-28).
- A critique documents retrieval leakage, date mismatches and over-wide equivalence margins in "superhuman forecaster" papers, and concludes that autonomous AI forecasters "remain far below expert-level accuracy" — [LessWrong critique](https://www.lesswrong.com/posts/uGkRcHqatmPkvpGLq/contra-papers-claiming-superhuman-ai-forecasting) (2026-09-28).
- ForecastBench runs a continuous, contamination-free leaderboard against superforecasters (latest update 19 Aug 2026). I could not extract its current scores — [forecastbench.org](https://www.forecastbench.org/) (2026-09-28) [S].

### 1.3 News and sentiment alpha: before and during the LLM era (mostly equities)

**Pre-LLM**
- **Tetlock (2007), *J. Finance***: media pessimism predicts short-term index return reversals. DOI 10.1111/j.1540-6261.2007.01232.x (cited from the literature; not re-read).
- **Heston & Sinha (2017), *FAJ***: news sentiment predicts returns over days, and weekly aggregation lengthens the horizon — [Fed FEDS version](https://www.federalreserve.gov/econres/feds/news-versus-sentiment-predicting-stock-returns-from-news-stories.htm) (2026-09-28) [S].
- **Ke, Kelly & Xiu (NBER w26186)**: supervised text sentiment predicts returns — [RePEc](https://ideas.repec.org/p/nbr/nberwo/26186.html) (2026-09-28) [S].

**LLM era**
- **Lopez-Lira & Tang** (arXiv 2304.07619, v6 Oct 2025) [P] — [arXiv](https://arxiv.org/abs/2304.07619) (2026-09-28):
  - Uses **post-knowledge-cutoff** headlines.
  - About 90% portfolio-day hit rate for the *non-tradable initial reaction*.
  - The effect is strongest in small caps and on negative news.
  - Strategy returns **declined over time as adoption rose**.
- **Chen, Kelly & Xiu, "Expected Returns and LLMs"** (SSRN 4416687, rev. Aug 2024) [P] — [SSRN](https://papers.ssrn.com/sol3/papers.cfm?abstract_id=4416687) (2026-09-28):
  - Covers 16 equity markets and 13 languages.
  - LLM embeddings beat simpler NLP; prices respond slowly to news.
  - The abstract does not discuss costs.
- **Anic, Barbon, Seiz & Zarattini (arXiv 2510.26228, Oct 2025)** [P] — [arXiv HTML](https://arxiv.org/html/2510.26228v1) (2026-09-28):
  - The LLM (GPT-4o-mini) conditions a **deterministic momentum strategy**; it does not replace it.
  - Test period Jan 2024 – Mar 2025, which is **after** the Oct 2023 cutoff. Costs of 2 bp per trade.
  - OOS Sharpe 1.06 vs 0.79 baseline.
  - **This is the design pattern most consistent with Fiboki.** Limits: equities only, 15-month OOS, one model.

**Alpha / factor mining**
- Alpha-GPT (arXiv 2308.00016) — [arXiv](https://arxiv.org/abs/2308.00016) (2026-09-28) [S].
- AlphaAgent (KDD '25; arXiv 2502.16789) [P] — [arXiv HTML](https://arxiv.org/html/2502.16789v2) (2026-09-28):
  - CSI 500 and S&P 500, 2021–2024, with costs.
  - Reports 11.0% annualised excess return (IR 1.5) on CSI 500 and 8.74% (IR 1.05) on S&P 500.
  - The test window **overlaps typical model training periods**, so contamination is not ruled out.
  - Its explicit anti-decay machinery (originality checks, complexity control) is a useful idea for Fiboki's hypothesis generation.

### 1.4 FX, gold and indices specifically (H1–D1)

**Macro announcements move these markets hard and fast (supported, pre-LLM).**
- Andersen, Bollerslev, Diebold & Vega (2003, *AER*): announcement surprises produce "conditional mean jumps" in FX. Price discovery happens within minutes — [NBER w8959](https://www.nber.org/papers/w8959) (2026-09-28) [S].
- Gold price discovery responds asymmetrically to US and euro-area surprises, concentrated in the NY/London overlap. Effects reverse under extreme uncertainty — [ScienceDirect, Finance Research Letters/IRFA 2021](https://www.sciencedirect.com/science/article/abs/pii/S1057521921002209) (2026-09-28) [S].
- Naderi Semiromi, Lessmann & Peters (2020) predict **30-minute** FX direction after calendar releases with XGBoost plus an FX lexicon. Accuracy is highest right after release — [ScienceDirect](https://www.sciencedirect.com/science/article/abs/pii/S1062940820300784) (2026-09-28) [S].
- **Implication:** at H1–D1 on a closed-candle system, the directional information in scheduled news is mostly priced before the bar closes. What survives is spread and volatility risk.

**Media sentiment across currencies (supported, pre-LLM, slow horizon).**
- Filippou, Taylor & Wang (*JFQA* 2023): 48 currencies, 1.2m articles, 35 years. A **contrarian** cross-sectional strategy (buy negative-sentiment currencies) earns significant Sharpe ratios — [Cambridge Core](https://www.cambridge.org/core/journals/journal-of-financial-and-quantitative-analysis/article/abs/media-sentiment-and-currency-reversals/84CEB4F2EEE1521C3C694F547AC35A0B) (2026-09-28) [S].
- This is a cross-sectional, low-frequency result. It does not show H1 time-series edge on a handful of majors.

**Retail positioning (plausible).**
- Kaourma et al. (2025, *J. Int. Fin. Markets*): about 5m EUR/USD retail trades from a European broker, 2014–16.
- Retail traders trade *against* macro surprises. Crossover strategies on retail order flow earn significant returns over **4–20 hour** holding periods — [ScienceDirect](https://www.sciencedirect.com/science/article/abs/pii/S1042443125000368) (2026-09-28) [S].
- The data was proprietary. Public proxies (IG client sentiment, OANDA position book, Myfxbook) are coarser, and whether the effect survives costs is not established.

**LLM-specific FX work (weak evidence).**
- Yang et al. (*Financial Innovation*, Sep 2025) [P] — [Springer](https://link.springer.com/article/10.1186/s40854-025-00789-6) (2026-09-28):
  - ChatGPT-derived topic-sentiment index, **weekly** USD/CNY, EUR and JPY, 2019–2023.
  - Improves interval-forecast error.
  - **No trading returns, no costs, and the sample sits inside the training window.**
- EUR/USD "IUS" framework (arXiv 2408.13214): MAE −10.7% and RMSE −9.6%, with no trading backtest [P] — [arXiv](https://arxiv.org/abs/2408.13214) (2026-09-28).

**Central-bank text classification (supported as classification, not as alpha).**
- Hansen & Kazinnik, "Can ChatGPT Decipher Fedspeak?" — [SSRN](https://ssrn.com/abstract=4399406) (2026-09-28) [S].
- BIS WP 1215 (Oct 2024): domain-adapted CB-LMs can beat GPT-4 on FOMC stance classification, while large LLMs win on long, complex news sets. **No market-reaction evidence** — [BIS](https://www.bis.org/publ/work1215.pdf) (2026-09-28) [P].

### 1.5 Calibrated summary

| Claim | Status | Basis |
|---|---|---|
| Text contains return-relevant information (equities, short horizon) | **Supported** | Tetlock; Heston-Sinha; Ke-Kelly-Xiu; Lopez-Lira (post-cutoff); Chen-Kelly-Xiu |
| LLMs extract it better than dictionaries | **Supported (equities)** | Lopez-Lira; Chen-Kelly-Xiu; decaying with adoption |
| LLM *agents as decision-makers* beat simple baselines net of costs across regimes | **Not supported** | FINSABER; AI-Trader benchmark; review 2605.05211 |
| In-sample LLM backtests are materially inflated by weight contamination | **Supported, strongly** | LAP; Profit Mirage; FinCAD (−67%); Look-Ahead-Bench; HindsightBench |
| Scheduled macro news moves FX/gold/indices; the move is fast | **Supported** | Andersen et al.; gold price-discovery literature |
| LLM news signals carry **net-of-cost alpha in FX/gold/indices at H1–D1** | **Unknown: no credible post-cutoff, costed study found** | Search of arXiv/SSRN/journals, 2026-09-28 |
| LLM as a *bounded modifier* of a deterministic strategy adds value | **Weakly supported (1 equity study, 15-month OOS)** | Anic et al. 2025 |
| Retail-positioning contrarian signals at 4–20h | **Plausible (proprietary data; costs unclear)** | Kaourma et al. 2025 |
| LLM central-bank stance classification is accurate | **Supported** | Hansen-Kazinnik; BIS CB-LM |
| Vendor-stated model cutoffs are reliable for defining OOS | **Not supported** | HindsightBench (up to 8 months early) |

---

## 2. What "agentic auto-trading" systems actually do in 2026

**Where the LLM sits:**
- **(a)** research and idea generation
- **(b)** feature or signal generation
- **(c)** decision-making (buy/sell/hold, rating)
- **(d)** execution (the LLM originates broker orders)

**How it is guarded:**
- **Structural:** enforced in code, permissions or accounts.
- **Prompt:** relies on instructions to the model.

### 2.1 Open source (repositories inspected at the commit given, or official docs)

| System | Where the LLM sits | Guarding | Assets | Notes |
|---|---|---|---|---|
| **TradingAgents** (Tauric) [R] `35543d0`, v0.5.1 2026-09-24 | a, b, **c**. The Portfolio Manager emits a five-tier rating and "approves/rejects the transaction proposal … sent to the simulated exchange" (README l.104) | Structured outputs for manager/trader agents (v0.2.4). Point-in-time **data** filtering (v0.4–0.5). **No broker execution.** | US/intl equities, crypto tickers | README: "Treat the framework as a research scaffold … not as a strategy with a fixed, replicable return". Also warns that "news and social sources still reflect 'now'" on historical runs, which is a live look-ahead path. No handling of **model-weight** contamination (grep for cutoff/contamination: none beyond data dates). |
| **Vibe-Trading** (HKUDS) [R] `18988fb`, 2026-09-28 | a, b, c, **d**. The LLM generates strategy code, runs backtests, and can call `trading_place_order` | **Structural but bounded**: live orders pass `sdk_order_gate.execute_live_order`. That gate checks a user-committed mandate (`max_order_notional_usd`, `max_total_exposure_usd`, `max_leverage`, `max_trades_per_day`, symbol universe), a filesystem kill switch (`halt_flag_set`) and a daily lock, and writes an audit ledger. **Paper-trade profiles place orders with "no mandate declared".** Generated strategy code runs locally in a narrowed subprocess, per SECURITY.md | Multi-market equities, crypto, futures, forex (backtest engines, MT5 connector) | The LLM is still the order *originator*; the code only caps it. That is the opposite of Fiboki's rule. Very high development velocity (daily releases), which cuts both ways for auditability. |
| **AI-Trader** (HKUDS) [R] `d03ff6c` + [paper](https://arxiv.org/abs/2512.10971) | c, d (paper trading; copy trading; "sync signals across brokers") | **Prompt-level.** Agents join by being told to "Read https://ai4trade.ai/SKILL.md and register", i.e. they fetch remote instructions: a textbook prompt-injection and supply-chain surface | US stocks, A-shares, crypto, Polymarket; README claims forex | A social / benchmark platform, not an execution architecture. |
| **FinRobot** (AI4Finance) — [GitHub](https://github.com/ai4finance-foundation/finrobot) (2026-09-28) [P] | a (equity research reports, forecasts) | "Numbers are code-calculated", with the LLM for narrative. No execution | Equities | Good pattern: deterministic numbers, LLM for prose. |
| **FinMem / FinAgent** (research code) | c | Prompt; backtest only | Equities, crypto | Did not survive FINSABER. |
| **freqtrade / FreqAI** [R] `30c00ed`, 2026-09-28 | **No LLM in core** (grep found none). FreqAI is classical ML/RL at (b) | Deterministic engine | Crypto | Worth copying: `docs/lookahead-analysis.md` tooling for detecting look-ahead. |
| **NautilusTrader** | No first-party LLM integration found. Third-party experiments exist (e.g. a DeepSeek crypto bot) — [GitHub](https://github.com/Akshay-a/nautilus_ai_trading_agent) (2026-09-28) [S] | Deterministic engine | Multi-asset incl. FX | Where the LLM sits is up to the integrator. |
| **QuantConnect** — Mia V2 + MCP server [P] — [MCP key concepts](https://www.quantconnect.com/docs/v2/ai-assistance/mcp-server/key-concepts), [Mia V2](https://www.quantconnect.com/announcements/19846/your-ai-quant-developer/) (2026-09-28) | a (ideation, code, backtests); via MCP also **d-adjacent**: "deploy live algorithms" to paper, `stop_live_algorithm`, `liquidate_live_algorithm` | Platform permissions; the docs I read are **sparse on confirmations or guards** | Multi-asset | The deterministic LEAN engine executes. The LLM authors the code that will trade. |
| **Hummingbot** — MCP + Condor [P] — [MCP](https://hummingbot.org/mcp/), [Condor README](https://github.com/hummingbot/condor/blob/main/README.md) (2026-09-28) | c, **d**: MCP lets an agent "place orders, manage positions … deploy bots". Condor agents can "author [their] own tick strategy … and run it autonomously with dry-run support" | Mixed: deterministic executors, per-agent risk limits, RBAC. Docs treat MCP as "a privileged control plane" and advise network isolation (Tailscale) | Crypto CEX/DEX | |
| **OpenBB** — Workspace MCP (26 May 2026) [P] — [blog](https://openbb.co/blog/introducing-workspace-mcp/) (2026-09-28) | a (analysis, dashboards) | **Structural**: agent inherits user entitlements; data lineage on every output; vaulted credentials. No trade execution described | Multi-asset data | A governance model worth copying (entitlement inheritance, lineage). |

### 2.2 Commercial

| Offering | Where the LLM sits | Guarding | Assets / jurisdiction | Notes |
|---|---|---|---|---|
| **Composer** — [composer.trade/ai](https://www.composer.trade/ai) (2026-09-28) [P] | a, b (prompt → strategy; backtest) → deterministic "symphony" auto-executes | Execution is deterministic once the strategy is authored; SEC/FINRA broker-dealer (Composer Securities; Alpaca/Apex clearing) | US equities/ETFs | The LLM authors; rules execute. Not UK. |
| **Tickeron** "AI Robots" — [instructions](https://tickeron.com/trading-investing-101/ai-robots-instructions/) (2026-09-28) [S] | Signal generation (b/c); whether it auto-executes is unclear from the page | Unclear | Equities; forex claimed by third parties | Performance statistics shown; I could not establish whether they are live, audited or hypothetical. **Treat as marketing.** |
| **TrendSpider AI Strategy Lab** — [blog, Nov 2024](https://trendspider.com/blog/introducing-trendspiders-ai-strategy-lab/) (2026-09-28) [P] | **Not an LLM**: Naive Bayes, logistic regression, KNN, random forest; (b). Can deploy as bots | Rule/ML engine | Multi-asset charting | A useful reminder that "AI" in retail marketing is often classical ML. |
| **Kavout** (Kai Score, InvestGPT) — [kavout.com](https://www.kavout.com/) (2026-09-28) [S] | a, b (rankings, research agents) | N/A (no execution found) | Equities, crypto | |
| **Robinhood Agentic Trading** (launched 27 May 2026) — [support article](https://robinhood.com/us/en/support/articles/agentic-trading-overview/), [TechCrunch](https://techcrunch.com/2026/05/27/robinhood-now-lets-your-ai-agents-trade-stocks/) (2026-09-28) [P] | **d**: external agents (Claude, ChatGPT and others via MCP) place orders | **Structural account segregation**: agents trade only in a dedicated "Agentic" account and have read-only access elsewhere. Robinhood warns agents "can make errors … act on incomplete or outdated information" | US stocks, crypto; US only | The first mainstream broker to grant agent order authority, bounded by account segregation. |
| **UK brokers** (Capital.com, IG, CMC) — [Finder UK](https://www.finder.com/uk/cfd-trading/how-are-traders-using-ai) (2026-09-28) [S] | Capital.com reportedly supports external AI agents over MCP and has behavioural analytics. IG/CMC offer pattern recognition | Finder describes no autonomous agentic execution; third-party AI use is "at your own risk" | CFD / spread bet, FCA-regulated | **I found no FCA-regulated UK retail offering that lets an LLM autonomously originate spread-bet or CFD orders.** Unverified beyond Finder; open question. |

### 2.3 Pattern across the landscape

- The market is splitting into two camps:
  - **"The LLM authors, deterministic code executes"**: Composer, QuantConnect, FinRobot, TrendSpider.
  - **"The LLM originates orders inside a cage"**: Vibe-Trading mandates, Robinhood Agentic accounts, Hummingbot executors.
- **No surveyed system matches Fiboki's stance, in which the agent package cannot even import execution types.**
- The strongest structural guards seen elsewhere are:
  - account segregation (Robinhood);
  - code-enforced mandates with a kill switch (Vibe-Trading);
  - entitlement inheritance with lineage (OpenBB).

---

## 3. Safe architecture patterns for LLM-in-the-loop trading

### 3.1 Engineering sources

- **Anthropic, "Building effective agents"** [P] — [anthropic.com](https://www.anthropic.com/engineering/building-effective-agents) (2026-09-28):
  - Prefer **workflows**, meaning predefined code paths, over open-ended agents unless flexibility is truly needed.
  - "finding the simplest solution possible".
  - Extensive sandboxed testing, guardrails and human checkpoints.
  - Autonomy means "higher costs, and the potential for compounding errors".
- **OpenAI, "A practical guide to building agents"** [P] — [PDF](https://cdn.openai.com/business-guides-and-resources/a-practical-guide-to-building-agents.pdf) (2026-09-28):
  - Layered guardrails: relevance and safety classifiers, rules-based checks, output validation.
  - **Tool risk ratings by reversibility and financial impact.**
  - Mandatory human escalation for "high-risk actions (… processing payments)".
- **OWASP LLM06:2025 Excessive Agency** [P] — [genai.owasp.org](https://genai.owasp.org/llmrisk/llm062025-excessive-agency/) (2026-09-28): minimise extensions, least privilege, human approval, **complete mediation in downstream systems**, execute in user context, monitoring and rate limits.
- **Prompt-injection design patterns** — Beurer-Kellner et al. (arXiv 2506.08837, Jun 2025) [P] — [arXiv](https://arxiv.org/abs/2506.08837) (2026-09-28):
  - Action-selector, plan-then-execute, map-reduce, dual-LLM, code-then-execute, context minimisation.
  - These give provable resistance by keeping untrusted text away from control flow.
- **CaMeL** (Debenedetti et al., Google DeepMind; arXiv 2503.18813) [P] — [arXiv](https://arxiv.org/abs/2503.18813) (2026-09-28):
  - Capability-based separation of control flow and data flow.
  - 77% of AgentDojo tasks solved *with provable security*, vs 84% undefended.
- **"Lethal trifecta"** (Willison, Jun 2025) [P] — [simonwillison.net](https://simonwillison.net/2025/Jun/16/the-lethal-trifecta/) (2026-09-28):
  - The trifecta is private data + untrusted content + the ability to communicate externally.
  - **A news-reading agent is by definition exposed to untrusted content.** It must therefore have no consequential action and no exfiltration channel.
- **Structured outputs.** Anthropic's structured outputs use **constrained decoding** to guarantee schema-valid JSON and strict tool inputs [P] — [docs](https://platform.claude.com/docs/en/build-with-claude/structured-outputs) (2026-09-28). Stated limits:
  - Numeric `minimum`/`maximum` and `pattern` are **not enforced by the grammar**; the SDK re-validates them.
  - Refusals and `max_tokens` stops can still yield off-schema output.
  - Enum casing can differ.
  - **Numeric bounds must be enforced in your own validator, not trusted to the model.**
- **NIST AI RMF 1.0** (Jan 2023; Govern/Map/Measure/Manage) and the **Generative AI Profile NIST AI 600-1** (26 Jul 2024). NIST says the RMF "is being revised as part of the White House AI Action Plan" [P] — [nist.gov](https://www.nist.gov/itl/ai-risk-management-framework) (2026-09-28).

### 3.2 Regulator and industry sources

- **FCA multi-firm review of algorithmic trading controls** (21 Aug 2025; 10 principal trading firms; RTS 6) [P] — [fca.org.uk](https://www.fca.org.uk/publications/multi-firm-reviews/algorithmic-trading-controls-high-level-observations) (2026-09-28). Good practice:
  - pre-trade controls calibrated by algorithm and asset class and **enforced at the gateway**;
  - stress-scenario simulation;
  - phased pilot deployment;
  - formal material-change definitions;
  - an algorithm inventory with named owners.
- **BoE/FCA "AI in UK financial services 2024"** (21 Nov 2024; 118 firms) [P] — [bankofengland.co.uk](https://www.bankofengland.co.uk/report/2024/artificial-intelligence-in-uk-financial-services-2024) (2026-09-28):
  - 75% of firms use AI; foundation models are 17% of use cases.
  - 55% of use cases involve some automated decision-making, but only **2% are fully autonomous**.
  - 46% of firms report only "partial understanding" of the AI they use.
- **IOSCO CR/01/2025** (Mar 2025, consultation) [P] — [PDF](https://www.iosco.org/library/pubdocs/pdf/IOSCOPD788.pdf) (2026-09-28): risks include explainability, data quality, third-party concentration and hallucination. Agentic trading systems are described as largely exploratory.
- **FCA stance on AI**:
  - No bespoke AI rulebook. Existing regimes apply: Consumer Duty, SM&CR, SYSC, operational resilience — [WilmerHale, 29 Apr 2026](https://www.wilmerhale.com/en/insights/client-alerts/20260429-ai-and-the-uk-financial-conduct-authority) (2026-09-28) [S].
  - Nikhil Rathi, 24 Jun 2026: "accountability for regulated activities and outcomes must remain clear" for agentic systems; "98% of operational incidents reported to us related to technology and cyber issues" — [fca.org.uk speech](https://www.fca.org.uk/news/speeches/rethinking-regulation-age-ai) (2026-09-28) [P].

### 3.3 Pattern catalogue mapped to Fiboki

Fiboki's current state is taken from `docs/v2/AI_AGENT_ARCHITECTURE.md`, snapshot 2026-09-19.

| Pattern | What it means | Fiboki today | Gap / recommendation |
|---|---|---|---|
| **Absent capability** (strongest form of least privilege) | The agent cannot be granted what does not exist | `Capability` enum has 20 members and **no execution member**. An import-time parser rejects mutating-verb × execution-noun names. An AST test forbids agent imports of `fiboki.broker/risk/portfolio`, `Order`, `ExecutionMode` | Keep. Stronger than anything surveyed in §2. |
| **Complete mediation** | Every call goes through one checkpoint | `AgentSession.call`: role bundle → deny-by-default capability → input schema → budget → handler → **output schema** → exactly one audit record | Keep. |
| **Structured outputs / schema enforcement** | Constrained JSON plus your own validator | Pydantic `extra="forbid"`; shape limits → code scan → schema → compile | Add provider-side constrained decoding where available, but **keep the local validator authoritative** (vendor grammars do not enforce numeric bounds). |
| **Tool allow-lists** | Per-role bundles | 12 roles, 25 tools; bundles validated at import | New news/event role: read-only, **no web-write, no job submission**. |
| **Lethal-trifecta isolation / dual-LLM** | Untrusted text never drives control flow | `search_web` / `fetch_research` are stubs | A news classifier reads untrusted text, so its *only* output must be a schema-typed annotation into a quarantined store, read by deterministic code. It gets no tools and never sees strategy IP (context minimisation). |
| **Bounded numeric influence** | The LLM emits a score; deterministic policy caps its effect | Not yet (no signal path from agents) | If ever built: (1) **veto-only** (block new entries) → (2) **downsize-only** multiplier in [0.5, 1.0] → never upsize. Policy constants live outside `fiboki.agents` and change only through reviewed commits. |
| **Fail-closed / missing = neutral** | Timeouts and errors never increase risk | Calendar's empty default is **fail-open** (not in blackout) and is documented as dangerous | Decide per consumer: for a veto, missing annotation = *no veto* (keeps parity with the baseline) **plus** an alert. Record `available_at` so a backtest replays the same gaps. |
| **Human-in-the-loop vs human-on-the-loop** | Approve each action vs supervise and override | Research outputs are artefacts; promotion is gated | Agents stay human-on-the-loop for research. Any promotion of an LLM-derived feature to paper is **human-in-the-loop** (explicit operator sign-off, as with strategy promotion). |
| **Shadow mode** | Compute and log, but do not act | Not yet | Mandatory first stage for any agent output that could touch trading (§6.3). |
| **Pre-registration** | Hypothesis, metric, threshold and sample fixed before looking | Statistical Auditor pre-registers experiments | Extend to agent-feature evaluations. Count every LLM-generated hypothesis in the honest trial count for DSR. |
| **Audit trail** | Append-only, hash-chained, prompt → tool → artefact | `agents/audit.py`, hash-chained JSONL | Add a retention and rotation policy (an acknowledged gap). Log the model ID **and** a weights hash or version for local models. |
| **Gateway pre-trade controls, kill switch** (FCA RTS 6 good practice) | Enforce at the last hop | Five live controls in the mode guard + three in the OANDA adapter | No change; that is already the correct layer. |

---

### 3.4 UK regulation for a private individual trading their own money with an automated system

This is not legal advice. Items marked *open* need a solicitor.

| Regime | Applies to Joe as a private individual trading his own account? | Basis |
|---|---|---|
| **MiFID II RTS 6 / FCA MAR 7A** (algorithmic trading systems and controls) | **No.** MAR 7A applies to "UK MiFID investment firms" and third-country firms with a UK establishment that engage in algorithmic trading. It "transposes article 17 of MiFID" | [FCA Handbook MAR 7A](https://handbook.fca.org.uk/handbook/mar7a) (2026-09-28) [P]; [FCA RTS 6 glossary](https://handbook.fca.org.uk/glossary/G3566m) [S] |
| **Consumer Duty (PRIN 2A)** | **No: it binds the broker.** It applies "in relation to a firm's retail market business". It shapes what OANDA may offer (e.g. API access, appropriateness), not Joe's own conduct | [PRIN 2A.1](https://handbook.fca.org.uk/handbook/prin2a/prin2as1) (2026-09-28) [P] |
| **CFD / spread-bet product intervention** (PS19/18, 1 Aug 2019) | **Indirectly, through the broker.** Leverage limits "between 30:1 and 2:1" by asset class (30:1 major FX; 20:1 gold, major indices and non-major FX, per PS19/18), 50% margin close-out, negative balance protection. "References to CFDs include financial spread bets and rolling spot forex" | [FCA press release](https://www.fca.org.uk/news/press-releases/fca-confirms-permanent-restrictions-sale-cfds-and-cfd-options-retail-consumers), [PS19/18](https://www.fca.org.uk/publications/policy-statements/ps19-18-restricting-contract-difference-products) (2026-09-28) [P/S]. Fiboki's sizing must respect these, as V1's IG-style leverage alignment already did |
| **UK MAR** (market manipulation) | **Yes: it applies to any person**, including via algorithms, for instruments in scope. Spot FX is generally outside scope. Spread bets whose value depends on in-scope instruments (e.g. index or gold futures) may fall within it | [UK MAR Art. 12](https://www.legislation.gov.uk/eur/2014/596/article/12/data.xht) (2026-09-28) [S]. Low practical risk for H1 strategies, but **an agent that reacts to injected content could in principle generate patterned orders**; another reason to deny agents order authority |
| **Perimeter: authorisation** | Dealing on one's own account is generally not carried on "by way of business" for an individual. However, the business test turns on "the degree of continuity, the existence of a commercial element, the scale" | [PERG 2.3](https://handbook.fca.org.uk/handbook/perg2/perg2s3) (2026-09-28) [P] |
| **Perimeter: other people** | *Open and important.* If Fiboki trades **Tom's or anyone else's money**, pools capital, shares signals, or is productised, it may amount to managing investments, advising, arranging or a collective investment scheme. Each needs authorisation | FSMA / RAO; Fiboki's two-operator set-up makes this live. **Take advice before any shared-capital or signal-sharing arrangement.** |
| **AI-specific rules** | None bespoke. The FCA is principles-based (FCA AI Update, Apr 2024; Rathi speech, Jun 2026) | [FCA AI Update PDF](https://www.fca.org.uk/publication/corporate/ai-update.pdf) (2026-09-28) [S]; speech above [P] |
| **Broker terms** | **Yes, contractually.** API use, automated trading and spread-betting availability are governed by OANDA Europe's terms | OANDA says spread betting is available on v20 "Not by default. You can create a new sub-account with spread betting enabled" — [OANDA UK](https://www.oanda.com/uk-en/trading/standard-account/differences/) (2026-09-28) [P]. **Whether the v20 REST API can trade a spread-betting sub-account is not stated: open question.** |
| **Data licences** | **Yes, contractually.** This is the most concrete legal constraint on an automated pipeline (§4) | See §4 |

**Bottom line:** nothing in UK regulation requires Joe, trading his own money, to apply RTS 6 or the Consumer Duty. Mirroring RTS 6 good practice voluntarily (gateway pre-trade limits, kill switch, change control, inventory) is prudent, and Fiboki already does it. The live risks are perimeter creep (other people's money or signals), UK MAR, and data-licence breach.

---

## 4. News, macro and data feeds for FX, gold and indices

"Free viable" means usable by a solo operator in an automated, private, non-redistributed pipeline without breaching the stated terms. "PIT archive" means timestamps as first published, suitable for honest backtesting.

### 4.1 Economic calendars and official releases

| Source | Cost / tier | Limits | Licence for automated use | Latency | History / PIT | Free viable? |
|---|---|---|---|---|---|---|
| **ForexFactory** — [notices](https://www.forexfactory.com/notices) (2026-09-28) [P] | Free website | n/a | **Prohibits** access "using a method other than the interface"; "copying, republication or redistribution of FEED … explicitly prohibited" | Near-real-time | Web history; no licensed archive | **No** for automated pipelines. A weekly XML/JSON export (`nfs.faireconomy.media/ff_calendar_thisweek.xml`) is widely used, but its licence is not stated. Avoid, or get written consent. |
| **Investing.com** — [T&C](https://www.investing.com/about-us/terms-and-conditions) (2026-09-28) [P] | Free website | n/a | "prohibited to use, store, reproduce … the data … without … prior written permission"; prices "indicative and not appropriate for trading purposes" | — | — | **No.** |
| **Trading Economics** — [pricing (via apis.io)](https://apis.io/plans/tradingeconomics/tradingeconomics-plans-pricing/), [point-in-time docs](https://docs.tradingeconomics.com/economic_calendar/point-in-time/) (2026-09-28) [S/P] | Trial (100 requests); Standard ≈ $149/month and Professional ≈ $299/month billed yearly [S] | Plan-based; WebSocket on Pro | Licensed API; redistribution only on Enterprise | Streaming available | **Point-in-time calendar endpoint**: events "exactly as they appeared on a specific date, preserving the original values before any subsequent revisions", with Actual, Forecast (consensus), TEForecast and Previous [P]. Depth and plan tier not stated | **No (paid)**, but it is **the only affordable PIT calendar with consensus found**. |
| **FXStreet Calendar API** — [docs](https://docs.fxstreet.com/api/calendar/) (2026-09-28) [P] | Licensed (OAuth2); price not public | — | Commercial licence | Webhooks | Actual / consensus / revised; depth not stated | No |
| **Econoday** | Licensed; not public | — | Commercial | — | Consensus; institutional | No |
| **OANDA ForexLabs calendar** — [oandapyV20 docs](https://oanda-api-v20.readthedocs.io/en/latest/endpoints/forexlabs/calendar.html) (2026-09-28) [S] | With an account | — | Broker terms | — | `labs/v1/calendar` (v1-era; actual/forecast/previous). **Current availability unverified** | Maybe; verify. |
| **IG calendar** | With an account | — | Broker terms | — | — | Unverified |
| **FRED / ALFRED** — [API ToU](https://fred.stlouisfed.org/docs/api/terms_of_use.html) (2026-09-28) [P]; rate ≈ 120 req/min per key [S] | Free (API key) | ≈120/min | Attribution required. **Third-party series need the owner's permission beyond personal use** | Daily/series | **ALFRED vintages (`realtime_start/end`) give US first prints**, which fixes the "revised actuals" optimism noted in Fiboki's `USER_ACTION_NOTE`. No consensus | **Yes** |
| **BLS API** — [features](https://www.bls.gov/bls/api_features.htm) (2026-09-28) [P] | Free | v2: 500 queries/day, 50 series, 20 years | Public data | Release at 08:30 ET | Revised series; use ALFRED for vintages | **Yes** |
| **ONS API** — [developer hub](https://developer.ons.gov.uk/tour/latest-version/) (2026-09-28) [S] | Free | — | Open Government Licence (typical for ONS) | — | Release calendar published | **Yes** |
| **Central-bank schedules**: FOMC calendars ([Fed](https://www.federalreserve.gov/monetarypolicy/fomc_historical_year.htm)), BoE MPC dates, ECB meeting calendar | Free | — | Public | Published months ahead | Historical meeting dates available on the official sites | **Yes.** These are the scheduled *times*, which is all a blackout needs. |

### 4.2 News

| Source | Cost | Limits | Licence | Latency | Historical / PIT archive | Free viable? |
|---|---|---|---|---|---|---|
| **Central-bank RSS**: Fed ([feeds](https://www.federalreserve.gov/feeds/feeds.htm)), ECB ([RSS](https://www.ecb.europa.eu/home/html/rss.en.html)), BoE ([RSS](https://www.bankofengland.co.uk/rss)) (2026-09-28) [P] | Free | Polling | Public | Seconds to minutes | Official archives of statements and speeches (timestamps need care) | **Yes. The best signal-to-noise and primary source.** |
| **GDELT** — [DOC 2.0](https://blog.gdeltproject.org/gdelt-doc-2-0-api-debuts/), [data](https://gdeltproject.org/data.html) (2026-09-28) [P/S] | Free | DOC API max 250 results per query; **rolling 3-month window** | Open | ≈15-minute update cycle (GDELT 2.0) | Raw event and GKG files downloadable historically (2015+), also on BigQuery [S] | **Yes for research.** Noisy, general news, not finance-specific. |
| **NewsAPI.org** — [pricing](https://newsapi.org/pricing) (2026-09-28) [P] | Free Developer; Business $449/month; Advanced $1,749/month | 100/day free | **Free tier "development … only", not staging or production** | Free: 24 h delay | Free 1 month; paid 5 years | **No** |
| **Finnhub** — [third-party summary](https://apicostcalc.com/finnhub.html) (2026-09-28) [S] | Free; paid ≈ $50–500+/month | ≈60 calls/min free | Free = "personal, non-commercial" | Real-time | Depth not verified | **Yes (personal).** |
| **Marketaux** — [pricing](https://www.marketaux.com/pricing) (2026-09-28) [P] | Free; $29 / $49 / $99 / $199 per month | 100 requests/day and 3 articles/request free; up to 50k/day | Per plan | Near-real-time | Not stated | Marginal (3 articles/request) |
| **Alpha Vantage NEWS_SENTIMENT** — [docs](https://www.alphavantage.co/documentation/), [premium](https://www.alphavantage.co/premium/) (2026-09-28) [P] | Free 25/day; premium $49.99–249.99/month (75–1,200/min) | — | Per ToS | — | Supports `FOREX:USD` tickers and `economy_monetary` topics. Archive start not stated (reportedly 2022) [S] | Marginal |
| **Benzinga via Massive (ex-Polygon)** — [endpoint docs](https://massive.com/docs/rest/partners/benzinga/news) (2026-09-28) [P] | $99/month individual | — | Individual | Real-time | **Since 27 Apr 2009, `published` and `last_updated` timestamps**, so near-PIT | No (paid). **Equity-centric**; weak for FX |
| **Tiingo News** — [pricing](https://www.tiingo.com/about/pricing) (2026-09-28) [P] | Power $30/month individual; $50/month commercial internal | 10k/hour | Individual vs commercial tiers | — | **Only 3 months queryable history** | No for backtests |
| **EODHD News** — [docs](https://eodhd.com/financial-apis/stock-market-financial-news-api), [pricing](https://eodhd.com/pricing) (2026-09-28) [P] | Free 20 calls/day; from $19.99/month; all-in $99.99/month | 5 + 5/ticker calls per request | Personal plans | — | Covers `EURUSD.FOREX`; daily sentiment; depth unstated | Marginal |
| **finlight** — [trading page](https://finlight.me/news-api-for-trading) (2026-09-28) [P] | Pro tiers; price not read | 1–3 WebSocket connections | — | Raw stream at ingestion; **enrichment ≈28 s later** | REST historical ("for backtesting"); depth unstated | Unknown |
| **StockNewsAPI** — [pricing](https://stocknewsapi.com/pricing) (2026-09-28) [P] | $19.99 / $49.99 per month | 20k / 50k calls per month | — | — | Historical on Premium | No (US-stock focus) |
| **LSEG Machine Readable News** — [factsheet](https://lseg.com/content/dam/data-analytics/en_us/documents/fact-sheets/lseg-machine-readable-news.pdf) (2026-09-28) [P] | Institutional (quote) | — | Enterprise | Low-latency | **Analytics archive from 2003, ms timestamps, "point-in-time"** | No |
| **RavenPack** — [blog](https://www.ravenpack.com/blog/machine-readable-news) (2026-09-28) [S] | Institutional | — | Enterprise | — | ">18 years of millisecond time-stamped data" incl. FX and commodities | No |
| **Bloomberg Terminal** | ≈ $31,980 per seat per year (2026) [S] — [costbench](https://costbench.com/software/financial-data-terminals/bloomberg-terminal/) (2026-09-28) | — | Terminal licence; data feeds (B-PIPE) extra | — | Full | No |

**Honest conclusion on news archives.**
- No cheap source provides a **finance-grade, FX/macro-tagged, point-in-time news archive**.
- Benzinga via Massive (2009+) is the closest affordable option, but it is equity-centric.
- LSEG, RavenPack and Bloomberg are institutional.
- The **practical route for a solo operator is to start recording now**: headline, first-seen UTC, vendor timestamp and source, in an append-only store. Then evaluate forward, in shadow.

### 4.3 Positioning and sentiment

| Source | Cost | Limits / latency | Licence | History | Free viable? |
|---|---|---|---|---|---|
| **CFTC COT** — [COT page](https://www.cftc.gov/MarketReports/CommitmentsofTraders/index.htm), [Federal Register RFC](https://www.federalregister.gov/documents/2026/05/05/2026-08743/review-of-the-commitments-of-traders-reporting-program) (2026-09-28) [P] | Free; Socrata API without a token | Weekly. Tuesday positions released **Friday 15:30 ET** (≈3-day lag) | Public | Legacy from 1986; TFF/Disaggregated from 2006 | **Yes.** Note: the CFTC ran a **request for comment (deadline 4 Jun 2026) on publication frequency and content**, so the format may change. |
| **OANDA order/position book** — [oandapyV20](https://oanda-api-v20.readthedocs.io/en/latest/endpoints/instruments/instrumentpositionbook.html) (2026-09-28) [S] | With an account | Snapshot cadence not verified | Broker terms | Limited instruments; depth unverified | Probably; verify |
| **IG client sentiment** — [DailyFX/IG](https://www.dailyfx.com/sentiment), [trading-ig REST](https://trading-ig.readthedocs.io/en/latest/rest.html) (2026-09-28) [S] | With an IG account | REST `clientsentiment` | Broker terms | Snapshot only (no archive found) | Only with an IG account; start recording |
| **Myfxbook community outlook** — [API](https://www.myfxbook.com/api) (2026-09-28) [P] | Free / paid | **100 requests per 24 h** free; 2,800 paid; IP-bound sessions | "any software you develop should be free"; ToU applies | Snapshot | Yes, lightly |
| **Gold options skew (CME QuikStrike)** | CME tools; licensed data for automation | — | CME licence | — | No (not verified further) |

### 4.4 Cross-asset

- **FRED** covers, daily and free with attribution [S]:
  - US 10-year yield (`DGS10`), 2-year (`DGS2`);
  - broad dollar index (`DTWEXBGS`, a DXY proxy; ICE's DXY itself is licensed);
  - VIX (`VIXCLS`);
  - WTI (`DCOILWTICO`).
- **Check third-party copyright** on each series before relying on it (FRED ToU above).
- **MOVE** (ICE BofA) is licensed and not freely available.
- Intraday cross-asset data needs a paid feed, or OANDA CFD proxies (e.g. US bond, oil and index CFDs) from the dealing venue itself.

### 4.5 Price data (FX / CFD)

| Source | Cost | Limits | Licence | History | Notes | Free viable? |
|---|---|---|---|---|---|---|
| **OANDA v20 candles** — [instrument endpoint](https://developer.oanda.com/rest-live-v20/instrument-ep/), [best practices](https://developer.oanda.com/rest-live-v20/best-practices/) (2026-09-28) [P] | With an account (practice is fine) | **Max 5,000 candles per request** (default 500); 100 req/s on persistent connections, 2 new connections/s | Account terms | 2005 for majors per a third-party summary [S] | **The dealing venue.** Bid/ask/mid (`M`, `B`, `A`, `BA`). Fiboki notes that live pricing tier ≠ historical candles, and volume is tick count | **Yes** |
| **Dukascopy** — [historical export](https://www.dukascopy.com/swiss/english/marketwatch/historical/), [website ToU](https://www.dukascopy.com/swiss/english/legal-pages/terms-of-use/), [date ranges](https://tickstory.com/dukascopy-historical-data-available-date-ranges/) (2026-09-28) [P/S] | Free | — | **Website ToU: "personal, non-commercial use"; no "scraper, robot, bot … data mining"; "may not be used to construct a database of any kind"** | Ticks: EURUSD, GBPUSD, XAUUSD from May 2003; DAX from Jan 2012; FTSE and S&P 500 from Sep 2011 | Another venue's prices (Fiboki already documents this). **Licence risk:** Fiboki's `dukascopy.py` builds `datafeed.dukascopy.com` URLs. Whether bulk datafeed pulls are covered by the website ToU is **open**; read literally, database construction is forbidden. | **Uncertain. Get written consent or restrict to personal research.** |
| **HistData** — [site](https://www.histdata.com/) (2026-09-28) [S] | Free web; paid FTP | — | Not verified | M1/tick, 10+ years | V1 store; bid only, EST-no-DST (documented) | Yes (personal) |
| **TrueFX** — [third-party summary](https://newyorkcityservers.com/blog/top-12-sources-to-download-forex-historical-data-free-paid) (2026-09-28) [S] | Free (registration) | — | Registration terms | Ticks from May 2009 | Aggregated liquidity | Yes (personal) |
| **Massive (ex-Polygon) Currencies** — [pricing](https://massive.com/pricing?product=currencies) (2026-09-28) [P] | Basic free (5 calls/min, 2 years, EOD); Starter $49/month (10+ years, real-time, WebSockets) | — | **Individual only** | 10+ years | Useful independent cross-check | Basic: marginal |
| **Twelve Data** — [pricing](https://twelvedata.com/pricing) (2026-09-28) [P] | Basic free 8 credits/min (800/day); Grow $79/month; Pro $229/month | Credit system | **Individual plans are personal, non-commercial** | "20+ years" FX (marketing) | — | Yes (light) |

### 4.6 Recommended minimal stack (solo operator, private use)

| Layer | Pick | Monthly cost | Why |
|---|---|---|---|
| Prices (dealing venue) | OANDA v20 practice + live account | £0 | Parity with execution; bid/ask |
| Prices (deep history) | HistData (existing) + Dukascopy **only after licence clarification**; or Massive Currencies Starter ($49) as a licensed cross-check | £0–40 | Cross-venue validation |
| Calendar: scheduled times | Official schedules (Fed/BoE/ECB/BoJ, BLS, ONS, Eurostat) compiled into Fiboki's `InMemoryEconomicCalendar` JSON | £0 | Unblocks `EventRestriction`; licence-clean |
| Calendar: first-print actuals | ALFRED vintages (US); ONS revisions archive (UK) | £0 | Honest actual-vs-previous features |
| Calendar: consensus (optional) | Trading Economics Standard (≈$149/month, yearly), **only if** an actual-vs-consensus surprise feature is pre-registered | ≈£120 | The only affordable PIT consensus found |
| News: primary | Central-bank RSS (Fed, ECB, BoE, BoJ, SNB, RBA) | £0 | Authoritative, low volume, low injection risk |
| News: aggregate | Finnhub free (personal) or Marketaux Basic ($29); GDELT for research | £0–25 | Unscheduled-event detection |
| Positioning | CFTC COT; OANDA position book (if API-accessible) | £0 | Weekly regime context |
| Cross-asset | FRED daily series | £0 | Regime features |
| **Archive** | **Own append-only recorder** of all of the above with first-seen UTC | £0 | The only route to an honest future backtest |

**Total: about £0–£200 per month.** Everything above the £0 core is optional and should be justified by a pre-registered test.

---

## 5. Operational realities

### 5.1 LLM cost per instrument-day

List prices, 2026-09-28 [P]:

| Provider | Model | Input $/M tokens | Output $/M tokens |
|---|---|---|---|
| Anthropic ([pricing](https://platform.claude.com/docs/en/about-claude/pricing)) | Haiku 4.5 | 1 | 5 |
| Anthropic | Sonnet 5.5 | 2 | 10 |
| Anthropic | Opus 5.5 | 4 | 20 |
| OpenAI ([pricing](https://openai.com/api/pricing/)) | GPT-5.6 Luna | 0.20 | 1.20 |
| OpenAI | GPT-5.6 Terra | 2 | 12 |
| OpenAI | GPT-5.6 Sol | 5 | 30 |

- Both providers give **50% off for batch** processing.
- Anthropic cache reads cost 0.1× the base input price (0.05× on Opus 5.5).

**Illustrative arithmetic, not measured.** Two figures are my assumptions, not measurements: the token counts, and the ≈2,000 headlines a day that a news filter lets through.

| Workload | Design | Tokens / day | Small tier (Luna / Haiku) | Mid tier (Sonnet / Terra) |
|---|---|---|---|---|
| **A. Per-instrument H1 classifier** (24 calls/day × 3k in / 200 out) | Wasteful: re-reads the same news for each instrument | 72k in, 4.8k out per instrument | ≈ $0.02–0.10 per instrument-day | ≈ $0.19–0.20 |
| **B. Classify each headline once, map to instruments deterministically** (≈2,000 headlines × 400 in / 100 out) | **Recommended** | 800k in, 200k out total | ≈ $0.40–1.80/day total (≈ $12–55/month for *all* instruments) | ≈ $3.60/day |
| **C. TradingAgents-style multi-agent decision** (≈150k in / 20k out per decision) | Not recommended | per decision | ≈ $0.05–0.25 | ≈ $0.50 → ×24 H1 × 20 instruments ≈ **$240/day** |
| **D. Daily regime commentary** (1 call, 20k in / 2k out) | Operator-facing only | — | < $0.01 | ≈ $0.06 |
| **E. Historical backfill** (5 years × 250 days × 2,000 headlines, batch) | Research only; **contaminated before the cutoff** | ≈1.25B in | ≈ $275 (Luna batch) to ≈ $1,250 (Haiku batch) | — |

**Conclusion:** cost is not the constraint for design B. Contamination and evidence are.

### 5.2 Latency against the H1 bar close

- Fiboki evaluates on **closed candles**, so an annotation must exist *before* the evaluation at hh:00:00 + ε, or it is treated as missing.
- Reasoning models can take tens of seconds to minutes.
- News enrichment has its own lag: finlight's enriched stream arrives about 28 s after the raw headline [P].
- Scheduled-release price discovery completes within minutes (Andersen et al.), so on H1 the LLM is not racing the market. It is racing the bar close.

**Design rule:** every annotation carries `observed_at` (news first-seen), `available_at` (classification written) and `model_id`. The engine reads only annotations with `available_at ≤ bar_close`, and a backtest replays exactly that condition.

### 5.3 Provider reliability

- I found no authoritative uptime statistics; third-party status aggregators are not primary sources.
- The FCA reports that 98% of operational incidents notified to it relate to technology and cyber (Rathi, Jun 2026) [P].
- **Treat the LLM as an unreliable dependency:**
  - fail to the deterministic baseline, meaning no veto and no size change;
  - alert on missing annotations;
  - never let missing annotations accumulate silently.
- Fiboki's `HeartbeatWatchdog` and alert taxonomy are the right home for this.

### 5.4 Local models

Fiboki's `ModelRouter` is already local-first.

**Reported hardware tiers (July 2026)** [S] — [openclawdc](https://openclawdc.com/blog/best-local-llm-by-ram/) (2026-09-28):

| RAM | Suggested models |
|---|---|
| 16 GB | Qwen 3.5 9B or Gemma 4 12B; **gpt-oss-20b** (Apache 2.0, 21B total / 3.6B active, runs in 16 GB with MXFP4, function calling — [HF card](https://huggingface.co/openai/gpt-oss-20b) [P]) |
| 32 GB | Qwen 3.6 27B or Gemma 4 31B |
| 64–128 GB | gpt-oss-120b at Q4–Q6 |

The same source warns that tool-call JSON reliability degrades with aggressive quantisation, and HindsightBench found quantisation changes hindsight behaviour.

**Why local helps here (beyond privacy):**
- **Pinned weights.** A hashable, immutable model version means results can be reproduced and the vendor cannot silently change the model mid-evaluation.
- **A known release date**, which bounds the cutoff from above.

**Why local does not solve contamination.** A model released in August 2025 still knows 2024. And measured cutoffs can differ from stated ones (HindsightBench).

**Viability.** For headline classification (design B), a 20–30B local model on a 32–64 GB Apple-silicon machine is plausibly adequate. **This is unverified for FX-specific accuracy.** Test it against a small hand-labelled set before relying on it.

### 5.5 Backtesting an LLM signal without contamination

1. **Pin the model**: provider model ID with date, or local weights hash, plus quantisation and serving settings.
2. **Measure the effective cutoff.** Do not trust the vendor's date. Run a LAP-style date-recall probe (Gao et al.) or the HindsightBench four-arm design. Set `OOS_start = max(vendor_cutoff, measured_cutoff) + safety margin` (≥ 6–8 months, per HindsightBench).
3. **Use point-in-time inputs only**:
   - first-published timestamps (not `last_updated`);
   - first-print macro actuals (ALFRED);
   - no articles that narrate the price move (the "TSLA tumbled" leak).
   Filter out headlines containing realised-move language.
4. **Mask what can be masked.** Remove dates and absolute price levels from prompts (Glasserman-Lin style). Accept that currencies and central banks cannot be anonymised.
5. **Pre-register** before touching OOS data:
   - hypothesis;
   - consumer policy (veto-only);
   - primary metric (e.g. change in net expectancy per trade and change in max adverse excursion on vetoed vs non-vetoed trades);
   - minimum event count;
   - decision threshold;
   - trial count, added to Fiboki's honest-trial DSR ledger.
6. **Power check.** Scheduled high-impact events number roughly 100–200 a year across USD/EUR/GBP/JPY. Unscheduled shocks are far rarer. **A year of post-cutoff data may be too small to detect a modest effect.** Say so in advance, rather than extending the window after seeing results.
7. **Forward shadow is the gold standard.** Every day after deployment is uncontaminated by construction. Run it in parallel with the deterministic baseline for a pre-set period.
8. **Treat pre-cutoff backfills as exploratory only.** Never let them feed promotion.

---

## 6. Recommendation for Fiboki

### 6.1 Agent functions: now, later, never

| Function | Verdict | Rationale | Guarding |
|---|---|---|---|
| **Deterministic economic calendar** (not an agent) | **Now, top priority** | `EventRestriction` exists, but the calendar is empty, so paper bots and backtests "trade straight through FOMC and NFP". Blackouts on *scheduled* events need times, not an LLM | Official schedules + ALFRED; `assert_populated()` in paper and validation pipelines |
| **Research assistant** (existing research roles) | **Now** | Already built and structurally isolated; only the provider wiring and `register_research_handlers` are missing. Low risk, real operator value | Local-first; every LLM-originated hypothesis counts toward the DSR trial count |
| **Anomaly / incident triage** (`failure_investigator`, `data_quality_analyst`) | **Now** | Read-only by design ("the worst it can produce is a wrong diagnosis"). High value for operator trust. Evidence on trading alpha is irrelevant here | Triggered by the alert taxonomy; output is a research note only |
| **Unscheduled-event classifier** (news / event blackout) | **Now, shadow only** | The one trading-adjacent use consistent with the evidence: risk *avoidance* at H1, where direction is already priced | A new read-only role with **no tools**. Input: central-bank RSS + one news API. Output: strict schema `{event_type: enum, currencies: [enum], severity: 0–3, scheduled: bool, confidence: 0–1, source_ids}`, validated locally, written to a quarantined annotation store with `available_at`. The consumer is a deterministic policy outside `fiboki.agents` that can **only block new entries**. Shadow first (§6.3) |
| **Regime commentary** | **Now, low priority** | Cheap, operator-facing narrative. Risk: the operator anchors on fluent prose. No evidence of alpha | Never an input to signals; always labelled "commentary, not a signal" alongside the deterministic regime vector |
| **Strategy hypothesis generation** | **Now (exists), with discipline** | Useful for breadth. Risk: rediscovering published, decayed anomalies, and multiple-testing inflation | Pre-registration; honest trial counting; an AlphaAgent-style originality check against the existing library |
| **LLM-derived signal features** (bounded modifiers) | **Later**, only after a pre-registered shadow evaluation passes | Only one well-designed positive study (equities, Anic et al.); none in FX/gold/indices | Stage 1 veto → Stage 2 downsize multiplier in [0.5, 1.0] → never upsize. Constants in reviewed source |
| **LLM direction or decision signals** (TradingAgents-style) | **Not recommended** | FINSABER, the AI-Trader benchmark and the contamination literature | — |
| **Order, sizing, risk-limit or kill-switch authority** | **Never** | The cardinal rule is already enforced by five mechanisms. Nothing in the evidence argues for relaxing it; UK MAR and prompt injection argue against | Keep the absent-capability design |

### 6.2 Minimal data stack

As in §4.6:
- OANDA v20;
- official calendars + ALFRED;
- central-bank RSS + one free news API;
- CFTC COT;
- FRED;
- **a Fiboki-owned append-only recorder**.

Resolve the Dukascopy licence question before any bulk pull. Avoid scraping ForexFactory or Investing.com.

### 6.3 How to prove value cheaply before anything touches sizing

1. **Week 0: pre-register.** File a `hypothesis` + `experiment_design` artefact containing:
   - "A severity ≥ 2 unscheduled-event veto reduces adverse excursion and does not reduce net expectancy of strategy set S on instruments I";
   - metrics, minimum event count, threshold, stop date and model pin.
2. **Weeks 1–N: shadow.** The classifier runs live and writes annotations. The deterministic engine runs *unchanged*. A shadow evaluator computes, per trade, whether it would have been vetoed, and the counterfactual P&L.
   - The annotation store and evaluator are ordinary deterministic code.
   - The LLM still has zero authority.
3. **Baselines** that must be beaten on identical trades:
   - (a) no veto;
   - (b) a **deterministic** unscheduled proxy, such as a realised-volatility or spread-spike filter;
   - (c) scheduled-calendar blackout only.
   - If the LLM veto does not beat (b), it has no reason to exist.
4. **Decide at the pre-set date, not before.** Promote to paper only with operator sign-off, and still veto-only.
5. **Cost to run:** under £50 per month in API spend (design B), or zero with a local model, plus the recorder.

---

## 7. Facts, assumptions and open questions

### 7.1 Facts (primary source read)

- TradingAgents' published results are a three-month, three-stock, in-training-window test with Sharpe 5.6–8.2. The authors call these high because of few pullbacks.
- FINSABER (KDD '26): LLM strategy advantages deteriorate over 2000–2024 and 100+ symbols; conservative in bulls, aggressive in bears.
- LAP collapses to about zero after the training cutoff. FinCAD corrections cut in-sample performance by up to 67%. HindsightBench finds measured cutoffs up to eight months earlier than vendor claims.
- Anic et al. (2025) find that an LLM overlay on momentum improves OOS Sharpe after the cutoff, with 2 bp costs, in US equities.
- MAR 7A applies to UK MiFID investment firms. PRIN 2A applies to firms' retail business. PS19/18 covers spread bets.
- Vibe-Trading lets the LLM originate live orders, capped by a code-enforced mandate and kill switch. Its paper profiles declare no mandate.
- Robinhood Agentic Trading (May 2026, US) segregates agent trading into a dedicated account.
- ForexFactory, Investing.com and the Dukascopy website terms restrict automated extraction or reuse.
- The Trading Economics point-in-time calendar preserves first-release actuals and consensus.
- OANDA v20 caps a candles request at 5,000.
- Fiboki's calendar ships no dated events, and its blackout defaults to "not in blackout".

### 7.2 Assumptions (mine, stated)

- H1–D1 directional edge from scheduled news is largely priced before the bar closes (inferred from the minutes-scale price-discovery literature, not tested on Fiboki's data).
- About 2,000 relevant headlines a day and the token counts in §5.1.
- A 20–30B local model is adequate for headline event classification (untested).
- About 100–200 high-impact scheduled events a year across the majors (order of magnitude).
- Veto-only influence is lower-risk than downsizing, which is lower-risk than upsizing. This is sound reasoning, but a veto can still bias the trade sample, which is why it is measured in shadow.

### 7.3 Open questions

1. Does OANDA Europe's v20 REST API support **trading a spread-betting sub-account**, and do the API terms permit fully automated trading on it?
2. **Dukascopy**: does its licence permit bulk datafeed downloads into a private research database? Written confirmation is advisable.
3. **Perimeter**: whose capital will Fiboki trade? If it is Tom's, or pooled, or if signals are shared, authorisation issues arise and need a solicitor.
4. Trading Economics: which plan tier includes the point-in-time endpoint, and how far back does it go?
5. OANDA order/position-book availability via v20 for Fiboki's instruments, its snapshot cadence and its history.
6. Is there any credible post-cutoff, cost-inclusive study of LLM news signals in FX/gold/indices at H1–D1? **None found as of 2026-09-28.** Re-search every six months.
7. The effective (measured) cutoff of whichever model is chosen. Run a LAP or HindsightBench-style probe before any evaluation.
8. Whether a deterministic volatility or spread-spike filter already captures most of the benefit an LLM unscheduled-event veto would add. This is the decisive baseline.
9. The future format of CFTC COT after the 2026 request for comment.
10. Whether any FCA-regulated UK broker yet offers agent order routing for spread bets or CFDs comparable to Robinhood's. Finder mentions MCP connectivity at Capital.com; not verified.
