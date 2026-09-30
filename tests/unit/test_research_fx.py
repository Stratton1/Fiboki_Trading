"""Research runs in GBP with a real rate source built from stored bars (audit P1-5, P3-3)."""
from __future__ import annotations

import pandas as pd
import pytest

from fiboki.core.enums import Timeframe
from fiboki.core.money import DEFAULT_MAX_STALENESS, SeriesFxSource
from fiboki.discovery.campaign import (
    CampaignCheckpoint,
    CampaignRunner,
    CampaignSpec,
    store_bar_source,
)
from fiboki.research.experiment import ExperimentLedger
from fiboki.validation.engine_evaluator import EvaluatorConfig
from fiboki.validation.holdout import HoldoutRegistry
from fiboki.validation.run import (
    RESEARCH_ACCOUNT_CCY,
    FxSourceUnavailable,
    build_research_fx_source,
    research_fx_pairs,
)
from tests.discovery_fixtures import RecordingValidator, a_hypothesis


def T(s: str) -> pd.Timestamp:
    return pd.Timestamp(s, tz="UTC")


class _Version:
    def __init__(self, version_id: str) -> None:
        self.version_id = version_id


class FakeStore:
    def __init__(self, frames: dict[str, pd.DataFrame]) -> None:
        self.frames = frames

    def read_latest(self, symbol, timeframe, *, kind=None):
        if symbol not in self.frames:
            raise LookupError(symbol)
        return self.frames[symbol], _Version(f"ds_{symbol}")


def daily(close: float, days: int = 30, start: str = "2024-01-01") -> pd.DataFrame:
    idx = pd.date_range(start, periods=days, freq="1D", tz="UTC", name="timestamp")
    return pd.DataFrame(
        {"open": close, "high": close, "low": close, "close": close}, index=idx
    )


# ------------------------------------------------------------- the defaults


def test_research_is_denominated_in_gbp() -> None:
    assert RESEARCH_ACCOUNT_CCY == "GBP"
    assert EvaluatorConfig(instrument="EURUSD", timeframe=Timeframe.H1).account_ccy == "GBP"
    spec = CampaignSpec(campaign_id="c", universe=("EURGBP",), timeframes=(Timeframe.H1,))
    assert spec.account_ccy == "GBP"


def test_staleness_is_four_days_not_seven() -> None:
    assert pd.Timedelta(days=4) == DEFAULT_MAX_STALENESS
    src = SeriesFxSource({"GBPUSD": pd.Series([1.25], index=[T("2024-01-05")])})
    assert src.rate("USD", "GBP", T("2024-01-09")) == pytest.approx(0.8)
    with pytest.raises(KeyError, match="stale"):
        src.rate("USD", "GBP", T("2024-01-09 00:00:01"))


# --------------------------------------------------------------- routing


@pytest.mark.parametrize(
    ("quote", "pairs"),
    [
        ("GBP", ()),
        ("USD", ("GBPUSD",)),
        ("JPY", ("GBPJPY",)),
        ("EUR", ("EURGBP",)),
        ("CHF", ("GBPCHF",)),
        ("CAD", ("GBPCAD",)),
        ("AUD", ("GBPAUD",)),
        # GBPNZD is registered since the OANDA registry (2026-09-30), so NZD
        # now converts through the direct cross. It triangulated through USD
        # (NZDUSD, GBPUSD) while no GBP/NZD cross was registered.
        ("NZD", ("GBPNZD",)),
        # SEK has no GBP cross on OANDA, so it still triangulates.
        ("SEK", ("USDSEK", "GBPUSD")),
    ],
)
def test_research_fx_pairs(quote: str, pairs: tuple[str, ...]) -> None:
    assert research_fx_pairs(quote) == pairs


def test_a_currency_with_no_route_says_which_pair_to_register() -> None:
    # This was HKD until 2026-09-30; OANDA offers GBP_HKD and USD_HKD, both now
    # registered. ILS is a currency the registry knows with no pair on OANDA.
    with pytest.raises(FxSourceUnavailable) as exc:
        research_fx_pairs("ILS")
    assert exc.value.missing == ("USDILS",)


