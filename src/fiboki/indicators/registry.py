"""Indicator registry.

The registry is the single place that knows every indicator that exists. Two
things depend on it:

* :mod:`fiboki.strategy.compiler`, which instantiates indicators named in a DSL
  document and derives the strategy's warmup from them.
* ``tests/unit/test_indicator_causality.py``, which parametrises over
  :func:`causality_suite` so that adding an indicator without a causality proof
  is impossible -- the test collects it automatically.
"""
from __future__ import annotations

from typing import Any

from fiboki.indicators.base import Indicator
from fiboki.indicators.fibonacci import Fibonacci
from fiboki.indicators.ichimoku import Ichimoku
from fiboki.indicators.momentum import CCI, ROC, RSI, Stochastic
from fiboki.indicators.swing import SwingDetector
from fiboki.indicators.trend import ADX, EMA, MACD, PSAR, SMA, WMA
from fiboki.indicators.volatility import (
    ATR,
    Bollinger,
    Donchian,
    Keltner,
    RealisedVolatility,
)
from fiboki.indicators.volume import OBV, VWAP

INDICATORS: dict[str, type[Indicator]] = {
    "sma": SMA,
    "ema": EMA,
    "wma": WMA,
    "rsi": RSI,
    "macd": MACD,
    "bollinger": Bollinger,
    "atr": ATR,
    "adx": ADX,
    "donchian": Donchian,
    "keltner": Keltner,
    "stochastic": Stochastic,
    "cci": CCI,
    "psar": PSAR,
    "roc": ROC,
    "obv": OBV,
    "vwap": VWAP,
    "realised_volatility": RealisedVolatility,
    "ichimoku": Ichimoku,
    "swing": SwingDetector,
    "fibonacci": Fibonacci,
}

#: Indicators that read the ``volume`` column.
VOLUME_INDICATORS: frozenset[str] = frozenset({"obv", "vwap"})


class UnknownIndicatorError(KeyError):
    """Raised for an indicator key that is not registered."""


def available() -> list[str]:
    return sorted(INDICATORS)


def create(key: str, params: dict[str, Any] | None = None) -> Indicator:
    """Instantiate a registered indicator. Unknown keys and bad parameters raise."""
    if key not in INDICATORS:
        raise UnknownIndicatorError(
            f"Unknown indicator {key!r}. Registered: {available()}"
        )
    cls = INDICATORS[key]
    kwargs = dict(params or {})
    # Tuple-valued parameters arrive as lists from JSON/YAML.
    for field in ("retracements", "extensions"):
        if field in kwargs and isinstance(kwargs[field], list):
            kwargs[field] = tuple(kwargs[field])
    try:
        return cls(**kwargs)
    except TypeError as exc:
        raise TypeError(f"{key}: bad parameters {kwargs!r}: {exc}") from exc


def causality_suite() -> list[Indicator]:
    """Every registered indicator, with parameter variants worth proving.

    Small periods are used so the suite runs on a short fixture; a handful of
    second instances exercise alternative parameterisations (shift asymmetry in
    Ichimoku, a non-default swing lookback, the volume "mark" policy).
    """
    suite: list[Indicator] = [
        SMA(period=5),
        SMA(period=20),
        EMA(period=5),
        WMA(period=4),
        RSI(period=3),
        RSI(period=14),
        MACD(fast=3, slow=7, signal=3),
        Bollinger(period=5, num_std=2.0),
        ATR(period=3),
        ADX(period=4),
        Donchian(period=5),
        Keltner(ema_period=5, atr_period=3, multiple=1.5),
        Stochastic(k_period=5, smooth=2, d_period=2),
        CCI(period=5),
        PSAR(),
        ROC(period=3),
        OBV(on_unavailable="mark"),
        VWAP(period=5, on_unavailable="mark"),
        RealisedVolatility(period=5),
        RealisedVolatility(period=5, bars_per_year=6240.0),
        # senkou_shift deliberately != chikou_shift: V1 conflated them.
        Ichimoku(
            tenkan_period=3,
            kijun_period=5,
            senkou_b_period=7,
            senkou_shift=3,
            chikou_shift=4,
        ),
        Ichimoku(),
        SwingDetector(lookback=2),
        SwingDetector(lookback=5),
        Fibonacci(swing_lookback=2),
        Fibonacci(swing_lookback=3, retracements=(0.5,), extensions=(1.618,)),
    ]
    covered = {type(i).__name__ for i in suite}
    expected = {cls.__name__ for cls in INDICATORS.values()}
    missing = expected - covered
    if missing:  # pragma: no cover - guard against a silent coverage hole
        raise AssertionError(
            f"causality_suite is missing registered indicators: {sorted(missing)}"
        )
    return suite
