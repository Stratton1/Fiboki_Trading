"""K6 grammar: 100,000 structurally distinct, cost-aware strategy documents.

Usage::

    python research/generate_families_k6.py                 # writes research/generated_k6/
    python research/generate_families_k6.py --check         # regenerate, compare with the manifest
    python research/generate_families_k6.py --size 2000 --out DIR   # a smaller set (tests)

This extends ``research/generate_families.py`` (K5, families-v1, 310 cells sampled
to 100) to a grammar large enough for the K6 campaign
(``research/preregistration/campaign_k6.json``, DRAFT). K5's generator is NOT
edited: its sha256 is recorded in K5's manifest and is part of that campaign's
provenance. The six family stories are imported from it verbatim; one new story
(``trend_signal``) is written here.

The grammar
-----------
A document is a tuple of seven STRUCTURAL choices::

    STATE x ENTRY x CONFIRMATION x REGIME x FILTER x EXIT x DIRECTION

* STATE (setup section, 15 incl. none): which side of the market the bet needs
  (price vs a slow average, the kumo, the MACD or RSI side, DI dominance, ...).
* ENTRY (22): the event that times entry (a re-cross, a channel break, a band
  re-entry, an oscillator cross, an Ichimoku cross, a PSAR flip, ...). The entry
  names the FAMILY and so the economic story.
* CONFIRMATION (9 incl. none): one extra same-bar condition.
* REGIME (6 incl. none): a market-state gate (ADX floor, ADX ceiling, the seeds'
  realised-volatility band, volatility compression, ATR expansion).
* FILTER (3 incl. none): the London/New York window (H4 only) or Tuesday to
  Thursday.
* EXIT (6): time exit, R target, chandelier trail, R target plus trail, ATR
  target, swing-structure stop (one-sided documents only).
* DIRECTION (3): both, long only, short only.

Every axis is a different RULE, indicator, section or exit KIND: numbers are
elided by :func:`fiboki.research.structure.structure_hash`, so a document that
differs from another only in a period or a multiple is the SAME structure and
is dropped. Each document declares at most three parameters, each a two-value
``choice`` (state lookback, entry lookback, exit size), so its grid is at most
eight points: the K5 grid shape (``MAX_GRID_POINTS = 8``, two values per axis).

Cost-aware by construction
--------------------------
K5's documents could place a 0.5-ATR minimum stop and a 1.5R target; on FX at
H4 the spread is then a large fraction of the risk and of the reward. Here
every stop is at least 2 ATR(14) (``min_distance_atr`` 1.0 under a structure
stop), every R target is at least 2R, every ATR target at least 3 ATR, every
time exit at least 20 bars, and no H1 document is generated: the timeframes are
H4 and D1 only (H4 only for the London/New York window, which D1 bars cannot
express). Whether costs still dominate on a given universe is measured, not
assumed: the campaign's screen records each document's cost share.

Distinctness and deduplication
------------------------------
Three hashes are computed for every candidate cell:

* ``structure_hash`` (numbers elided) of the document;
* ``rules_hash``: the structure hash with the universe and timeframes replaced by
  a fixed placeholder, so a K6 document whose RULES equal a seed's or one of
  K5's hundred is caught even though its universe differs (a universe-scoped
  hash would let a K5 rule set back in under a new universe);
* ``body_hash``: SHA-256 of the canonical JSON of the document without its
  identity and prose (id, name, hypothesis, notes, author) or its universe: the
  canonical CONTENT hash the campaign dedupes on.

A cell is kept only if all three are new and its ``rules_hash`` is not a seed's
or a K5 document's. The cells are visited in an order fixed by a seeded
permutation of the whole (exclusion-filtered) grammar, so the first ``size``
survivors are a pure function of the grammar and ``RNG_SEED``.

What is written
---------------
``cells.csv.gz``: one line per kept document, ``index,cell_code`` (the document
is rebuilt from its code by :func:`build_document`). ``MANIFEST.json``: grammar
size before and after exclusions, the seed, the generator's sha256, per-family
and per-axis counts, the drop reasons, the sha256 of the cells file, and a hash
CHAIN over every kept document's (index, structure, rules, body) hashes, so a
changed grammar or a changed DSL serialisation shows up as a different manifest.
The documents themselves are not written (100,000 JSON files are about 500 MB);
:func:`materialise` rebuilds any one on demand for a named universe.

What it cannot do
-----------------
It cannot invent an economic mechanism. Seven stories, written by hand, cover
every document; the generator recombines states, events, filters and exits
under them. A K6 survivor is a HYPOTHESIS to be re-authored as a seed with its
own written story, never a result. Every one of the 100,000 documents counts in
the campaign's trial count, whether or not it survives the screen.
"""
from __future__ import annotations

import argparse
import csv
import gzip
import hashlib
import io
import json
import sys
from collections import Counter
from collections.abc import Callable, Iterator, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "research"))

import generate_families as k5  # noqa: E402  (K5's generator, imported, never edited)