def test_triangulation_through_usd() -> None:
    """SEK->GBP = (1 / USDSEK) * (1 / GBPUSD) = 1 / (10.00 x 1.25) = 0.08.

    (The example was NZD via NZDUSD until GBPNZD was registered from OANDA.)
    """
    store = FakeStore({"USDSEK": daily(10.0), "GBPUSD": daily(1.25)})
    fx, label = build_research_fx_source(store, quote_currencies=("SEK",))
    assert fx.rate("SEK", "GBP", T("2024-01-10 12:00")) == pytest.approx(0.08, abs=1e-15)
    assert "USDSEK@ds_USDSEK" in label and "GBPUSD@ds_GBPUSD" in label


def test_a_missing_cross_is_refused_listing_every_instrument_to_ingest() -> None:
    store = FakeStore({"GBPUSD": daily(1.25)})
    with pytest.raises(FxSourceUnavailable) as exc:
        build_research_fx_source(store, quote_currencies=("USD", "JPY", "NZD", "EUR"))
    # GBPJPY, GBPNZD and EURGBP are all missing; they are named together.
    # (NZD needed NZDUSD before GBPNZD was registered from OANDA, 2026-09-30.)
    assert exc.value.missing == ("EURGBP", "GBPJPY", "GBPNZD")
    assert "ingest EURGBP, GBPJPY, GBPNZD" in str(exc.value)


def test_a_daily_close_is_known_only_at_the_bar_close() -> None:
    frame = daily(1.25, days=2)
    frame.loc[frame.index[1], "close"] = 1.28
    fx, _ = build_research_fx_source(FakeStore({"GBPUSD": frame}), quote_currencies=("USD",))
    assert fx.rate("USD", "GBP", T("2024-01-02 12:00")) == pytest.approx(1 / 1.25)
    assert fx.rate("USD", "GBP", T("2024-01-03 00:00")) == pytest.approx(1 / 1.28)
    with pytest.raises(KeyError, match="no observation"):
        fx.rate("USD", "GBP", T("2024-01-01 23:59"))


# ------------------------------------------------------------ the campaign


def _spec(universe: tuple[str, ...]) -> CampaignSpec:
    return CampaignSpec(
        campaign_id="gbp_fx",
        universe=universe,
        timeframes=(Timeframe.H4,),
        hypotheses=(a_hypothesis(),),
        actor="tests:gbp-fx",
        max_generations=0,
        include_prior_trials=False,
    )


def _runner(store: FakeStore, universe: tuple[str, ...]) -> CampaignRunner:
    return CampaignRunner(
        _spec(universe),
        bars=store_bar_source(store),
        ledger=ExperimentLedger.in_memory(),
        registry=HoldoutRegistry.in_memory(),
        checkpoint=CampaignCheckpoint.in_memory(),
        validator=RecordingValidator(),
    )


def test_a_store_backed_campaign_builds_its_fx_source_from_the_store() -> None:
    store = FakeStore({"GBPUSD": daily(1.25), "GBPJPY": daily(190.0)})
    runner = _runner(store, ("EURUSD", "GBPJPY", "EURGBP"))
    assert isinstance(runner.fx, SeriesFxSource)
    assert "GBPJPY@ds_GBPJPY" in runner.fx_label
    assert runner.fx.rate("JPY", "GBP", T("2024-01-10")) == pytest.approx(1 / 190.0)


def test_a_store_backed_campaign_refuses_up_front_when_a_cross_is_missing() -> None:
    store = FakeStore({"GBPUSD": daily(1.25)})
    with pytest.raises(FxSourceUnavailable) as exc:
        _runner(store, ("EURUSD", "GBPJPY", "AUDNZD"))
    # GBPNZD, not NZDUSD, since GBPNZD was registered from OANDA (2026-09-30).
    assert exc.value.missing == ("GBPJPY", "GBPNZD")


def test_a_gbp_quoted_universe_needs_no_fx() -> None:
    runner = _runner(FakeStore({}), ("EURGBP", "UK100"))
    assert runner.fx is None
