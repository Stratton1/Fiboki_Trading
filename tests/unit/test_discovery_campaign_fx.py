"""A campaign over a real universe is mostly not quoted in its account currency.

Nine of the sixteen HistData H4 series are quoted in JPY, GBP, CHF, CAD or EUR.
``run_validation`` refuses to run those in a USD account without a rate source,
rather than applying a 1.0 that would mis-state every monetary figure by the
exchange rate. Before campaign K2 that refusal was unreachable from a campaign:
``run_cell`` never forwarded an FX source, so nine instruments errored cell by
cell and a "multi-instrument" campaign was quietly a seven-instrument one.

These tests pin the wiring and the declaration rule that goes with it.
"""
from __future__ import annotations

import pandas as pd
import pytest

from fiboki.core.enums import Timeframe
from fiboki.core.money import SeriesFxSource
from fiboki.discovery.campaign import (
    CampaignCheckpoint,
    CampaignRunner,
    CampaignSpec,
    run_cell,
)
from fiboki.research.experiment import ExperimentLedger
from fiboki.validation.gates import GATE_SET_V2
from fiboki.validation.holdout import HoldoutRegistry
from fiboki.validation.ladder import LadderConfig
from tests.discovery_fixtures import (
    RecordingValidator,
    a_hypothesis,
    bar_source,
    one_barset,
    seed,
)

GBPJPY_H4 = one_barset(instrument="GBPJPY", version="ds_test_gbpjpy")
SOURCE = bar_source({("GBPJPY", "H4"): GBPJPY_H4})

FX_LABEL = "SeriesFxSource(USDJPY->JPY; bid closes, HistData H4, as-of backward)"


def an_fx_source() -> SeriesFxSource:
    index = pd.date_range("2014-01-01", periods=1200, freq="4h", tz="UTC")
    return SeriesFxSource(series={"USDJPY": pd.Series(110.0, index=index)})


def a_spec(**overrides) -> CampaignSpec:
    payload = {
        "campaign_id": "k_fx",
        "universe": ("GBPJPY",),
        "timeframes": (Timeframe.H4,),
        "hypotheses": (a_hypothesis(),),
        "actor": "tests:campaign-fx",
        "account_ccy": "USD",
        "gate_set": GATE_SET_V2,
        "max_evaluations": 1000,
        "max_grid_points": 8,
        "max_values_per_axis": 2,
        "sweep_parameters": {
            "donchian_breakout_atr": (
                "channel_period",
                "stop_atr_multiple",
                "trail_atr_multiple",
            )
        },
        "max_generations": 0,
        "include_prior_trials": False,
    }
    payload.update(overrides)
    return CampaignSpec(**payload)


@pytest.fixture
def ledger():
    with ExperimentLedger.in_memory() as led:
        yield led


class TestAConversionMustBeDeclared:
    def test_an_fx_source_without_a_label_is_refused(self, ledger) -> None:
        with pytest.raises(ValueError, match="fx_label is blank"):
            CampaignRunner(
                a_spec(),
                bars=SOURCE,
                ledger=ledger,
                registry=HoldoutRegistry.in_memory(),
                checkpoint=CampaignCheckpoint.in_memory(),
                validator=RecordingValidator(),
                fx=an_fx_source(),
            )

    def test_no_source_needs_no_label(self, ledger) -> None:
        runner = CampaignRunner(
            a_spec(),
            bars=SOURCE,
            ledger=ledger,
            registry=HoldoutRegistry.in_memory(),
            checkpoint=CampaignCheckpoint.in_memory(),
            validator=RecordingValidator(),
        )
        assert runner.fx is None

    def test_the_label_reaches_the_serialised_spec(self) -> None:
        """The source object cannot go in a JSON report; the label is the record."""
        spec = a_spec(fx_label=FX_LABEL)
        assert spec.to_dict()["fx_label"] == FX_LABEL


class TestTheSourceReachesTheLadder:
    def test_run_cell_refuses_a_foreign_quote_without_a_source(self) -> None:
        """The guardrail that fired 133 times in K2's first attempt."""
        outcome = run_cell(
            cell=_a_cell(),
            bars=GBPJPY_H4,
            spec=a_spec(),
            ladder_config=LadderConfig(min_trades=400),
            registry=HoldoutRegistry.in_memory(),
        )
        assert outcome.report is None
        assert "quoted in JPY" in outcome.error
        assert "account currency is USD" in outcome.error

    def test_run_cell_gets_past_the_refusal_once_a_source_is_supplied(self) -> None:
        """Not a promotion: only that the currency check no longer blocks the run."""
        outcome = run_cell(
            cell=_a_cell(),
            bars=GBPJPY_H4,
            spec=a_spec(fx_label=FX_LABEL),
            ladder_config=LadderConfig(min_trades=400),
            registry=HoldoutRegistry.in_memory(),
            fx=an_fx_source(),
            fx_label=FX_LABEL,
        )
        assert "quoted in JPY" not in outcome.error

    def test_the_runner_hands_its_source_to_the_default_validator(self, ledger) -> None:
        captured: dict[str, object] = {}

        def spy(**kwargs):
            captured.update(kwargs)
            from fiboki.discovery.campaign import CellOutcome

            return CellOutcome(error="spy")

        runner = CampaignRunner(
            a_spec(fx_label=FX_LABEL),
            bars=SOURCE,
            ledger=ledger,
            registry=HoldoutRegistry.in_memory(),
            checkpoint=CampaignCheckpoint.in_memory(),
            fx=an_fx_source(),
        )
        # Replace only the call target, keeping the runner's own binding of fx.
        import fiboki.discovery.campaign as campaign_module

        original = campaign_module.run_cell
        campaign_module.run_cell = spy
        try:
            plan = runner.plan([seed("donchian_breakout_atr")])
            runner.run(plan)
        finally:
            campaign_module.run_cell = original

        assert isinstance(captured.get("fx"), SeriesFxSource)
        assert captured.get("fx_label") == FX_LABEL


def _a_cell():
    from fiboki.discovery.campaign import CandidateCell

    return CandidateCell(
        document=seed("donchian_breakout_atr"),
        instrument="GBPJPY",
        timeframe=Timeframe.H4,
        origin="seed",
    )