from fiboki.core.enums import Timeframe  # noqa: E402
from fiboki.research.structure import structure_hash  # noqa: E402
from fiboki.strategy.dsl import (  # noqa: E402
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
from fiboki.strategy.primitives import (  # noqa: E402
    ConstantOperand,
    CrossoverRule,
    IndicatorOperand,
    IndicatorVsIndicatorRule,
    IndicatorVsPriceRule,
    RegimeGateRule,
    SessionWindowRule,
    ThresholdRule,
)

OUT = REPO / "research" / "generated_k6"
CELLS_NAME = "cells.csv.gz"
MANIFEST_NAME = "MANIFEST.json"

GENERATOR_VERSION = "families-v2-k6"
CAMPAIGN = "K6"
#: Seed of the permutation. Recorded in the manifest and the pre-registration;
#: changing it is a new campaign, not a re-roll of this one.
RNG_SEED = 20261011
SIZE = 100_000
MAX_GRID_POINTS = 8
AUTHOR = "fiboki-v2-generator"
#: Placeholder universe used ONLY for the universe-free ``rules_hash`` and the
#: default materialisation; a campaign run materialises for its own universe.
PLACEHOLDER_UNIVERSE = ("EURUSD",)

P = k5.P
spec = k5.spec
op = k5.op
CLOSE = k5.CLOSE
ATR14 = k5.ATR14
ATR_OP = k5.ATR_OP


def choice2(default: Any, values: tuple[Any, Any], description: str) -> ParameterSpec:
    return ParameterSpec(kind="choice", default=default, choices=values, description=description)


# --------------------------------------------------------------------- stories

HYPOTHESES: dict[str, str] = dict(k5.HYPOTHESES)
HYPOTHESES["trend_signal"] = (
    "ECONOMIC STORY. A trend-change signal (a fast average crossing a slow one, a "
    "parabolic SAR flip, or the directional-movement lines crossing) is the textbook "
    "expression of time-series momentum: Moskowitz, Ooi and Pedersen (2012) and Hurst, "
    "Ooi and Pedersen (2017) document the premium across asset classes and a century, "
    "attributing it to slow information diffusion and to hedging and risk-management "
    "flows. The bet is that the signal marks the start of a persistent move often enough, "
    "and the move is large enough relative to the stop, to pay for the whipsaws.\n\n"
    "EVIDENCE AGAINST, STATED PLAINLY. Moving-average and SAR rules are the family with "
    "the worst record once data snooping is priced in: Brock, Lakonishok and LeBaron "
    "(1992) shrank under White's Reality Check (Sullivan, Timmermann and White 1999); "
    "Coakley, Marzano and Nankervis (2016) find FX technical rules do not survive "
    "Step-SPA; Olson (2004) and Neely, Weller and Ulrich (2009) find FX trading-rule "
    "profits declined as markets adapted. Crossover signals arrive late by construction "
    "and whipsaw in ranges, and trend-following returns have decayed since 2009. The "
    "honest prior is an edge near zero before costs."
)

FAMILY_ENUM: dict[str, StrategyFamily] = dict(k5.FAMILY_ENUM)
FAMILY_ENUM["trend_signal"] = StrategyFamily.TREND_FOLLOWING

# --------------------------------------------------------------------- parts

Rules = tuple[Any, ...]
Params = tuple[tuple[str, ParameterSpec], ...]


@dataclass(frozen=True)
class Part:
    """One grammar choice: its long and short rules and the parameters it declares."""

    long: Rules
    short: Rules
    params: Params = ()


def _price_side(ind: IndicatorOperand) -> Part:
    """Long when close is above the indicator, short when below."""
    return Part((IndicatorVsPriceRule(indicator=ind, comparator="<", price=CLOSE),),
                (IndicatorVsPriceRule(indicator=ind, comparator=">", price=CLOSE),))


def _level_side(o: Any, level: float, short_level: float | None = None) -> Part:
    s = level if short_level is None else short_level
    return Part((ThresholdRule(operand=o, comparator=">", value=level),),
                (ThresholdRule(operand=o, comparator="<", value=s),))


def _ind_side(left: IndicatorOperand, right: IndicatorOperand) -> Part:
    return Part((IndicatorVsIndicatorRule(left=left, right=right, comparator=">"),),
                (IndicatorVsIndicatorRule(left=left, right=right, comparator="<"),))


def _cross(fast_l: Any, slow_l: Any, fast_s: Any | None = None, slow_s: Any | None = None,
           params: Params = ()) -> Part:
    fs = fast_l if fast_s is None else fast_s
    ss = slow_l if slow_s is None else slow_s
    return Part((CrossoverRule(fast=fast_l, slow=slow_l, direction="above"),),
                (CrossoverRule(fast=fs, slow=ss, direction="below"),), params)


STATE_PERIOD = ("state_period", choice2(200, (100, 200), "Lookback of the state condition"))
ENTRY_PERIOD = ("entry_period", choice2(20, (10, 20), "Lookback of the entry event"))
CHANNEL = ("entry_period", choice2(20, (20, 40), "Prior range whose break is the entry"))

ICHI = spec("ichimoku", tenkan_period=9, kijun_period=26, senkou_b_period=52,
            senkou_shift=26, chikou_shift=26)
MACD = spec("macd", fast=12, slow=26, signal=9)
RSI14 = spec("rsi", period=14)
ADX14 = spec("adx", period=14)
STOCH = spec("stochastic", k_period=14, smooth=3, d_period=3)
PSAR = spec("psar", af_start=0.02, af_step=0.02, af_max=0.2)
ZERO = ConstantOperand(value=0.0)


def _with(part: Part, *params: tuple[str, ParameterSpec]) -> Part:
    return Part(part.long, part.short, tuple(params))


def _states() -> dict[str, Part]:
    roc_state = ("state_period", choice2(50, (20, 50), "Rate-of-change lookback of the state"))
    return {
        "none": Part((), ()),
        "ema_side": _with(_price_side(op(spec("ema", period=P("state_period")))), STATE_PERIOD),
        "sma_side": _with(_price_side(op(spec("sma", period=P("state_period")))), STATE_PERIOD),
        "wma_side": _with(_price_side(op(spec("wma", period=P("state_period")))), STATE_PERIOD),
        "ma_stack": _ind_side(op(spec("ema", period=50)), op(spec("ema", period=200))),
        "kumo_side": Part(
            (IndicatorVsPriceRule(indicator=op(ICHI, "cloud_top"), comparator="<", price=CLOSE),),
            (IndicatorVsPriceRule(indicator=op(ICHI, "cloud_bottom"), comparator=">",
                                  price=CLOSE),)),
        "macd_hist_side": _level_side(op(MACD, "hist"), 0.0),
        "macd_line_side": _level_side(op(MACD, "line"), 0.0),
        "rsi_side": _level_side(op(RSI14), 50.0),
        "roc_side": _with(_level_side(op(spec("roc", period=P("state_period"))), 0.0), roc_state),
        "donchian_mid_side": _price_side(op(spec("donchian", period=55), "mid")),
        "psar_side": _level_side(op(PSAR, "psar_trend"), 0.0),
        "di_side": _ind_side(op(ADX14, "plus_di"), op(ADX14, "minus_di")),
        "stoch_side": _level_side(op(STOCH, "k"), 50.0),
        "cci_side": _level_side(op(spec("cci", period=50)), 0.0),
    }


def _entries() -> dict[str, tuple[str, Part]]:
    """entry name -> (family, part). The family carries the economic story."""
    kc_p = spec("keltner", ema_period=20, atr_period=10, multiple=P("entry_mult"))
    kc_mult = ("entry_mult", choice2(1.5, (1.5, 2.0), "Keltner band multiple"))
    bb_p = spec("bollinger", period=20, num_std=P("entry_mult"))
    bb_mult = ("entry_mult", choice2(2.0, (2.0, 2.5), "Bollinger band width"))
    don = spec("donchian", period=P("entry_period"))
    rsi_e = spec("rsi", period=P("entry_period"))
    rsi_p = ("entry_period", choice2(14, (9, 14), "RSI lookback of the entry"))
    cci_e = spec("cci", period=P("entry_period"))
    cci_p = ("entry_period", choice2(20, (14, 20), "CCI lookback of the entry"))
    stoch_e = spec("stochastic", k_period=P("entry_period"), smooth=3, d_period=3)
    stoch_p = ("entry_period", choice2(14, (9, 14), "Stochastic %K lookback"))
    roc_e = spec("roc", period=P("entry_period"))
    roc_p = ("entry_period", choice2(10, (10, 20), "Rate-of-change lookback of the entry"))
    ema_fast = spec("ema", period=P("entry_period"))
    sma_fast = spec("sma", period=P("entry_period"))
    ichi_e = spec("ichimoku", tenkan_period=9, kijun_period=P("entry_period"),
                  senkou_b_period=52, senkou_shift=26, chikou_shift=26)
    kijun_p = ("entry_period", choice2(26, (26, 30), "Kijun (base line) lookback"))
    half = ConstantOperand(value=0.5)
    return {
        # trend pullback: re-cross of a fast line or band inside the state
        "ema_recross": ("trend_pullback", _cross(CLOSE, op(ema_fast), params=(ENTRY_PERIOD,))),
        "sma_recross": ("trend_pullback", _cross(CLOSE, op(sma_fast), params=(ENTRY_PERIOD,))),
        "kc_pullback": ("trend_pullback", _cross(CLOSE, op(kc_p, "lower"), CLOSE,
                                                 op(kc_p, "upper"), params=(kc_mult,))),
        # breakout
        "donchian_close_break": ("breakout_vol", Part(
            (IndicatorVsPriceRule(indicator=op(don, "upper_prior"), comparator="<", price=CLOSE),),
            (IndicatorVsPriceRule(indicator=op(don, "lower_prior"), comparator=">", price=CLOSE),),
            (CHANNEL,))),
        "donchian_fresh_cross": ("breakout_vol", _cross(CLOSE, op(don, "upper_prior"), CLOSE,
                                                        op(don, "lower_prior"), (CHANNEL,))),
        "kc_break": ("breakout_vol", _cross(CLOSE, op(kc_p, "upper"), CLOSE, op(kc_p, "lower"),
                                            (kc_mult,))),
        "bb_break": ("breakout_vol", _cross(CLOSE, op(bb_p, "upper"), CLOSE, op(bb_p, "lower"),
                                            (bb_mult,))),
        # mean reversion: re-entry into a band, oscillator leaving an extreme
        "bb_reentry": ("mean_reversion_band", _cross(CLOSE, op(bb_p, "lower"), CLOSE,
                                                     op(bb_p, "upper"), (bb_mult,))),
        # Leaving an extreme, written as "was beyond it on the prior bar, is back
        # inside now": two thresholds, so the structure differs from a crossover
        # through a constant (which ``rsi_50_cross`` and ``cci_thrust`` are, and
        # which the structure hash cannot tell apart from one at 30 or -100).
        "rsi_exit_extreme": ("mean_reversion_band", Part(
            (ThresholdRule(operand=op(rsi_e, "", 1), comparator="<", value=30.0),
             ThresholdRule(operand=op(rsi_e), comparator=">=", value=30.0)),
            (ThresholdRule(operand=op(rsi_e, "", 1), comparator=">", value=70.0),
             ThresholdRule(operand=op(rsi_e), comparator="<=", value=70.0)),
            (rsi_p,))),
        "cci_reversal": ("mean_reversion_band", Part(
            (ThresholdRule(operand=op(cci_e, "", 1), comparator="<", value=-100.0),
             ThresholdRule(operand=op(cci_e), comparator=">=", value=-100.0)),
            (ThresholdRule(operand=op(cci_e, "", 1), comparator=">", value=100.0),
             ThresholdRule(operand=op(cci_e), comparator="<=", value=100.0)),
            (cci_p,))),
        "stoch_extreme_cross": ("mean_reversion_band", Part(
            (CrossoverRule(fast=op(stoch_e, "k"), slow=op(stoch_e, "d"), direction="above"),
             ThresholdRule(operand=op(stoch_e, "k", 1), comparator="<", value=20.0)),
            (CrossoverRule(fast=op(stoch_e, "k"), slow=op(stoch_e, "d"), direction="below"),
             ThresholdRule(operand=op(stoch_e, "k", 1), comparator=">", value=80.0)),
            (stoch_p,))),
        # momentum
        "macd_signal_cross": ("momentum_oscillator", _cross(op(MACD, "line"), op(MACD, "signal"))),
        "macd_zero_cross": ("momentum_oscillator", _cross(op(MACD, "hist"), ZERO)),
        "rsi_50_cross": ("momentum_oscillator", _cross(op(rsi_e), ConstantOperand(value=50.0),
                                                       params=(rsi_p,))),
        "cci_thrust": ("momentum_oscillator", _cross(
            op(cci_e), ConstantOperand(value=100.0), op(cci_e), ConstantOperand(value=-100.0),
            (cci_p,))),
        "roc_zero_cross": ("momentum_oscillator", _cross(op(roc_e), ZERO, params=(roc_p,))),
        # ichimoku
        "tk_cross": ("ichimoku_variants", _cross(op(ichi_e, "tenkan"), op(ichi_e, "kijun"),
                                                 params=(kijun_p,))),
        "chikou_flip": ("ichimoku_variants", _cross(op(ichi_e, "chikou_above_price"), half,
                                                    params=(kijun_p,))),
        "kijun_cross": ("ichimoku_variants", _cross(CLOSE, op(ichi_e, "kijun"),
                                                    params=(kijun_p,))),
        # trend signals
        "ma_cross": ("trend_signal", _cross(op(ema_fast), op(spec("ema", period=100)),
                                            params=(("entry_period",
                                                     choice2(20, (20, 50), "Fast EMA")),))),
        "psar_flip": ("trend_signal", _cross(op(PSAR, "psar_trend"), ZERO)),
        "di_cross": ("trend_signal", _cross(op(ADX14, "plus_di"), op(ADX14, "minus_di"))),
    }


ATR_FAST = spec("atr", period=6)
ATR_DAY = spec("atr", period=24)
BB_WIDTH = spec("bollinger", period=20, num_std=2.0)
EMA20 = spec("ema", period=20)


def _confirmations() -> dict[str, Part]:
    sym = lambda r: Part((r,), (r,))  # noqa: E731 - side-neutral condition
    return {
        "none": Part((), ()),
        "adx_rising": sym(IndicatorVsIndicatorRule(left=op(ADX14), right=op(ADX14, "", 1),
                                                   comparator=">")),
        "rsi_not_extreme": Part((ThresholdRule(operand=op(RSI14), comparator="<", value=70.0),),
                                (ThresholdRule(operand=op(RSI14), comparator=">", value=30.0),)),
        "macd_hist_rising": Part(
            (IndicatorVsIndicatorRule(left=op(MACD, "hist"), right=op(MACD, "hist", 1),
                                      comparator=">"),),
            (IndicatorVsIndicatorRule(left=op(MACD, "hist"), right=op(MACD, "hist", 1),
                                      comparator="<"),)),
        "close_vs_ema20": _price_side(op(EMA20)),
        "atr_expanding": sym(IndicatorVsIndicatorRule(left=op(ATR_FAST), right=op(ATR_DAY),
                                                      comparator=">")),
        "bb_width_rising": sym(IndicatorVsIndicatorRule(
            left=op(BB_WIDTH, "width"), right=op(BB_WIDTH, "width", 1), comparator=">")),
        "roc_side": _level_side(op(spec("roc", period=10)), 0.0),
        "plus_di_side": _ind_side(op(ADX14, "plus_di"), op(ADX14, "minus_di")),
    }


RVOL20 = spec("realised_volatility", period=20)
RVOL100 = spec("realised_volatility", period=100)


def _regimes() -> dict[str, Rules]:
    return {
        "none": (),
        "adx_floor": (RegimeGateRule(metric=op(ADX14), min_value=20.0),),
        "adx_ceiling": (RegimeGateRule(metric=op(ADX14), max_value=25.0),),
        "rvol_band": (RegimeGateRule(metric=op(RVOL20), min_value=0.0015, max_value=0.05),),
        "compression": (IndicatorVsIndicatorRule(left=op(RVOL20), right=op(RVOL100),
                                                 comparator="<"),),
        "atr_expansion": (IndicatorVsIndicatorRule(left=op(ATR_FAST), right=op(ATR_DAY),
                                                   comparator=">"),),
    }


FILTERS: dict[str, Rules] = {
    "none": (),
    "london_ny": (SessionWindowRule(start_hour_utc=7, end_hour_utc=20, weekdays=(0, 1, 2, 3, 4)),),
    "midweek": (SessionWindowRule(start_hour_utc=0, end_hour_utc=23, weekdays=(1, 2, 3)),),
}

SWING5 = spec("swing", lookback=5)
EXIT_NAMES = ("time_exit", "tp_r", "trail", "tp_trail", "atr_target", "swing_tp")
EXIT_TEXT = {
    "time_exit": "3xATR(14) disaster stop plus a fixed holding period of 20 or 40 bars",
    "tp_r": "2xATR(14) stop plus one take-profit leg at 2R or 3R",
    "trail": "2xATR(14) stop plus an ATR chandelier trail of 3 or 4 ATR",
    "tp_trail": "2xATR(14) stop, half closed at 2R or 3R, the rest on a 3-ATR chandelier",
    "atr_target": "2xATR(14) stop plus one target 3 or 4 ATR from entry",
    "swing_tp": "stop beyond the last confirmed swing (min 1 ATR) plus a 2R or 3R target",
}
EXIT_PARAM = {
    "time_exit": ("exit_size", choice2(20, (20, 40), "Holding period in bars")),
    "tp_r": ("exit_size", choice2(2.0, (2.0, 3.0), "Take-profit distance in R")),
    "trail": ("exit_size", choice2(3.0, (3.0, 4.0), "Chandelier distance in ATR")),
    "tp_trail": ("exit_size", choice2(2.0, (2.0, 3.0), "First target in R")),
    "atr_target": ("exit_size", choice2(3.0, (3.0, 4.0), "Target distance in ATR")),
    "swing_tp": ("exit_size", choice2(2.0, (2.0, 3.0), "Take-profit distance in R")),
}


def _exit(name: str, direction: TradeDirection) -> dict[str, Any]:
    atr_stop = StopModel(kind="atr_multiple", value=2.0, atr=ATR_OP, min_distance_atr=1.0)
    size = P("exit_size")
    if name == "time_exit":
        return {"stop": StopModel(kind="atr_multiple", value=3.0, atr=ATR_OP, min_distance_atr=1.0),
                "pm": PositionManagement(max_concurrent_positions=1, max_bars_in_trade=size)}
    if name == "tp_r":
        return {"stop": atr_stop, "tps": (TakeProfitLeg(kind="r_multiple", value=size,
                                                        allocation=1.0, label="target"),)}
    if name == "trail":
        return {"stop": atr_stop, "trailing": TrailingModel(
            kind="atr_chandelier", value=size, activate_after_r=0.0, atr=ATR_OP)}
    if name == "tp_trail":
        return {"stop": atr_stop,
                "tps": (TakeProfitLeg(kind="r_multiple", value=size, allocation=0.5,
                                      label="first"),),
                "trailing": TrailingModel(kind="atr_chandelier", value=3.0,
                                          activate_after_r=0.0, atr=ATR_OP)}
    if name == "atr_target":
        return {"stop": atr_stop, "tps": (TakeProfitLeg(kind="atr_multiple", value=size,
                                                        allocation=1.0, atr=ATR_OP,
                                                        label="target"),)}
    if name == "swing_tp":
        level = "last_swing_low" if direction is TradeDirection.LONG else "last_swing_high"
        return {"stop": StopModel(kind="swing_structure", value=1.0, level=op(SWING5, level),
                                  atr=ATR_OP, buffer_atr=0.25, min_distance_atr=1.0),
                "tps": (TakeProfitLeg(kind="r_multiple", value=size, allocation=1.0,
                                      label="target"),)}
    raise KeyError(name)


DIRECTIONS = ("both", "long", "short")
DIRECTION_ENUM = {"both": TradeDirection.BOTH, "long": TradeDirection.LONG,
                  "short": TradeDirection.SHORT}

STATES = _states()
ENTRIES = _entries()
CONFIRMATIONS = _confirmations()
REGIMES = _regimes()

#: The axes in mixed-radix order. A cell code is ``s.e.c.r.f.x.d`` of indices.
AXES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("state", tuple(STATES)),
    ("entry", tuple(ENTRIES)),
    ("confirmation", tuple(CONFIRMATIONS)),
    ("regime", tuple(REGIMES)),
    ("filter", tuple(FILTERS)),
    ("exit", EXIT_NAMES),
    ("direction", DIRECTIONS),
)
RADICES = tuple(len(v) for _, v in AXES)

