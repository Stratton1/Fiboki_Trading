# Crypto for a UK retail operator: regulation, venues and the venue Fiboki models

Researched 2026-10-10 (Saturday) for the crypto wave (`wave/crypto`). Every claim below carries
its source and the date the source states or the date it was read. Where a primary page could
not be read (behind a sign-in, or rendered by script) the secondary source is named as such and
the fact is marked **to confirm**. Nothing here is legal or tax advice; it is the factual basis
for a modelling decision.

## 1. The regulatory position for a UK retail individual

| Question | Answer on 2026-10-10 | Source |
|---|---|---|
| Can a UK retail client buy crypto derivatives (CFDs, spread bets, futures, options)? | **No.** The ban that took effect on 6 January 2021 is still in force. COBS 22.6.5R: a firm "must not sell a cryptoasset derivative or a non-UK RIE cryptoasset exchange traded note to a retail client" (nor distribute or market them where retail clients are likely to receive the marketing). Rule text dated 08/10/2025; handbook page last updated 01/01/2026. | [FCA Handbook COBS 22.6](https://www.handbook.fca.org.uk/handbook/COBS/22/6.html) (read 2026-10-10); [FCA press release, 6 Oct 2020](https://www.fca.org.uk/news/press-releases/fca-bans-sale-crypto-derivatives-retail-consumers) (ban on CFDs, options, futures and ETNs referencing unregulated transferable cryptoassets, effective 6 January 2021) |
| Has anything changed for derivatives in 2025-26? | **No.** When the FCA reopened cETNs it said: "The FCA's ban on retail access to cryptoasset derivatives will remain in place." The 30 June 2026 policy statements (PS26/9 to PS26/13) do not lift it; the FCA says it will "keep our position on retail access to cryptoasset ETNs and derivatives under review". | [FCA, "FCA opens retail access to crypto ETNs"](https://www.fca.org.uk/news/press-releases/fca-opens-retail-access-crypto-etns) (published 1 Aug 2025, updated 6 Feb 2026); [FCA, cryptoasset regime policy statements](https://www.fca.org.uk/publications/policy-statements/cryptoasset-regime) (published 30 Jun 2026) |
| Crypto ETNs (cETNs)? | **Allowed since 8 October 2025**, but only cETNs admitted to an FCA-recognised UK investment exchange (the rule still bans "non-UK RIE" cETNs). Not covered by the FSCS; Consumer Duty and financial-promotion rules apply. A cETN is a listed security bought through a stockbroker, not a 24/7 market; out of scope for this wave. | Same FCA press release and COBS 22.6.5R |
| Spot crypto? | **Permitted** through a cryptoasset firm registered with the FCA under the Money Laundering Regulations. Not FSCS-protected. | Kraken, Coinbase and Bitstamp registration rows below |
| What is changing? | The Cryptoassets Regulations were passed on 4 February 2026. The FCA's full authorisation regime applies from **25 October 2027**; existing MLR registrations do not convert automatically; the application window runs **30 September 2026 to 28 February 2027**. Retail rules for trading-platform operators go live in October 2027, with some deadlines proposed for extension into 2028 (consultation September 2026). | [FCA cryptoasset regime overview](https://www.fca.org.uk/publications/policy-statements/cryptoasset-regime); [PS26/11](https://www.fca.org.uk/publication/policy/ps26-11.pdf) |
| Margin (borrowing to buy, or to sell short) on a spot venue? | Restricted. Kraken offers margin with fiat or fiat-backed stablecoins to UK non-corporate clients only if they declare themselves high-net-worth borrowers or borrow for business purposes. Coinbase's GBP products carry `margin_enabled: false`. A retail operator should assume **no shorting** of spot crypto. | [Kraken, margin trading for UK clients](https://support.kraken.com/gb/articles/margin-trading-for-clients-in-the-united-kingdom) (updated 2 Apr 2025); `tests/fixtures/coinbase/products_2026-10-10.json` |

**Conclusion.** For a UK retail individual in October 2026 the only 24/7 crypto exposure is
**spot, unleveraged, long only**, bought outright on an FCA-registered exchange. Fiboki models
exactly that: leverage 1x, no financing, and short entries refused by the risk layer.

## 2. Venues accessible from the UK

### 2.1 Coinbase (chosen)

* **Registration.** CB Payments Ltd was added to the FCA's register of cryptoasset firms in
  February 2025 ([Cointelegraph, 3 Feb 2025](https://cointelegraph.com/news/coinbase-fca-uk-crypto-registration),
  secondary). **To confirm** on the FCA register before any real account is opened.
* **GBP pairs** (public `GET /products`, recorded 2026-10-10 in
  `tests/fixtures/coinbase/products_2026-10-10.json`): BTC, ETH, SOL, ADA, LTC, LINK, DOGE, DOT
  and BCH against GBP are `online`; **XRP-GBP is `delisted`** (`trading_disabled: true`). Every
  GBP product has `margin_enabled: false`. Minimum order `min_market_funds` GBP 0.72;
  `base_increment` 1e-8 BTC and ETH.
* **Fees.** The Advanced Trade fee page needs a sign-in
  ([help page](https://help.coinbase.com/en/coinbase/trading-and-funding/advanced-trade/advanced-trade-fees),
  read 2026-10-10, no table shown). Secondary: **0.40% maker / 0.60% taker below USD 10,000 of
  30-day volume** ([BrokerChooser, updated 1 Oct 2026](https://brokerchooser.com/broker-reviews/coinbase-review/coinbase-fees)).
  Higher tiers are cheaper; the instant-buy app is dearer. **To confirm** when logged in.
* **Liquidity** (GBP books only; mean daily GBP turnover over the 30 days to 2026-10-10, from
  public D1 candles): BTC 3.84m, ETH 1.75m, SOL 0.99m, LINK 0.46m, ADA 0.19m, DOT 0.15m,
  LTC 0.10m, DOGE 0.08m. Median top-of-book spread, six samples at ~19:27 UTC
  (`tests/fixtures/coinbase/ticker_snapshot_2026-10-10.json`): BTC 0.8 bps, ETH 2.8, SOL 2.4,
  DOGE 3.1, LINK 4.3, LTC 4.6, ADA 5.2, DOT 7.4. One Saturday evening: a snapshot, not a
  distribution.
* **Public OHLCV.** `GET https://api.exchange.coinbase.com/products/{id}/candles`, no key,
  granularities 60, 300, 900, 3600, 21600 and 86400 seconds (**no 4-hour**: H4 is resampled
  from H1), at most 300 candles per request, paged by `start`/`end`; "no data is published for
  intervals where there are no ticks"
  ([docs](https://docs.cdp.coinbase.com/exchange/reference/exchangerestapi_getproductcandles)).
  Public endpoints are limited to 10 requests per second per IP, bursts of 15
  ([rate limits](https://docs.cdp.coinbase.com/exchange/rest-api/rate-limits)).
  **History depth** (probed 2026-10-10): BTC-GBP from 2015-04-21, ETH-GBP and LTC-GBP from
  2018-09-07, LINK-GBP 2020-07-21, ADA-GBP 2021-03-18, DOGE-GBP 2021-06-03, DOT-GBP
  2021-06-16, SOL-GBP 2021-06-17. Early BTC-GBP is thin (52 of the first 300 hours traded).
* **Terms on storing data.** The [Market Data Terms](https://www.coinbase.com/legal/market_data)
  (last updated 7 August 2026) grant a limited, revocable licence for "personal or research
  purposes" of the user and its entity's officers and employees; no redistribution or display to
  third parties, no Derived Works shared outside the organisation, no index or benchmark, and no
  use to "develop, train, fine-tune, teach, validate, benchmark, or otherwise improve any
  artificial intelligence" model without written consent. Fiboki stores bars in the operator's
  own store, never commits them, and its agents read results, not train on bars; Tom's access
  should be as an officer or employee of the same entity (an operator decision, see
  `USER_ACTIONS.md`).

### 2.2 Kraken

* **Registration.** Payward Limited, FRN 928768, "Registered Cryptoasset Firm" under the MLRs;
  "Retail clients living in the United Kingdom cannot trade derivatives"; new verified retail
  accounts have a mandatory 24-hour cooling-off period
  ([Kraken, "Where is Kraken licensed or regulated?"](https://support.kraken.com/hc/en-us/articles/360001368823-Geographic-Restrictions-Can-I-use-Kraken-if-I-m-from), updated 30 Sep 2026).
* **Fees** as rendered on the [fee schedule](https://www.kraken.com/features/fee-schedule)
  on 2026-10-10 (no page date): Kraken Pro spot **0.40% maker / 0.80% taker** at USD 0+,
  0.30/0.60 from USD 2,500, 0.22/0.38 from USD 10,000. Read through a summarising fetch: **to
  confirm** on the page itself.
* **GBP pairs** (public `AssetPairs`, 2026-10-10): XBT, ETH, SOL, XRP, ADA, LTC, LINK and
  DOGE against GBP. 24-hour GBP volume at 19:18 UTC: BTC ~0.68m, ETH ~0.23m, SOL ~0.28m,
  others below 0.13m (one snapshot), about a third of Coinbase's.
* **Public OHLC** `GET /0/public/OHLC`: "Returns up to 720 of the most recent entries (older
  data cannot be retrieved, regardless of the value of since)"
  ([docs](https://docs.kraken.com/api/docs/rest-api/get-ohlc-data/)). 720 H1 bars is 30 days.
  Deep history needs Kraken's downloadable trade files, a different pipeline. **This is the
  deciding difference for a research platform.**

### 2.3 Bitstamp (Robinhood)

* Bitstamp UK Ltd, FRN 978690, registered with the FCA as a cryptoasset firm under the MLRs;
  not FSCS-protected ([Robinhood UK support](https://robinhood.com/gb/en/support/articles/trading-crypto-on-bitstamp-uk/), read 2026-10-10).
* Public `trading-pairs-info` (2026-10-10): only **BTC, ETH, XRP and LINK** against GBP, minimum
  order GBP 10. Public OHLC supports a 4-hour step. Fee schedule page did not render; **not
  verified**.

### 2.4 OANDA (the CFD broker Fiboki already uses)

OANDA's practice instruments endpoint (fixture `practice_instruments_2026-09-30.json`, 123
instruments) lists **no crypto instrument**, which is what COBS 22.6.5R requires for a retail
CFD account. OANDA bought a majority of Coinpass, an FCA-registered UK spot crypto firm
([Finextra](https://www.finextra.com/pressarticle/97807); the page gives no date). Coinpass is a
separate spot venue with no public API investigated here, and OANDA's UK professional-account
page lists no crypto CFDs ([OANDA](https://www.oanda.com/uk-en/trading/professional-account)).

## 3. Decision

**Fiboki models Coinbase UK spot (Advanced Trade), GBP quote pairs, long only, unleveraged.**

1. It is the deepest GBP book of the three, by roughly three times on BTC and ETH.
2. Its public candles reach back to each product's listing with no key, so backtests and the
   paper feed read the same venue's bars. Kraken's 720-bar cap makes it unusable for research
   without a second, file-based pipeline.
3. GBP quote pairs: the account currency is GBP, so no FX conversion is needed or guessed.
4. Short selling needs margin the operator should assume is unavailable (Coinbase GBP products
   are `margin_enabled: false`; Kraken restricts UK retail margin), so **long only** is a venue
   fact (`Instrument.shortable = False`) enforced by the risk gateway and the backtest engine,
   never by a strategy.
5. Costs are modelled at the **lowest fee tier** (0.60% taker per leg) because every modelled
   order is a market order: a round trip costs 1.2% before spread and slippage. That is the
   honest default for a new account; it is far larger than any spread, and it is the reason a
   crypto strategy must clear the stress profile, not only the ladder's 2x-spread gate.

Universe: `crypto_majors_v1` = BTC, ETH, SOL, LINK, ADA and DOT against GBP (30-day mean daily
GBP turnover at least GBP 100,000). LTC and DOGE are registered but outside it (thin books).
XRP is excluded (delisted against GBP on Coinbase).

## 4. What this does not settle (operator decisions, see `USER_ACTIONS.md`)

* Opening and verifying a Coinbase account (KYC), and confirming the fee tier when logged in.
* Whether the operators' research use falls inside "you and your entity's officers and
  employees" in Coinbase's Market Data Terms.
* **UK tax**: HMRC treats a disposal of a cryptoasset by an individual as a disposal for Capital
  Gains Tax, with share-pooling and same-day and 30-day matching rules
  ([HMRC Cryptoassets Manual, CRYPTO22200](https://www.gov.uk/hmrc-internal-manuals/cryptoassets-manual/crypto22200)).
  A strategy that trades every few days creates many disposals. Flagged only; not advice.
* Revisiting this note when the FCA regime starts (25 October 2027): venues must be authorised
  by then, and the FCA keeps retail derivatives "under review".
