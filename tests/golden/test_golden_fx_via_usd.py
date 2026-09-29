"""JPY -> GBP through USD when the direct cross has not started: the arithmetic, by hand.

HistData's GBP crosses start later than the USD-quoted series they convert
(GBPJPY 2002-05-01 against USDJPY 2000-05-30), so research needs a fallback for
the early bars. ``SeriesFxSource(fallback_via_pivot=True)`` answers from the
direct cross whenever it has a fresh rate and from the product of the two USD
legs only where it has none, each leg staleness-checked (4 days).

Stored daily rates, indexed at the time each became KNOWN:

    GBPJPY  190.00  known from 2024-01-10 00:00   (the late cross)
    USDJPY  150.00  known from 2024-01-01 00:00
    GBPUSD    1.25  known from 2024-01-01 00:00

At 2024-01-03 12:00 GBPJPY has no observation, so JPY -> GBP goes via USD:

    JPY -> USD = 1 / USDJPY = 1 / 150.00       = 0.00666667 USD per JPY
    USD -> GBP = 1 / GBPUSD = 1 / 1.25         = 0.80       GBP per USD
    JPY -> GBP = 0.00666667 * 0.80 = 1 / 187.5 = 0.00533333 GBP per JPY

A profit of 30,000 JPY is therefore 30,000 / 187.5 = 160.00 GBP.

At 2024-01-10 12:00 GBPJPY has a fresh rate and wins, although both USD legs
are also fresh (the implied cross 187.5 differs from the quoted 190.0 on
purpose, so the test can tell which route answered):

    JPY -> GBP = 1 / 190.00 = 0.00526316; 30,000 JPY = 157.894737 GBP
"""
from __future__ import annotations

import pandas as pd
import pytest

from fiboki.core.instruments import get as get_instrument
from fiboki.core.money import SeriesFxSource, to_account_ccy

pytestmark = pytest.mark.golden


def T(s: str) -> pd.Timestamp:
    return pd.Timestamp(s, tz="UTC")


def _daily(value: float, start: str, days: int) -> pd.Series:
    return pd.Series(value, index=pd.date_range(start, periods=days, freq="1D", tz="UTC"))


def _source(**overrides) -> SeriesFxSource:
    payload = {
        "series": {
            "GBPJPY": _daily(190.0, "2024-01-10", 30),
            "USDJPY": _daily(150.0, "2024-01-01", 40),
            "GBPUSD": _daily(1.25, "2024-01-01", 40),
        },
        "fallback_via_pivot": True,
    }
    payload.update(overrides)
    return SeriesFxSource(**payload)


def test_before_the_cross_starts_the_rate_is_the_product_of_the_usd_legs() -> None:
    fx = _source()
    rate, route = fx.rate_with_route("JPY", "GBP", T("2024-01-03 12:00"))
    assert route == "via_usd"
    assert rate == pytest.approx(1 / 187.5, rel=1e-12)
    assert rate == pytest.approx(0.0053333333, abs=1e-10)
    usdjpy = get_instrument("USDJPY")
    assert to_account_ccy(30_000.0, usdjpy, "GBP", T("2024-01-03 12:00"), fx) == pytest.approx(
        160.00, abs=1e-9
    )
    assert fx.route_counts[("JPY", "GBP", "via_usd")] == 2


def test_the_direct_cross_wins_whenever_it_has_a_fresh_rate() -> None:
    fx = _source()
    rate, route = fx.rate_with_route("JPY", "GBP", T("2024-01-10 12:00"))
    assert route == "inverse"  # GBPJPY is the inverse of JPY -> GBP
    assert rate == pytest.approx(1 / 190.0, rel=1e-12)
    assert 30_000.0 * rate == pytest.approx(157.894737, abs=1e-6)
    assert ("JPY", "GBP", "via_usd") not in fx.route_counts


def test_a_gap_in_the_cross_longer_than_the_staleness_guard_falls_back_then_returns() -> None:
    """GBPJPY known 2024-01-10 .. 2024-01-12, then nothing until 2024-01-20."""
    gbpjpy = pd.concat([_daily(190.0, "2024-01-10", 3), _daily(191.0, "2024-01-20", 5)])
    fx = _source(series={**_source().series, "GBPJPY": gbpjpy})
    # 2024-01-17 12:00 is 5.5 days after the last GBPJPY rate: stale, so via USD.
    assert fx.rate_with_route("JPY", "GBP", T("2024-01-17 12:00")) == (
        pytest.approx(1 / 187.5, rel=1e-12),
        "via_usd",
    )
    # 2024-01-15 12:00 is 3.5 days after it: still fresh, so the cross.
    assert fx.rate_with_route("JPY", "GBP", T("2024-01-15 12:00"))[1] == "inverse"
    assert fx.rate_with_route("JPY", "GBP", T("2024-01-20 12:00")) == (
        pytest.approx(1 / 191.0, rel=1e-12),
        "inverse",
    )


def test_the_staleness_guard_applies_to_each_usd_leg() -> None:
    """GBPUSD's only rate is 2024-01-01: at 2024-01-06 it is five days old."""
    series = {**_source().series, "GBPUSD": _daily(1.25, "2024-01-01", 1)}
    fx = _source(series=series)
    with pytest.raises(KeyError) as exc:
        fx.rate("JPY", "GBP", T("2024-01-06 00:00"))
    message = str(exc.value)
    assert "GBPJPY has no observation" in message  # the direct error comes first
    assert "USD fallback could not answer" in message
    assert "GBPUSD is stale" in message


def test_without_the_flag_a_loaded_cross_that_cannot_answer_still_refuses() -> None:
    """The default is unchanged, so paper and every other caller behave as before."""
    fx = _source(fallback_via_pivot=False)
    with pytest.raises(KeyError, match="GBPJPY has no observation"):
        fx.rate("JPY", "GBP", T("2024-01-03 12:00"))