#: (state, entry) pairs where the entry event implies the state, so the state
#: adds no condition the entry does not already impose: the same bet stated
#: twice. Excluded from the grammar, not merely deduplicated, because their
#: structure hashes differ.
REDUNDANT_STATE_ENTRY = frozenset({
    ("rsi_side", "rsi_50_cross"), ("macd_hist_side", "macd_zero_cross"),
    ("psar_side", "psar_flip"), ("di_side", "di_cross"), ("roc_side", "roc_zero_cross"),
})
#: Confirmations that restate a state condition exactly.
REDUNDANT_STATE_CONFIRMATION = frozenset({("di_side", "plus_di_side")})


@dataclass(frozen=True)
class Cell:
    state: str
    entry: str
    confirmation: str
    regime: str
    filter: str
    exit: str
    direction: str

    @property
    def family(self) -> str:
        return ENTRIES[self.entry][0]

    @property
    def code(self) -> str:
        names = (self.state, self.entry, self.confirmation, self.regime, self.filter,
                 self.exit, self.direction)
        return ".".join(str(AXES[i][1].index(n)) for i, n in enumerate(names))

    @classmethod
    def from_code(cls, code: str) -> Cell:
        parts = [int(x) for x in code.split(".")]
        if len(parts) != len(AXES):
            raise ValueError(f"bad cell code {code!r}")
        return cls(*(AXES[i][1][k] for i, k in enumerate(parts)))

    def describe(self) -> dict[str, str]:
        return {"family": self.family, "state": self.state, "entry": self.entry,
                "confirmation": self.confirmation, "regime": self.regime,
                "filter": self.filter, "exit": self.exit, "direction": self.direction}


