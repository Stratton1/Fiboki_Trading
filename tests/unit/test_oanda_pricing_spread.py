"""A live spread source from OANDA practice pricing (audit F, P2-4).

``abnormal_spread`` compared the execution profile's spread with a multiple of
itself in paper and could never fire. These tests pin the replacement: the
spread the gateway sees is the venue's QUOTED spread at bar close, anything the
source cannot vouch for is ``nan`` (which blocks), and the live host is refused.
"""
from __future__ import annotations

import math

import pandas as pd
import pytest

from fiboki.broker.oanda import (
    OANDA_LIVE_HOST,
    OANDA_PRACTICE_HOST,
    HttpResponse,
    OandaHostError,
    RecordedTransport,
)
from fiboki.broker.oanda_pricing import OandaPricingSpreadSource
from fiboki.broker.retry import ReadRetry
from fiboki.core.contracts import AccountState, Signal, TradePlan
from fiboki.core.enums import Direction, ExecutionMode
from fiboki.core.instruments import get as get_instrument
from fiboki.portfolio.construction import PortfolioSnapshot
from fiboki.risk.gateway import MarketView, RiskContext, RiskGateway
from fiboki.risk.limits import PAPER_LIMITS

ACCOUNT = "101-004-1-001"
KEY = f"GET /v3/accounts/{ACCOUNT}/pricing"
NOW = pd.Timestamp("2026-10-21T10:00:05Z")


def _price(inst: str, bid: str, ask: str, *, at: pd.Timestamp = NOW, tradeable: bool = True):
    return {
        "instrument": inst,
        "time": at.strftime("%Y-%m-%dT%H:%M:%S.000000000Z"),
        "tradeable": tradeable,
        "bids": [{"price": bid, "liquidity": 1000000}],
        "asks": [{"price": ask, "liquidity": 1000000}],
    }


def _source(responses, *, now=NOW) -> tuple[OandaPricingSpreadSource, RecordedTransport]:
    transport = RecordedTransport({KEY: responses})
    clock = {"now": now}
    source = OandaPricingSpreadSource(
        transport=transport,
        account_id=ACCOUNT,
        api_token="t",
        read_retry=ReadRetry(sleeper=lambda _s: None, jitter=lambda: 0.0),
        clock=lambda: clock["now"],
    )
    source._test_clock = clock  # type: ignore[attr-defined]
    return source, transport


def test_the_spread_is_ask_minus_bid_in_price_units() -> None:
    body = {"prices": [_price("EUR_USD", "1.10000", "1.10012"), _price("XAU_USD", "2400.10", "2400.45")]}
    source, transport = _source([HttpResponse(200, body)])
    sample = source.sample(["EURUSD", "XAUUSD"])
    assert sample.ok
    assert source(get_instrument("EURUSD"), NOW) == pytest.approx(0.00012)
    assert source(get_instrument("XAUUSD"), NOW) == pytest.approx(0.35)
    call = transport.calls[0]
    assert call["query"] == "instruments=EUR_USD,XAU_USD"
    assert call["headers"]["Authorization"] == "Bearer t"
    assert source.spread_multiple(get_instrument("EURUSD"), NOW) == pytest.approx(0.00012 / 0.00009)


@pytest.mark.parametrize(
    "price",
    [
        _price("EUR_USD", "1.10000", "1.10012", at=NOW - pd.Timedelta(seconds=121)),  # stale
        _price("EUR_USD", "1.10000", "1.10012", tradeable=False),  # halted
        _price("EUR_USD", "1.10012", "1.10000"),  # crossed
    ],
)
def test_what_the_source_cannot_vouch_for_is_nan(price) -> None:
    source, _ = _source([HttpResponse(200, {"prices": [price]})])
    source.sample(["EURUSD"])
    assert math.isnan(source(get_instrument("EURUSD"), NOW))


def test_never_sampled_is_nan_not_zero() -> None:
    source, _ = _source([HttpResponse(200, {"prices": []})])
    assert math.isnan(source(get_instrument("EURUSD"), NOW))
    sample = source.sample(["EURUSD"])
    assert sample.missing == ("EURUSD",)


def test_a_failed_sample_keeps_old_quotes_which_then_age_out() -> None:
    good = HttpResponse(200, {"prices": [_price("EUR_USD", "1.10000", "1.10012")]})
    down = HttpResponse(503, {"errorMessage": "down"})
    source, transport = _source([good, down])
    source.sample(["EURUSD"])
    report = source.sample(["EURUSD"])
    assert report.error and "503" in report.error
    assert len(transport.calls) == 1 + 4, "a read IS retried, up to max_attempts"
    assert source(get_instrument("EURUSD"), NOW) == pytest.approx(0.00012)
    later = NOW + pd.Timedelta(seconds=180)
    assert math.isnan(source(get_instrument("EURUSD"), later)), "the stale quote ages out"


def test_a_refusal_is_not_retried() -> None:
    source, transport = _source([HttpResponse(401, {"errorMessage": "bad token"})])
    report = source.sample(["EURUSD"])
    assert "401" in report.error and len(transport.calls) == 1


@pytest.mark.parametrize(
    "url",
    [f"https://{OANDA_LIVE_HOST}", f"https://{OANDA_PRACTICE_HOST}.evil.example", "https://x.io"],
)
def test_only_the_practice_host_is_accepted(url: str) -> None:
    with pytest.raises(OandaHostError):
        OandaPricingSpreadSource(
            transport=RecordedTransport({}), account_id=ACCOUNT, api_token="t", base_url=url
        )


def test_the_token_is_not_in_the_repr() -> None:
    source, _ = _source([HttpResponse(200, {"prices": []})])
    assert "Bearer" not in repr(source) and "'t'" not in repr(source)


# -------------------------------------------- the gateway now blocks for real


def _context(spread: float | None) -> RiskContext:
    signal = Signal(
        strategy_id="s", instrument="EURUSD", timeframe="H1", direction=Direction.LONG,
        bar_time=NOW.floor("h") - pd.Timedelta(hours=1), reference_price=1.1,
        stop_price=1.095, take_profit_prices=(1.11,),
    )
    plan = TradePlan(
        signal=signal, size=1000.0, account_ccy="USD", risk_amount=5.0,
        sizing_basis="test", max_leverage_applied=30.0,
    )
    account = AccountState(balance=100_000.0, equity=100_000.0, currency="USD", peak_equity=100_000.0)
    return RiskContext(
        plan=plan,
        snapshot=PortfolioSnapshot(account=account, as_of=NOW),
        now=NOW,
        mode=ExecutionMode.PAPER,
        limits=PAPER_LIMITS,
        market=MarketView(mid_price=1.1, spread_price=spread, market_open=True),
        fx_quote_to_account=1.0,
    )


@pytest.mark.parametrize(
    ("bid", "ask", "blocked"),
    [("1.10000", "1.10012", False), ("1.10000", "1.10060", True)],  # 1.3x vs 6.7x typical
)
def test_abnormal_spread_fires_on_the_quoted_spread(bid: str, ask: str, blocked: bool) -> None:
    source, _ = _source([HttpResponse(200, {"prices": [_price("EUR_USD", bid, ask)]})])
    source.sample(["EURUSD"])
    spread = source(get_instrument("EURUSD"), NOW)
    decision = RiskGateway(limits=PAPER_LIMITS).evaluate(_context(spread))
    assert any(r.startswith("abnormal_spread:") for r in decision.reasons) is blocked
