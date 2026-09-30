"""Generate 100 structurally distinct strategy documents from a closed grammar.

Usage::

    python research/generate_families.py            # writes research/generated/
    python research/generate_families.py --out DIR  # anywhere else (tests)

Why a generator and not 100 files
---------------------------------
"Test 100 more strategies" has two dishonest readings. One is 100 hand-written
documents, each tuned by eye on the bars it will then be tested on. The other is
a parameter sweep relabelled as new strategies: an RSI period of 14 and of 21
are the same bet, and :func:`fiboki.research.structure.is_reparameterisation`
says so mechanically. This script does neither. It enumerates a small grammar

    FAMILY x ENTRY_EVENT x FILTER x EXIT_MODEL

in which EVERY axis is structural (a different rule, indicator, section or exit
kind), computes each document's structure hash, drops any document whose
structure hash equals another's or a seed's, and then takes a deterministic,
stratified sample of exactly 100. The sample is written as JSON documents plus a
``MANIFEST.json`` that records the grammar size before and after dedupe, the RNG
seed, every id with its structure and content hash, and the SHA-256 of this
file, so the set is reproducible byte for byte and any edit to the grammar is
visible as a changed manifest.

What it cannot do
-----------------
It cannot invent a new economic mechanism. The six family stories below were
written once, by hand, and every document in a family shares its story; the
generator only recombines entry events, filters and exits under it. A survivor
from this campaign is therefore a HYPOTHESIS that one recombination is worth
re-authoring as a seed with its own written story, never a result.

All 100 documents are ONE pre-registered campaign (K5). Each one raises the
trial count against which every other strategy in the project is deflated.

Parameters
----------
Each document declares at most three parameters, each a ``choice`` of two or
three values, so its declared grid has at most 36 cells (the largest here is
18) and the campaign runner's default grid (two values per axis, eight points)
covers every axis without the ``2 ** n_axes`` floor overriding the cap.
Filters use FIXED literals (the ADX floor of 20 and the realised-volatility band
of the seeds, the London/New York window): a filter here is a structural claim
about which market state the bet needs, not a knob.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import random
import sys
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from fiboki.core.enums import Timeframe
from fiboki.discovery.mutation import compiles, domain_feasibility
from fiboki.research.structure import structure_hash
from fiboki.strategy.dsl import (
    EventRestriction,
    ParameterSpec,
    PositionManagement,
    RuleSet,
    StopModel,
    StrategyDocument,
    StrategyFamily,
    TakeProfitLeg,
    TradeDirection,
    TrailingModel,
)
from fiboki.strategy.primitives import (
    ConstantOperand,
    CrossoverRule,
    IndicatorOperand,
    IndicatorSpec,
    IndicatorVsIndicatorRule,
    IndicatorVsPriceRule,
    ParamRef,
    PriceOperand,
    RegimeGateRule,
    SessionWindowRule,
    ThresholdRule,
)
from fiboki.strategy.registry import StrategyRegistry, load_seed_registry

REPO = Path(__file__).resolve().parents[1]
OUT = REPO / "research" / "generated"
SEED_DIR = REPO / "research" / "strategies"
MANIFEST_NAME = "MANIFEST.json"

GENERATOR_VERSION = "families-v1"
#: The pre-registered campaign these documents belong to.
CAMPAIGN = "K5"
#: Seed of the stratified sample. Recorded in the manifest; changing it is a new
#: campaign, not a re-roll of this one.
RNG_SEED = 20260930
SAMPLE_SIZE = 100
MIN_PER_FAMILY = 12
MAX_DECLARED_CELLS = 36
AUTHOR = "fiboki-v2-generator"


# ------------------------------------------------------------------ helpers
# The same three helpers as research/strategies/build_seed_documents.py.


def P(name: str) -> ParamRef:
    """A reference to a declared parameter (never a literal that can drift)."""
    return ParamRef(param=name)


def spec(name: str, **params: Any) -> IndicatorSpec:
    return IndicatorSpec(indicator=name, params=params)


def op(s: IndicatorSpec, output: str = "", offset: int = 0) -> IndicatorOperand:
    return IndicatorOperand(spec=s, output=output, offset=offset)


def choice(default: Any, values: Sequence[Any], description: str) -> ParameterSpec:
    return ParameterSpec(kind="choice", default=default, choices=tuple(values),
                         description=description)


CLOSE = PriceOperand(field="close")
ATR14 = spec("atr", period=14)
ATR_OP = op(ATR14)
ADX14 = spec("adx", period=14)
RVOL20 = spec("realised_volatility", period=20)
SWING5 = spec("swing", lookback=5)

# ---------------------------------------------------------------- universes
# Drawn only from instruments with HistData bars today (the K3 list):
# AUDJPY AUDUSD DE40 EURGBP EURJPY EURUSD GBPJPY GBPUSD NZDUSD UK100 US500
# USDCAD USDCHF USDJPY XAGUSD XAUUSD. No exotics. Every universe contains EURUSD
# so that every document can be smoke-run on the starter bars.

FX_MAJORS = ("EURUSD", "GBPUSD", "USDJPY", "AUDUSD", "USDCAD", "USDCHF", "NZDUSD")

UNIVERSES: dict[str, tuple[str, ...]] = {
    # Trend is a cross-asset claim (MOP 2012 / HOP 2017): FX, metals, indices.
    "trend_pullback": (*FX_MAJORS, "EURJPY", "GBPJPY", "XAUUSD", "XAGUSD",
                       "US500", "DE40", "UK100"),
    # Compression-then-expansion needs instruments that trend after a range;
    # the JPY crosses and metals are the classic carriers.
    "breakout_vol": (*FX_MAJORS, "EURJPY", "GBPJPY", "AUDJPY", "XAUUSD", "XAGUSD",
                     "US500", "DE40", "UK100"),
    # Liquidity-provision reversal: the deepest, most range-bound FX only. Equity
    # indices and metals are left out because their drift is the regime this
    # family must not trade against.
    "mean_reversion_band": (*FX_MAJORS, "EURGBP"),
    # Oscillator momentum: FX majors and crosses plus gold.
    "momentum_oscillator": (*FX_MAJORS, "EURJPY", "GBPJPY", "AUDJPY", "EURGBP", "XAUUSD"),
    # Ichimoku's home market is the yen; majors, gold and US500 for breadth.
    "ichimoku_variants": ("USDJPY", "EURJPY", "GBPJPY", "AUDJPY", "EURUSD", "GBPUSD",
                          "AUDUSD", "XAUUSD", "US500"),
    # The London open concentrates EUR, GBP and CHF flow and the European indices.
    "session_breakout": ("EURUSD", "GBPUSD", "EURGBP", "USDCHF", "EURJPY", "GBPJPY",
                         "DE40", "UK100"),
}

TIMEFRAMES: dict[str, tuple[Timeframe, ...]] = {
    family: (Timeframe.H4, Timeframe.D1) for family in UNIVERSES
}
# H4 is a poor fit for a London-open breakout (the 08:00 bar closes at 12:00,
# long after the open); H1 is the honest timeframe and H4 is carried only so
# the campaign can run it on the H4 store. D1 has no intraday session at all.
TIMEFRAMES["session_breakout"] = (Timeframe.H1, Timeframe.H4)

FAMILY_ENUM: dict[str, StrategyFamily] = {
    "trend_pullback": StrategyFamily.TREND_FOLLOWING,
    "breakout_vol": StrategyFamily.BREAKOUT,
    "mean_reversion_band": StrategyFamily.MEAN_REVERSION,
    "momentum_oscillator": StrategyFamily.MOMENTUM,
    "ichimoku_variants": StrategyFamily.ICHIMOKU,
    "session_breakout": StrategyFamily.BREAKOUT,
}

#: Uniform across the campaign, as in the seeds: the blackout is not an axis.
EVENTS = EventRestriction(block_minutes_before=30, block_minutes_after=30,
                          blocked_event_tags=("nfp", "cpi", "central_bank_rate"))


# ------------------------------------------------------------ family stories
# Written once each, by hand. Every document in a family carries its family's
# text verbatim plus one sentence naming its grammar cell.

HYPOTHESES: dict[str, str] = {
    "trend_pullback": (
        "ECONOMIC STORY. Buy a dip inside an established trend. The trend state is price "
        "on the right side of a slow moving average, which is the cheapest expression of "
        "time-series momentum: Moskowitz, Ooi and Pedersen (2012) document it across 58 "
        "futures and forwards and Hurst, Ooi and Pedersen (2017) back to 1880, with slow "
        "information diffusion and hedging and risk-management flows as the proposed "
        "mechanisms. The entry waits for price to fall back to a fast average (or to the "
        "lower Keltner band) and cross back over it, so the position is opened when a "
        "counter-trend move is being absorbed rather than at an extended price. The "
        "claim is that entry TIMING inside a trend improves the reward-to-risk of a trend "
        "bet by placing the stop closer to a level the market has just defended.\n\n"
        "EVIDENCE AGAINST, STATED PLAINLY. Moving-average rules are the family with the "
        "worst record once data snooping is priced in: Brock, Lakonishok and LeBaron (1992) "
        "is the canonical positive result and the canonical example of one that shrank "
        "under White's Reality Check (Sullivan, Timmermann and White 1999); Coakley, "
        "Marzano and Nankervis (2016) find FX technical rules do not survive Step-SPA; "
        "Olson (2004) and Neely, Weller and Ulrich (2009) find FX trading-rule profits "
        "declined through time as markets adapted. There is NO published evidence that the "
        "pullback timing adds anything over a plain trend filter; it may only reduce the "
        "number of trades and so the power to detect anything. Trend-following returns "
        "have decayed since 2009. Expected edge before costs is small and may be zero."
    ),
    "breakout_vol": (
        "ECONOMIC STORY. Volatility clusters (Engle 1982; Bollerslev 1986) and is "
        "forecastable (Andersen and Bollerslev 1998), so a period of unusually low "
        "realised volatility is, with better than even odds, followed by a period of higher "
        "volatility. The bet is that when that expansion starts with a break of the prior "
        "range, the break carries direction, for the same reasons a time-series momentum "
        "signal does (slow diffusion, stop and hedging flows at range edges). The setup is "
        "a compression state: 20-bar realised volatility below the 100-bar figure (a proxy "
        "for 'below its 100-bar median'; the library has no rolling percentile indicator, "
        "and an absolute threshold on per-bar volatility would mean different things on "
        "H4 and D1 and on EURUSD and XAUUSD, so the comparison is relative) AND a "
        "Bollinger-inside-Keltner squeeze on the prior bar. That compression setup is what "
        "makes this a different document from donchian_breakout_atr, whose setup is a "
        "trend EMA and whose regime is an absolute volatility band.\n\n"
        "EVIDENCE AGAINST, STATED PLAINLY. Volatility forecastability says nothing about "
        "the SIGN of the next move; the direction of a post-squeeze break is an empirical "
        "claim with no peer-reviewed support. The squeeze is practitioner lore (Bollinger "
        "2001; Carter 2005), not a tested result. Park and Irwin (2007) survey the "
        "technical-analysis literature and find that most positive results are "
        "compromised by data snooping and ex-post rule selection; channel breakouts on FX "
        "specifically fail Step-SPA (Coakley, Marzano and Nankervis 2016). Compression "
        "states are also where false breaks cluster, so the stop is hit more often exactly "
        "when the setup is present. Expect a low hit rate and a result dominated by a few "
        "large trends."
    ),
    "mean_reversion_band": (
        "ECONOMIC STORY. Short-horizon reversal is compensation for supplying liquidity "
        "when it is scarce: Nagel (2012) shows reversal returns scale with funding stress, "
        "and Jegadeesh (1990) and Lo and MacKinlay (1990) document short-horizon reversal "
        "in equities. The trade is specified as a RE-ENTRY: price closes back inside a "
        "Bollinger or Keltner band having closed outside it, so the position supplies "
        "liquidity after the imbalance has started to correct rather than into it. The "
        "setup is a range-bound market, ADX(14) below a ceiling (Wilder 1978), because "
        "fading a band break during a trend is a slow way to lose money.\n\n"
        "EVIDENCE AGAINST, STATED PLAINLY. The liquidity premium is measured on US equities "
        "at daily frequency with near-zero spread; in FX and CFDs every leg pays a spread "
        "that is large relative to a reversion to the band midline. Oscillator and band "
        "rules belong to the family that does not survive Step-SPA on currencies (Coakley, "
        "Marzano and Nankervis 2016). The payoff is structurally negative-skewed (many small "
        "wins, occasional large losses when the ADX regime call is wrong), which flatters "
        "Sharpe and hides tail risk; rank on tail-aware statistics. This family overlaps "
        "rsi_band_mean_reversion in idea, which is a reason for suspicion if both appear to "
        "work: they are close to one bet counted twice."
    ),
    "momentum_oscillator": (
        "ECONOMIC STORY. Momentum is among the best-documented return anomalies, "
        "cross-sectionally (Jegadeesh and Titman 1993) and in time series (Moskowitz, Ooi "
        "and Pedersen 2012). These documents take the momentum STATE from a slow "
        "oscillator (MACD histogram sign, or RSI above or below 50) and the TIMING from a "
        "fast one: a stochastic %K crossing %D out of an extreme zone (a pullback being "
        "bought inside positive momentum) or CCI crossing +100 or -100, which is how "
        "Lambert (1980) designed CCI to be used (a thrust into a new trend). The claim is "
        "that a fast oscillator improves entry timing within a momentum state.\n\n"
        "EVIDENCE AGAINST, STATED PLAINLY. Stochastic and CCI are practitioner tools with no "
        "peer-reviewed evidence of predictive power in FX; oscillator rules are part of the "
        "universe that fails Step-SPA in Coakley, Marzano and Nankervis (2016), and Hsu, "
        "Taylor and Wang (2016) find FX technical-rule predictability concentrated in "
        "emerging-market currencies and declining in developed ones -- the pairs traded "
        "here. The stochastic variant mixes a momentum state with a short-term reversal "
        "trigger, two effects of opposite sign, so a positive result could come from "
        "either and would not identify the mechanism. Momentum is also subject to severe "
        "crashes after reversals (Daniel and Moskowitz 2016)."
    ),
    "ichimoku_variants": (
        "ECONOMIC STORY. Stripped of folklore, Ichimoku's lines are Donchian midpoints over "
        "9, 26 and 52 bars with a 26-bar displacement; price above the cloud is a compact "
        "statement that price sits above its medium- and long-horizon equilibrium, which "
        "is a time-series momentum state (Moskowitz, Ooi and Pedersen 2012). These "
        "documents keep the kumo as the state and vary the EVENT that times entry: the "
        "tenkan/kijun cross, the causal chikou flip (close crossing above its own value "
        "26 bars ago), or price crossing the kijun with chikou confirmation. They differ "
        "structurally from the seed ichimoku_kumo_trend (no tenkan-above-kijun setup, no "
        "ADX regime section, no kijun-level stop, different exits).\n\n"
        "EVIDENCE AGAINST, STATED PLAINLY. Deng, Sakurai and Ueda (2021) test Ichimoku "
        "rules on major currency pairs and find no significant profitability once data "
        "snooping is accounted for, and the moving-average family Ichimoku belongs to "
        "fails Step-SPA on FX (Coakley, Marzano and Nankervis 2016). The honest prior is "
        "an edge of approximately zero before costs and negative after spread. Worse for "
        "this family: these are recombinations of the seed's own pieces, so testing them "
        "is close to re-testing the seed with more tries, and any survivor must be read "
        "against the seed's result and the enlarged trial count, not on its own."
    ),
    "session_breakout": (
        "ECONOMIC STORY. FX activity and volatility have a strong intraday pattern, with a "
        "sharp rise at the European open (Andersen and Bollerslev 1998; Ito and Hashimoto "
        "2006), and exchange-rate moves are driven by order flow (Evans and Lyons 2002). "
        "The bet is that the first hours of London trading resolve the imbalance "
        "accumulated in the thin Asian session, so a break of the prior short range, taken "
        "only inside the London-open window (07:00-10:00 UTC) and only when short-horizon "
        "ATR is expanding relative to its 24-bar value, carries direction. Holmberg, "
        "Lonnbark and Lundstrom (2013) report opening-range breakout profits in crude oil "
        "futures. The timeframe is H1; H4 is declared only so the campaign can run it on "
        "the H4 store, and on H4 the 08:00 bar closes at 12:00, after most of the move this "
        "story is about, so an H4 result is a weak test of the idea in either direction.\n\n"
        "EVIDENCE AGAINST, STATED PLAINLY. The volatility seasonality is well documented; "
        "its DIRECTION is not, and nothing in the cited microstructure work says a range "
        "break at the open predicts continuation. Holmberg et al. is one market and one "
        "sample. Marshall, Cahan and Cahan (2008) find no value in intraday technical rules "
        "in US equities once costs and snooping are addressed. Spreads are widest in the "
        "hour before London and the stop distances here are small, so costs are a large "
        "fraction of risk. The order flow the story relies on is not observable in these "
        "bars; the break is only a proxy for it."
    ),
}


# ------------------------------------------------------------------ grammar


@dataclass(frozen=True)
class Parts:
    """The side-specific rules and declared parameters of one family x entry."""

    setup_long: tuple[Any, ...]
    setup_short: tuple[Any, ...]
    entry_long: tuple[Any, ...]
    entry_short: tuple[Any, ...]
    confirmation_long: tuple[Any, ...] = ()
    confirmation_short: tuple[Any, ...] = ()
    parameters: tuple[tuple[str, ParameterSpec], ...] = ()


def _slow_ma(kind: str) -> IndicatorSpec:
    return spec(kind, period=P("slow_period"))


SLOW_PERIOD = ("slow_period", choice(200, (100, 200), "Slow average: the trend state"))
FAST_PERIOD = ("fast_period", choice(20, (10, 20), "Fast average the pullback re-crosses"))


def _trend_pullback(entry: str) -> Parts:
    ma = "sma" if entry == "sma_recross" else "ema"
    slow = _slow_ma(ma)
    setup_l = (IndicatorVsPriceRule(indicator=op(slow), comparator="<", price=CLOSE),)
    setup_s = (IndicatorVsPriceRule(indicator=op(slow), comparator=">", price=CLOSE),)
    if entry in ("ema_recross", "sma_recross"):
        fast = spec(ma, period=P("fast_period"))
        return Parts(
            setup_l, setup_s,
            (CrossoverRule(fast=CLOSE, slow=op(fast), direction="above"),),
            (CrossoverRule(fast=CLOSE, slow=op(fast), direction="below"),),
            parameters=(SLOW_PERIOD, FAST_PERIOD),
        )
    kc = spec("keltner", ema_period=20, atr_period=10, multiple=P("kc_multiple"))
    return Parts(
        setup_l, setup_s,
        (CrossoverRule(fast=CLOSE, slow=op(kc, "lower"), direction="above"),),
        (CrossoverRule(fast=CLOSE, slow=op(kc, "upper"), direction="below"),),
        parameters=(SLOW_PERIOD,
                    ("kc_multiple", choice(1.5, (1.0, 1.5), "Keltner band the pullback touches"))),
    )


SQUEEZE_BB = spec("bollinger", period=20, num_std=2.0)


def _breakout_vol(entry: str) -> Parts:
    rv_short = spec("realised_volatility", period=P("compression_window"))
    rv_long = spec("realised_volatility", period=100)
    kc = spec("keltner", ema_period=20, atr_period=10, multiple=1.5)
    compression = (
        IndicatorVsIndicatorRule(left=op(rv_short), right=op(rv_long), comparator="<"),
        # Squeeze on the PRIOR bar: the break bar itself widens the Bollinger band.
        IndicatorVsIndicatorRule(left=op(SQUEEZE_BB, "upper", 1), right=op(kc, "upper", 1),
                                 comparator="<"),
        IndicatorVsIndicatorRule(left=op(SQUEEZE_BB, "lower", 1), right=op(kc, "lower", 1),
                                 comparator=">"),
    )
    window = ("compression_window",
              choice(20, (10, 20), "Short realised-vol window compared with 100 bars"))
    if entry == "kc_break":
        return Parts(
            compression, compression,
            (CrossoverRule(fast=CLOSE, slow=op(kc, "upper"), direction="above"),),
            (CrossoverRule(fast=CLOSE, slow=op(kc, "lower"), direction="below"),),
            parameters=(window,),
        )
    don = spec("donchian", period=P("channel_period"))
    channel = ("channel_period", choice(20, (10, 20, 40), "Prior range whose break is the entry"))
    if entry == "donchian_close_break":
        return Parts(
            compression, compression,
            (IndicatorVsPriceRule(indicator=op(don, "upper_prior"), comparator="<", price=CLOSE),),
            (IndicatorVsPriceRule(indicator=op(don, "lower_prior"), comparator=">", price=CLOSE),),
            parameters=(window, channel),
        )
    return Parts(  # donchian_fresh_cross: the FIRST close beyond the prior range
        compression, compression,
        (CrossoverRule(fast=CLOSE, slow=op(don, "upper_prior"), direction="above"),),
        (CrossoverRule(fast=CLOSE, slow=op(don, "lower_prior"), direction="below"),),
        parameters=(window, channel),
    )


def _mean_reversion_band(entry: str) -> Parts:
    ranging = (ThresholdRule(operand=op(ADX14), comparator="<", value=P("adx_ceiling")),)
    ceiling = ("adx_ceiling", choice(25.0, (20.0, 25.0), "Range-regime ceiling on ADX(14)"))
    if entry == "bb_reentry":
        band = spec("bollinger", period=20, num_std=P("bb_num_std"))
        width = ("bb_num_std", choice(2.0, (2.0, 2.5), "Bollinger band width"))
    else:  # kc_reentry
        band = spec("keltner", ema_period=20, atr_period=10, multiple=P("kc_multiple"))
        width = ("kc_multiple", choice(2.0, (1.5, 2.0), "Keltner band width"))
    return Parts(
        ranging, ranging,
        (CrossoverRule(fast=CLOSE, slow=op(band, "lower"), direction="above"),),
        (CrossoverRule(fast=CLOSE, slow=op(band, "upper"), direction="below"),),
        parameters=(ceiling, width),
    )


MACD = spec("macd", fast=12, slow=26, signal=9)
ZERO = ConstantOperand(value=0.0)


def _momentum_oscillator(entry: str) -> Parts:
    params: list[tuple[str, ParameterSpec]] = []
    if entry == "rsi_stoch":
        rsi = spec("rsi", period=P("rsi_period"))
        setup_l = (ThresholdRule(operand=op(rsi), comparator=">", value=50.0),)
        setup_s = (ThresholdRule(operand=op(rsi), comparator="<", value=50.0),)
        params.append(("rsi_period", choice(14, (9, 14), "RSI whose side of 50 is the state")))
    else:
        setup_l = (ThresholdRule(operand=op(MACD, "hist"), comparator=">", value=0.0),)
        setup_s = (ThresholdRule(operand=op(MACD, "hist"), comparator="<", value=0.0),)
    if entry == "macd_cci":
        cci = spec("cci", period=P("cci_period"))
        params.append(("cci_period", choice(20, (14, 20), "CCI lookback")))
        return Parts(
            setup_l, setup_s,
            (CrossoverRule(fast=op(cci), slow=ConstantOperand(value=100.0), direction="above"),),
            (CrossoverRule(fast=op(cci), slow=ConstantOperand(value=-100.0), direction="below"),),
            parameters=tuple(params),
        )
    stoch = spec("stochastic", k_period=P("stoch_k"), smooth=3, d_period=3)
    params.append(("stoch_k", choice(14, (9, 14), "Stochastic %K lookback")))
    return Parts(
        setup_l, setup_s,
        (
            CrossoverRule(fast=op(stoch, "k"), slow=op(stoch, "d"), direction="above"),
            ThresholdRule(operand=op(stoch, "k", 1), comparator="<", value=20.0),
        ),
        (
            CrossoverRule(fast=op(stoch, "k"), slow=op(stoch, "d"), direction="below"),
            ThresholdRule(operand=op(stoch, "k", 1), comparator=">", value=80.0),
        ),
        parameters=tuple(params),
    )


ICHI = spec("ichimoku", tenkan_period=9, kijun_period=P("kijun_period"),
            senkou_b_period=52, senkou_shift=26, chikou_shift=26)


def _ichimoku_variants(entry: str) -> Parts:
    setup_l = (IndicatorVsPriceRule(indicator=op(ICHI, "cloud_top"), comparator="<", price=CLOSE),)
    setup_s = (IndicatorVsPriceRule(indicator=op(ICHI, "cloud_bottom"), comparator=">",
                                    price=CLOSE),)
    kijun = ("kijun_period", choice(26, (22, 26, 30), "Kijun (base line) lookback"))
    chikou = op(ICHI, "chikou_above_price")
    half = ConstantOperand(value=0.5)
    if entry == "kumo_tk_cross":
        return Parts(
            setup_l, setup_s,
            (CrossoverRule(fast=op(ICHI, "tenkan"), slow=op(ICHI, "kijun"), direction="above"),),
            (CrossoverRule(fast=op(ICHI, "tenkan"), slow=op(ICHI, "kijun"), direction="below"),),
            parameters=(kijun,),
        )
    if entry == "kumo_chikou_flip":
        return Parts(
            setup_l, setup_s,
            (CrossoverRule(fast=chikou, slow=half, direction="above"),),
            (CrossoverRule(fast=chikou, slow=half, direction="below"),),
            parameters=(kijun,),
        )
    return Parts(  # kumo_kijun_cross, confirmed by chikou
        setup_l, setup_s,
        (CrossoverRule(fast=CLOSE, slow=op(ICHI, "kijun"), direction="above"),),
        (CrossoverRule(fast=CLOSE, slow=op(ICHI, "kijun"), direction="below"),),
        (ThresholdRule(operand=chikou, comparator=">", value=0.5),),
        (ThresholdRule(operand=chikou, comparator="<", value=0.5),),
        parameters=(kijun,),
    )


#: London open, 07:00-10:00 UTC inclusive, Monday to Friday.
LONDON_OPEN = SessionWindowRule(start_hour_utc=7, end_hour_utc=10, weekdays=(0, 1, 2, 3, 4))
ATR_FAST = spec("atr", period=6)
ATR_DAY = spec("atr", period=24)


def _session_breakout(entry: str) -> Parts:
    don = spec("donchian", period=P("range_bars"))
    setup = (LONDON_OPEN,)
    expanding = (IndicatorVsIndicatorRule(left=op(ATR_FAST), right=op(ATR_DAY), comparator=">"),)
    rng = ("range_bars", choice(6, (4, 6, 8), "Bars in the prior range (the Asian range on H1)"))
    if entry == "range_close_break":
        long_e = (IndicatorVsPriceRule(indicator=op(don, "upper_prior"), comparator="<",
                                       price=CLOSE),)
        short_e = (IndicatorVsPriceRule(indicator=op(don, "lower_prior"), comparator=">",
                                        price=CLOSE),)
    else:  # range_fresh_cross
        long_e = (CrossoverRule(fast=CLOSE, slow=op(don, "upper_prior"), direction="above"),)
        short_e = (CrossoverRule(fast=CLOSE, slow=op(don, "lower_prior"), direction="below"),)
    return Parts(setup, setup, long_e, short_e, expanding, expanding, parameters=(rng,))


@dataclass(frozen=True)
class Family:
    name: str
    code: str
    build: Callable[[str], Parts]
    entries: dict[str, str]
    filters: tuple[str, ...]


FAMILIES: tuple[Family, ...] = (
    Family("trend_pullback", "tp", _trend_pullback, {
        "ema_recross": "slow EMA trend state, close re-crossing a fast EMA",
        "sma_recross": "slow SMA trend state, close re-crossing a fast SMA",
        "kc_recross": "slow EMA trend state, close re-crossing the Keltner band after a touch",
    }, ("none", "rvol_gate", "adx_gate", "session")),
    Family("breakout_vol", "bv", _breakout_vol, {
        "donchian_close_break": "close beyond the prior Donchian range after compression",
        "donchian_fresh_cross": "first close across the prior Donchian range after compression",
        "kc_break": "close crossing the Keltner band after compression",
    }, ("none", "rvol_gate", "adx_gate", "session")),
    # No ADX filter: the family's setup IS an ADX ceiling, and an ADX floor gate
    # would contradict it (or, set below the ceiling, merely restate it).
    Family("mean_reversion_band", "mr", _mean_reversion_band, {
        "bb_reentry": "close back inside the Bollinger band",
        "kc_reentry": "close back inside the Keltner band",
    }, ("none", "rvol_gate", "session")),
    Family("momentum_oscillator", "mo", _momentum_oscillator, {
        "macd_stoch": "MACD histogram sign state, stochastic cross out of 20/80",
        "rsi_stoch": "RSI side-of-50 state, stochastic cross out of 20/80",
        "macd_cci": "MACD histogram sign state, CCI crossing +100/-100",
    }, ("none", "rvol_gate", "adx_gate", "session")),
    Family("ichimoku_variants", "ic", _ichimoku_variants, {
        "kumo_tk_cross": "close beyond the kumo, tenkan/kijun cross",
        "kumo_chikou_flip": "close beyond the kumo, causal chikou flip",
        "kumo_kijun_cross": "close beyond the kumo, close crossing kijun with chikou confirmation",
    }, ("none", "rvol_gate", "adx_gate", "session")),
    Family("session_breakout", "sb", _session_breakout, {
        "range_close_break": "London-open close beyond the prior short range, ATR expanding",
        "range_fresh_cross": "London-open first close across the prior short range, ATR expanding",
    }, ("none", "rvol_gate", "adx_gate", "session")),
)

FILTER_TEXT = {
    "none": "no regime filter",
    "rvol_gate": "realised-volatility(20) regime gate in [0.0015, 0.05] per bar, the seeds' band",
    "adx_gate": "ADX(14) >= 20 regime gate",
    "session": "session filter",
}
SESSION_FILTER_TEXT = {
    "session_breakout": "Tuesday-to-Thursday only (the window is already the setup)",
    "default": "07:00-20:00 UTC London/New York window, H4 only (no intraday hours on D1)",
}

#: ``swing_tp`` is realised as two ONE-SIDED documents. StopModel carries a
#: single ``level`` operand for both sides, so a both-sided document cannot put
#: the long stop under the last swing low and the short stop over the last swing
#: high; a long-side level used for shorts would silently fall back to the
#: minimum-distance floor on every short. See the module report.
EXITS = ("time_exit", "tp_r", "trail", "swing_tp_long", "swing_tp_short")
EXIT_TEXT = {
    "time_exit": "3xATR(14) disaster stop plus a fixed holding period (time exit)",
    "tp_r": "2xATR(14) stop plus one take-profit leg at an R multiple",
    "trail": "2xATR(14) stop plus an ATR chandelier trail",
    "swing_tp_long": "long-only; stop under the last confirmed swing low, one R-multiple target",
    "swing_tp_short": "short-only; stop over the last confirmed swing high, one R-multiple target",
}


def _filter_rules(family: str, name: str) -> tuple[tuple[Any, ...], tuple[Any, ...]]:
    """(regime, filters) rules for one filter cell."""
    if name == "none":
        return (), ()
    if name == "rvol_gate":
        return (RegimeGateRule(metric=op(RVOL20), min_value=0.0015, max_value=0.05),), ()
    if name == "adx_gate":
        return (RegimeGateRule(metric=op(ADX14), min_value=20.0),), ()
    if family == "session_breakout":
        return (), (SessionWindowRule(start_hour_utc=0, end_hour_utc=23, weekdays=(1, 2, 3)),)
    return (), (SessionWindowRule(start_hour_utc=7, end_hour_utc=20, weekdays=(0, 1, 2, 3, 4)),)


TP_R = ("tp_r", choice(2.0, (1.5, 2.0, 3.0), "Take-profit distance in R"))


def _exit_parts(name: str) -> dict[str, Any]:
    if name == "time_exit":
        return {
            "stop": StopModel(kind="atr_multiple", value=3.0, atr=ATR_OP, min_distance_atr=0.5),
            "pm": PositionManagement(max_concurrent_positions=1,
                                     max_bars_in_trade=P("hold_bars")),
            "params": (("hold_bars", choice(20, (10, 20, 40), "Fixed holding period in bars")),),
        }
    if name == "tp_r":
        return {
            "stop": StopModel(kind="atr_multiple", value=2.0, atr=ATR_OP, min_distance_atr=0.5),
            "tps": (TakeProfitLeg(kind="r_multiple", value=P("tp_r"), allocation=1.0,
                                  label="target"),),
            "params": (TP_R,),
        }
    if name == "trail":
        return {
            "stop": StopModel(kind="atr_multiple", value=2.0, atr=ATR_OP, min_distance_atr=0.5),
            "trailing": TrailingModel(kind="atr_chandelier", value=P("trail_atr"),
                                      activate_after_r=0.0, atr=ATR_OP),
            "params": (("trail_atr", choice(3.0, (2.0, 3.0, 4.0), "Chandelier distance in ATR")),),
        }
    level = "last_swing_low" if name == "swing_tp_long" else "last_swing_high"
    return {
        "stop": StopModel(kind="swing_structure", value=1.0, level=op(SWING5, level), atr=ATR_OP,
                          buffer_atr=0.25, min_distance_atr=0.5),
        "tps": (TakeProfitLeg(kind="r_multiple", value=P("tp_r"), allocation=1.0,
                              label="target"),),
        "params": (TP_R,),
    }


@dataclass(frozen=True)
class Cell:
    family: Family
    entry: str
    filter: str
    exit: str

    @property
    def stem(self) -> str:
        return f"{self.family.name}__{self.entry}__{self.filter}__{self.exit}"

    @property
    def strategy_id(self) -> str:
        return f"gen_{self.family.code}_{self.entry}_{self.filter}_{self.exit}"


def build_document(cell: Cell) -> StrategyDocument:
    fam = cell.family
    parts = fam.build(cell.entry)
    regime, filters = _filter_rules(fam.name, cell.filter)
    exit_ = _exit_parts(cell.exit)

    direction = {
        "swing_tp_long": TradeDirection.LONG,
        "swing_tp_short": TradeDirection.SHORT,
    }.get(cell.exit, TradeDirection.BOTH)
    keep_long = direction in (TradeDirection.LONG, TradeDirection.BOTH)
    keep_short = direction in (TradeDirection.SHORT, TradeDirection.BOTH)

    def sides(long: tuple[Any, ...], short: tuple[Any, ...]) -> RuleSet:
        return RuleSet(long=long if keep_long else (), short=short if keep_short else ())

    timeframes = TIMEFRAMES[fam.name]
    if cell.filter == "session" and fam.name != "session_breakout":
        timeframes = (Timeframe.H4,)

    params = dict(parts.parameters) | dict(exit_["params"])
    session_text = SESSION_FILTER_TEXT.get(fam.name, SESSION_FILTER_TEXT["default"])
    filter_text = session_text if cell.filter == "session" else FILTER_TEXT[cell.filter]
    cell_sentence = (
        f"\n\nGRAMMAR CELL. Entry '{cell.entry}' ({fam.entries[cell.entry]}); filter "
        f"'{cell.filter}' ({filter_text}); exit '{cell.exit}' ({EXIT_TEXT[cell.exit]}). "
        f"Generated by {GENERATOR_VERSION} as one of {SAMPLE_SIZE} documents pre-registered "
        f"together as campaign {CAMPAIGN}; a survivor is a hypothesis to re-author as a seed, "
        "not a result."
    )
    return StrategyDocument(
        strategy_id=cell.strategy_id,
        name=f"Generated {fam.name} / {cell.entry} / {cell.filter} / {cell.exit}",
        family=FAMILY_ENUM[fam.name],
        hypothesis=HYPOTHESES[fam.name] + cell_sentence,
        universe=UNIVERSES[fam.name],
        timeframes=timeframes,
        direction=direction,
        regime=regime,
        setup=sides(parts.setup_long, parts.setup_short),
        entry=sides(parts.entry_long, parts.entry_short),
        confirmation=sides(parts.confirmation_long, parts.confirmation_short),
        filters=filters,
        stop=exit_["stop"],
        take_profits=exit_.get("tps", ()),
        trailing=exit_.get("trailing"),
        position_management=exit_.get("pm", PositionManagement(max_concurrent_positions=1)),
        sessions=None,
        events=EVENTS,
        parameters={k: params[k] for k in sorted(params)},
        author=AUTHOR,
        notes=(
            f"grammar cell family={fam.name} entry={cell.entry} filter={cell.filter} "
            f"exit={cell.exit}; generator {GENERATOR_VERSION}, rng_seed {RNG_SEED}, "
            f"campaign {CAMPAIGN}. Generated, not hand-written: do not edit, regenerate."
        ),
    )


def grammar() -> list[Cell]:
    """Every cell of the grammar, in a fixed order."""
    return [
        Cell(fam, entry, flt, exit_)
        for fam in FAMILIES
        for entry in fam.entries
        for flt in fam.filters
        for exit_ in EXITS
    ]


def declared_cells(document: StrategyDocument) -> int:
    total = 1
    for p in document.parameters.values():
        total *= len(p.domain())
    return total


# ------------------------------------------------------------------ generate


@dataclass(frozen=True)
class Generated:
    documents: dict[str, StrategyDocument]  # file stem -> document, the sample
    manifest: dict[str, Any]


def _check(document: StrategyDocument) -> None:
    """A grammar cell that is not a healthy strategy is a bug in the grammar."""
    ok, why = compiles(document)
    if not ok:
        raise RuntimeError(f"{document.strategy_id} does not compile: {why}")
    cells = declared_cells(document)
    if cells > MAX_DECLARED_CELLS:
        raise RuntimeError(f"{document.strategy_id} declares {cells} cells > {MAX_DECLARED_CELLS}")
    feasibility = domain_feasibility(document, max_cells=MAX_DECLARED_CELLS,
                                     max_values_per_axis=3)
    if not (feasibility.feasible and feasibility.exhaustive):
        raise RuntimeError(f"{document.strategy_id}: {feasibility.describe()}")
    registry = StrategyRegistry()
    registry.register(document)
    report = registry.health_check()
    bad = [i for i in report.issues
           if i.severity == "error" or i.code in ("unmanaged_runner", "stop_only_exit")]
    if bad:
        raise RuntimeError(f"{document.strategy_id} is unhealthy: {bad}")


def generate(seed_dir: Path = SEED_DIR) -> Generated:
    seeds = {d.strategy_id: structure_hash(d) for d in load_seed_registry(seed_dir)}
    seed_hashes = {h: sid for sid, h in seeds.items()}

    cells = grammar()
    kept: dict[str, tuple[Cell, StrategyDocument, str]] = {}
    by_hash: dict[str, str] = {}
    dropped: list[dict[str, str]] = []
    for cell in cells:
        document = build_document(cell)
        if len(document.strategy_id) > 64:
            raise RuntimeError(f"strategy_id too long: {document.strategy_id}")
        _check(document)
        s_hash = structure_hash(document)
        if s_hash in seed_hashes:
            dropped.append({"cell": cell.stem, "reason": f"structure hash of seed {seed_hashes[s_hash]}"})
            continue
        if s_hash in by_hash:
            dropped.append({"cell": cell.stem, "reason": f"structure hash of cell {by_hash[s_hash]}"})
            continue
        by_hash[s_hash] = cell.stem
        kept[cell.stem] = (cell, document, s_hash)

    # Stratified, deterministic sample: MIN_PER_FAMILY from each family, then the
    # rest from the pooled remainder. Everything is drawn from sorted lists by a
    # seeded RNG, so the sample is a pure function of the grammar and the seed.
    rng = random.Random(RNG_SEED)
    chosen: list[str] = []
    for fam in FAMILIES:
        pool = sorted(stem for stem, (c, _, _) in kept.items() if c.family.name == fam.name)
        if len(pool) < MIN_PER_FAMILY:
            raise RuntimeError(f"{fam.name}: only {len(pool)} distinct documents")
        chosen.extend(rng.sample(pool, MIN_PER_FAMILY))
    rest = sorted(set(kept) - set(chosen))
    chosen.extend(rng.sample(rest, SAMPLE_SIZE - len(chosen)))
    chosen.sort()

    documents: dict[str, StrategyDocument] = {}
    entries = []
    for stem in chosen:
        cell, document, s_hash = kept[stem]
        file_stem = f"{stem}__{s_hash[:8]}"
        documents[file_stem] = document
        entries.append({
            "file": f"{file_stem}.json",
            "strategy_id": document.strategy_id,
            "family": cell.family.name,
            "entry": cell.entry,
            "filter": cell.filter,
            "exit": cell.exit,
            "structure_hash": s_hash,
            "content_hash": document.content_hash(),
            "declared_cells": declared_cells(document),
        })

    def per_family(stems: Sequence[str]) -> dict[str, int]:
        return {f.name: sum(1 for s in stems if kept[s][0].family.name == f.name)
                for f in FAMILIES}

    manifest = {
        "generator": "research/generate_families.py",
        "generator_version": GENERATOR_VERSION,
        "generator_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "campaign": CAMPAIGN,
        "rng_seed": RNG_SEED,
        "sample_size": SAMPLE_SIZE,
        "min_per_family": MIN_PER_FAMILY,
        "grammar_size_before_dedupe": len(cells),
        "grammar_size_after_dedupe": len(kept),
        "dropped": dropped,
        "grammar_per_family": {
            f.name: sum(1 for c in cells if c.family.name == f.name) for f in FAMILIES
        },
        "sample_per_family": per_family(chosen),
        "seed_structure_hashes": dict(sorted(seeds.items())),
        "documents": entries,
    }
    return Generated(documents, manifest)


def write(generated: Generated, out: Path) -> None:
    out.mkdir(parents=True, exist_ok=True)
    # The directory is generated output: stale documents from an earlier
    # grammar must not survive beside the new manifest.
    for old in sorted(out.glob("*.json")):
        old.unlink()
    for stem in sorted(generated.documents):
        (out / f"{stem}.json").write_text(generated.documents[stem].to_json() + "\n",
                                          encoding="utf-8")
    (out / MANIFEST_NAME).write_text(
        json.dumps(generated.manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--out", type=Path, default=OUT)
    args = parser.parse_args(argv)
    generated = generate()
    write(generated, args.out)
    m = generated.manifest
    print(f"grammar: {m['grammar_size_before_dedupe']} cells, "
          f"{m['grammar_size_after_dedupe']} after dedupe ({len(m['dropped'])} dropped)")
    print(f"grammar per family: {m['grammar_per_family']}")
    print(f"sample per family:  {m['sample_per_family']}")
    print(f"wrote {len(generated.documents)} documents + {MANIFEST_NAME} to {args.out} "
          f"(rng_seed {RNG_SEED}, generator sha256 {m['generator_sha256'][:12]})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