def exclusion(cell: Cell) -> str | None:
    """Why a cell is outside the grammar, or ``None``. Fixed before any data."""
    if (cell.state, cell.entry) in REDUNDANT_STATE_ENTRY:
        return "entry implies state"
    if (cell.state, cell.confirmation) in REDUNDANT_STATE_CONFIRMATION:
        return "confirmation restates state"
    if cell.exit == "swing_tp" and cell.direction == "both":
        # StopModel carries ONE level operand for both sides (K5 module notes).
        return "swing stop needs a one-sided document"
    return None


def cell_at(flat: int) -> Cell:
    names = []
    for (_, values), radix in zip(reversed(AXES), reversed(RADICES), strict=True):
        flat, k = divmod(flat, radix)
        names.append(values[k])
    return Cell(*reversed(names))


def grammar_size() -> int:
    total = 1
    for r in RADICES:
        total *= r
    return total


def _sides(direction: TradeDirection, long: Rules, short: Rules) -> RuleSet:
    keep_l = direction in (TradeDirection.LONG, TradeDirection.BOTH)
    keep_s = direction in (TradeDirection.SHORT, TradeDirection.BOTH)
    return RuleSet(long=long if keep_l else (), short=short if keep_s else ())


def build_document(index: int, cell: Cell,
                   universe: Sequence[str] = PLACEHOLDER_UNIVERSE) -> StrategyDocument:
    """The document for one cell. Pure: the same (index, cell, universe) -> same bytes."""
    family = cell.family
    state = STATES[cell.state]
    entry = ENTRIES[cell.entry][1]
    confirmation = CONFIRMATIONS[cell.confirmation]
    direction = DIRECTION_ENUM[cell.direction]
    exit_ = _exit(cell.exit, direction)
    params = dict(state.params) | dict(entry.params) | dict(confirmation.params)
    params |= dict((EXIT_PARAM[cell.exit],))
    timeframes = (Timeframe.H4,) if cell.filter == "london_ny" else (Timeframe.H4, Timeframe.D1)
    sentence = (
        f"\n\nGRAMMAR CELL {cell.code}. State '{cell.state}'; entry '{cell.entry}'; "
        f"confirmation '{cell.confirmation}'; regime '{cell.regime}'; filter '{cell.filter}'; "
        f"exit '{cell.exit}' ({EXIT_TEXT[cell.exit]}); direction '{cell.direction}'. Generated by "
        f"{GENERATOR_VERSION} (seed {RNG_SEED}) as document {index} of the {SIZE:,} pre-registered "
        f"together as campaign {CAMPAIGN}; every one counts in the trial count; a survivor is a "
        "hypothesis to re-author as a seed, not a result."
    )
    return StrategyDocument(
        strategy_id=f"k6_{index:06d}",
        name=f"K6 {index:06d} {family} / {cell.entry} / {cell.exit} / {cell.direction}",
        family=FAMILY_ENUM[family],
        hypothesis=HYPOTHESES[family] + sentence,
        universe=tuple(universe),
        timeframes=timeframes,
        direction=direction,
        regime=REGIMES[cell.regime],
        setup=_sides(direction, state.long, state.short),
        entry=_sides(direction, entry.long, entry.short),
        confirmation=_sides(direction, confirmation.long, confirmation.short),
        filters=FILTERS[cell.filter],
        stop=exit_["stop"],
        take_profits=exit_.get("tps", ()),
        trailing=exit_.get("trailing"),
        position_management=exit_.get("pm", PositionManagement(max_concurrent_positions=1)),
        sessions=None,
        events=k5.EVENTS,
        parameters={k: params[k] for k in sorted(params)},
        author=AUTHOR,
        notes=(f"K6 grammar cell {cell.code} ({json.dumps(cell.describe(), sort_keys=True)}); "
               f"generator {GENERATOR_VERSION}, rng_seed {RNG_SEED}. Generated, not hand-written."),
    )


