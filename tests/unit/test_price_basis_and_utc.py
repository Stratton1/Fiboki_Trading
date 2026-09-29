"""The engine refuses BID bars and non-UTC indices (audit P1-3, P3-4).

The fill model assumes MID bars. On BID bars a long enters at bid + half spread,
which is the true mid -- half a spread too cheap -- and a short's stop triggers
on bid highs where the real trigger is the ask. The error is directional (it
flatters long-biased strategies), so the engine refuses rather than tolerates.
"""
from __future__ import annotations

import pandas as pd
import pytest

from fiboki.backtest.engine import (
    BacktestConfig,
    FixedSizeSizer,
    PrecomputedSignals,
    run_backtest,
)
from fiboki.core.enums import Timeframe
from fiboki.data.schema import PriceBasis
from fiboki.validation.run import research_mid_frame
from tests.data_fixtures import make_bars
from tests.helpers_exec import ConstantFx, flat_profile, make_frame

FX = ConstantFx({("USD", "GBP"): 0.8})


def _run(frame: pd.DataFrame, **cfg):
    return run_backtest(
        data={"EURUSD": frame},
        config=BacktestConfig(
            initial_balance=10_000.0, profile=flat_profile(), strategy_id="pb", **cfg
        ),
        strategy=PrecomputedSignals([]),
        sizer=FixedSizeSizer(1000.0),
        fx=FX,
    )


def _labelled(basis: PriceBasis) -> pd.DataFrame:
    return make_bars(periods=50, price_basis=basis)


@pytest.mark.parametrize("basis", [PriceBasis.BID, PriceBasis.ASK, PriceBasis.LAST])
def test_a_non_executable_price_basis_is_refused(basis: PriceBasis) -> None:
    with pytest.raises(ValueError, match="not executable on both sides"):
        _run(_labelled(basis))


@pytest.mark.parametrize("basis", [PriceBasis.MID, PriceBasis.SYNTHETIC_MID])
def test_mid_bases_are_accepted_and_recorded(basis: PriceBasis) -> None:
    result = _run(_labelled(basis))
    assert result.data_fingerprint["EURUSD"]["price_basis"] == basis.value


def test_an_unlabelled_frame_is_recorded_as_an_assumption() -> None:
    frame = make_frame([("2024-01-02 00:00", 1.1, 1.1, 1.1, 1.1),
                        ("2024-01-02 01:00", 1.1, 1.1, 1.1, 1.1)])
    result = _run(frame)
    assert result.data_fingerprint["EURUSD"]["price_basis"] == "assumed_mid"
    assert result.config_fingerprint["assume_mid"] is True


def test_assume_mid_false_refuses_an_unlabelled_frame() -> None:
    frame = make_frame([("2024-01-02 00:00", 1.1, 1.1, 1.1, 1.1),
                        ("2024-01-02 01:00", 1.1, 1.1, 1.1, 1.1)])
    with pytest.raises(ValueError, match="no price_basis column"):
        _run(frame, assume_mid=False)


def test_a_non_utc_index_is_refused() -> None:
    """Financing reads the UTC hour and weekday; London time shifts every rollover."""
    frame = make_frame([("2024-06-03 00:00", 1.1, 1.1, 1.1, 1.1),
                        ("2024-06-03 01:00", 1.1, 1.1, 1.1, 1.1)])
    frame.index = frame.index.tz_convert("Europe/London")
    with pytest.raises(ValueError, match="not UTC"):
        _run(frame)


def test_research_converts_bid_bars_with_the_typical_spread_and_says_so() -> None:
    """EURUSD typical spread 0.9 pips: every OHLC price rises by 0.45 pips."""
    bid = _labelled(PriceBasis.BID)
    mid, lineage = research_mid_frame(bid, "EURUSD")
    assert set(mid["price_basis"]) == {"synthetic_mid"}
    diff = (mid["close"] - bid["close"]).to_numpy()
    assert diff == pytest.approx([0.000045] * len(diff), abs=1e-12)
    assert lineage == {
        "basis": "synthetic_mid",
        "from": "bid",
        "via": "bid_to_mid",
        "assumed_spread_pips": 0.9,
        "pip_size": 0.0001,
    }
    # And the converted frame is one the engine accepts.
    assert _run(mid).data_fingerprint["EURUSD"]["price_basis"] == "synthetic_mid"


def test_research_refuses_ask_bars() -> None:
    with pytest.raises(ValueError, match="only mid, synthetic_mid or bid"):
        research_mid_frame(_labelled(PriceBasis.ASK), "EURUSD")


def test_research_records_an_unlabelled_frame_as_assumed_mid() -> None:
    frame = make_bars(periods=10).drop(columns=["price_basis"])
    out, lineage = research_mid_frame(frame, "EURUSD")
    assert out is frame
    assert lineage == {"basis": "assumed_mid"}


def test_the_evaluator_fingerprints_the_basis_and_its_lineage() -> None:
    """The conversion enters the engine fingerprint, and so every cache key."""
    from fiboki.validation.engine_evaluator import EngineEvaluator, EvaluatorConfig
    from tests.agents_fixtures import ema_crossover_document

    doc = ema_crossover_document()
    bid = make_bars(periods=300, price_basis=PriceBasis.BID)
    mid, lineage = research_mid_frame(bid, "EURUSD")
    common = dict(
        document=doc, dataset_version_id="ds_x",
        config=EvaluatorConfig(instrument="EURUSD", timeframe=Timeframe.H1,
                               account_ccy="USD"),
    )
    converted = EngineEvaluator(frame=mid, price_basis_lineage=lineage, **common)
    plain = EngineEvaluator(frame=_labelled(PriceBasis.MID).iloc[:300], **common)
    fp = converted.engine_fingerprint()
    assert fp["price_basis"] == "synthetic_mid"
    assert fp["price_basis_lineage"]["from"] == "bid"
    assert plain.engine_fingerprint()["price_basis"] == "mid"
    assert converted.engine_config_hash() != plain.engine_config_hash()