_IDENTITY_FIELDS = ("strategy_id", "name", "hypothesis", "notes", "author", "universe")


def body_hash(document: StrategyDocument) -> str:
    """SHA-256 of the canonical JSON without identity, prose and universe."""
    raw = document.model_dump(mode="json")
    for key in _IDENTITY_FIELDS:
        raw.pop(key, None)
    blob = json.dumps(raw, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def _elide_refs(node: Any) -> Any:
    """Every parameter reference becomes a number (which the structure hash elides)."""
    if isinstance(node, dict):
        if set(node) == {"$param"}:
            return 0
        return {k: _elide_refs(v) for k, v in node.items()}
    if isinstance(node, list):
        return [_elide_refs(v) for v in node]
    return node


def rules_hash(document: StrategyDocument) -> str:
    """What the RULES are, whatever the universe, timeframes, family or parameter names.

    The structure hash keeps a parameter reference's NAME (``{"$param":
    "slow_period"}``) and the declared parameter names (``context:tunable``), so
    K5's ``close above EMA($slow_period)`` and K6's ``close above
    EMA($state_period)`` would hash differently although they are one rule. Here
    every reference is elided like a number and the declared names are dropped,
    as are the universe, the timeframes and the family label: two documents
    with equal rules hashes are the same bet, whoever generated them.
    """
    raw = _elide_refs(document.model_dump(mode="json"))
    raw["universe"] = list(PLACEHOLDER_UNIVERSE)
    raw["timeframes"] = ["H4"]
    raw["family"] = "any"
    raw["parameters"] = {}
    return structure_hash(raw)


def declared_grid_points(document: StrategyDocument) -> int:
    total = 1
    for p in document.parameters.values():
        total *= len(p.domain())
    return total


def check_document(document: StrategyDocument) -> None:
    """A cell that does not compile or is unhealthy is a bug in the grammar."""
    k5._check(document)
    if declared_grid_points(document) > MAX_GRID_POINTS:
        raise RuntimeError(f"{document.strategy_id}: grid above {MAX_GRID_POINTS}")


def prior_rules_hashes() -> dict[str, str]:
    """rules_hash -> id for the six seeds and K5's hundred generated documents."""
    from fiboki.strategy.registry import load_seed_registry

    out: dict[str, str] = {}
    for doc in load_seed_registry(k5.SEED_DIR):
        out.setdefault(rules_hash(doc), doc.strategy_id)
    for path in sorted((REPO / "research" / "generated").glob("*.json")):
        if path.name == k5.MANIFEST_NAME:
            continue
        doc = StrategyDocument.from_json(path.read_text(encoding="utf-8"))
        out.setdefault(rules_hash(doc), doc.strategy_id)
    return out


def permutation(n: int, seed: int = RNG_SEED) -> np.ndarray:
    """The fixed visiting order of the grammar (NumPy PCG64, pinned numpy)."""
    return np.random.Generator(np.random.PCG64(seed)).permutation(n)


@dataclass
class Generated:
    cells: list[tuple[int, Cell]]
    manifest: dict[str, Any]


def generate(size: int = SIZE, *, seed: int = RNG_SEED, check: bool = True,
             progress: Callable[[int, int], None] | None = None) -> Generated:
    total = grammar_size()
    excluded: Counter[str] = Counter()
    valid = 0
    for flat in range(total):
        why = exclusion(cell_at(flat))
        if why is None:
            valid += 1
        else:
            excluded[why] += 1
    prior = prior_rules_hashes()
    seen_structure: dict[str, int] = {}
    seen_rules: dict[str, int] = {}
    seen_body: dict[str, int] = {}
    dropped: Counter[str] = Counter()
    kept: list[tuple[int, Cell]] = []
    chain = hashlib.sha256()
    visited = 0
    for flat in permutation(total, seed):
        cell = cell_at(int(flat))
        if exclusion(cell) is not None:
            continue
        visited += 1
        index = len(kept)
        document = build_document(index, cell)
        if check:
            check_document(document)
        s_hash = structure_hash(document)
        r_hash = rules_hash(document)
        b_hash = body_hash(document)
        if r_hash in prior:
            dropped["rules of a seed or K5 document"] += 1
            continue
        if s_hash in seen_structure:
            dropped["structure hash of an earlier cell"] += 1
            continue
        if r_hash in seen_rules:
            dropped["rules hash of an earlier cell"] += 1
            continue
        if b_hash in seen_body:
            dropped["body hash of an earlier cell"] += 1
            continue
        seen_structure[s_hash] = index
        seen_rules[r_hash] = index
        seen_body[b_hash] = index
        kept.append((index, cell))
        chain.update(f"{index},{cell.code},{s_hash},{r_hash},{b_hash}\n".encode())
        if progress is not None and len(kept) % 5000 == 0:
            progress(len(kept), visited)
        if len(kept) == size:
            break
    if len(kept) < size:
        raise RuntimeError(f"grammar yields {len(kept)} distinct documents, fewer than {size}")
    by_axis: dict[str, dict[str, int]] = {}
    for axis, _ in AXES:
        counts: Counter[str] = Counter()
        for _, c in kept:
            counts[getattr(c, axis)] += 1
        by_axis[axis] = dict(sorted(counts.items()))
    families = Counter(c.family for _, c in kept)
    timeframe_cells = sum(1 if c.filter == "london_ny" else 2 for _, c in kept)
    grid_points = Counter()
    for index, cell in kept[: min(len(kept), 2000)]:
        grid_points[declared_grid_points(build_document(index, cell))] += 1
    cells_text = _cells_text(kept)
    manifest = {
        "generator": "research/generate_families_k6.py",
        "generator_version": GENERATOR_VERSION,
        "generator_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "k5_generator_sha256": hashlib.sha256(Path(k5.__file__).read_bytes()).hexdigest(),
        "campaign": CAMPAIGN,
        "rng_seed": seed,
        "size": size,
        "axes": {name: list(values) for name, values in AXES},
        "grammar_size": total,
        "grammar_excluded": dict(sorted(excluded.items())),
        "grammar_valid": valid,
        "cells_visited": visited,
        "dropped": dict(sorted(dropped.items())),
        "prior_documents_checked": len(prior),
        "per_family": dict(sorted(families.items())),
        "per_axis": by_axis,
        "document_timeframe_cells": timeframe_cells,
        "declared_grid_points_first_2000": {str(k): v for k, v in sorted(grid_points.items())},
        "cells_file": CELLS_NAME,
        "cells_sha256": hashlib.sha256(cells_text.encode("utf-8")).hexdigest(),
        "hash_chain_sha256": chain.hexdigest(),
        "checked": bool(check),
    }
    return Generated(kept, manifest)


def _cells_text(kept: Sequence[tuple[int, Cell]]) -> str:
    buf = io.StringIO()
    writer = csv.writer(buf, lineterminator="\n")
    writer.writerow(["index", "cell_code"])
    for index, cell in kept:
        writer.writerow([index, cell.code])
    return buf.getvalue()


def write(generated: Generated, out: Path) -> None:
    out.mkdir(parents=True, exist_ok=True)
    text = _cells_text(generated.cells)
    # mtime=0 so the .gz bytes are a pure function of the content.
    with (out / CELLS_NAME).open("wb") as fh, gzip.GzipFile(
        fileobj=fh, mode="wb", mtime=0, filename=""
    ) as gz:
        gz.write(text.encode("utf-8"))
    (out / MANIFEST_NAME).write_text(
        json.dumps(generated.manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )


def read_cells(directory: Path = OUT) -> list[tuple[int, Cell]]:
    """The kept cells, verified against the manifest's sha256 of the cells file."""
    manifest = json.loads((directory / MANIFEST_NAME).read_text(encoding="utf-8"))
    text = gzip.decompress((directory / CELLS_NAME).read_bytes()).decode("utf-8")
    digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
    if digest != manifest["cells_sha256"]:
        raise ValueError(f"{directory / CELLS_NAME} does not match its manifest "
                         f"({digest} != {manifest['cells_sha256']})")
    rows = list(csv.reader(io.StringIO(text)))
    return [(int(i), Cell.from_code(code)) for i, code in rows[1:]]


def materialise(index: int, cell: Cell, universe: Sequence[str]) -> StrategyDocument:
    """Document ``index`` for a campaign universe (its universe field is the only change)."""
    return build_document(index, cell, universe)


def iter_documents(universe: Sequence[str], directory: Path = OUT,
                   start: int = 0, end: int | None = None) -> Iterator[StrategyDocument]:
    for index, cell in read_cells(directory)[start:end]:
        yield materialise(index, cell, universe)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--out", type=Path, default=OUT)
    parser.add_argument("--size", type=int, default=SIZE)
    parser.add_argument("--check", action="store_true",
                        help="regenerate and compare with the manifest at --out; write nothing")
    parser.add_argument("--no-health-check", action="store_true",
                        help="skip the per-document compile/health check (faster; not for filing)")
    args = parser.parse_args(argv)

    def progress(kept: int, visited: int) -> None:
        print(f"  {kept:,} kept of {visited:,} visited", file=sys.stderr, flush=True)

    generated = generate(args.size, check=not args.no_health_check, progress=progress)
    m = generated.manifest
    if args.check:
        filed = json.loads((args.out / MANIFEST_NAME).read_text(encoding="utf-8"))
        keys = ("rng_seed", "size", "grammar_size", "grammar_valid", "cells_sha256",
                "hash_chain_sha256", "per_family", "dropped")
        diff = [k for k in keys if filed.get(k) != m.get(k)]
        if diff:
            print(f"K6 GENERATION DIFFERS FROM MANIFEST in {diff}", file=sys.stderr)
            return 3
        print("K6 generation reproduces the manifest byte for byte "
              f"(cells sha256 {m['cells_sha256'][:12]}, chain {m['hash_chain_sha256'][:12]})")
        return 0
    write(generated, args.out)
    print(f"grammar {m['grammar_size']:,} cells, {m['grammar_valid']:,} after exclusions; "
          f"visited {m['cells_visited']:,}; kept {len(generated.cells):,}; dropped {m['dropped']}")
    print(f"per family: {m['per_family']}")
    print(f"wrote {args.out / CELLS_NAME} and {MANIFEST_NAME} (seed {m['rng_seed']})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
