"""The agent validation job deflates by the LEDGER's trial count and Lo's null variance.

Audit P1-2. The handler used to (a) take ``N`` from ``n_trials_in_search`` in
the payload -- a number the requesting agent writes -- and (b) use the
variance of per-TRADE RETURNS as the variance of the trial SHARPES. At 1% risk
per trade the first is about 0.01^2, which put the expected maximum Sharpe of
1,000 null trials at ~0.033 instead of ~0.165 and reported a DSR near 0.91 for
a strategy whose honest DSR is near 0.05.

Now ``N`` comes from :meth:`ExperimentLedger.count_trials` for the strategy's
FAMILY (structure hash) and, when the backtest names one, its CAMPAIGN; the
payload can only raise it. With no cross-section of trial Sharpes, the null
variance is Lo's (2002) asymptotic ``(1 - g3*SR + (g4-1)/4*SR^2) / (T-1)``.
Unknown ``N`` is NOT_EVALUATED, which blocks.
"""
from __future__ import annotations

import math

import pytest

from fiboki.agents.jobs import register_research_handlers
from fiboki.agents.orchestrator import JobSpec, JobStatus, JobType
from fiboki.research.experiment import ActorKind, ExperimentDraft, ExperimentLedger
from fiboki.research.structure import structure_hash
from fiboki.stats.sharpe import expected_max_sharpe, probabilistic_sharpe_ratio
from tests.agents_fixtures import Harness


def _wire(ledger: ExperimentLedger | None) -> Harness:
    harness = Harness(register_handlers=False)
    register_research_handlers(
        harness.orchestrator,
        research=harness.research,
        strategies=harness.strategies,
        bars=harness.bars,
        experiments=ledger,
    )
    return harness


def _run(harness: Harness, job_type: JobType, payload: dict, key: str) -> dict:
    harness.orchestrator.submit(
        JobSpec(job_type=job_type, queue="research", payload=payload, idempotency_key=key)
    )
    record = harness.orchestrator.drain("research")[0]
    assert record.status is JobStatus.SUCCEEDED, f"{job_type.value}: {record.error}"
    return dict(record.result or {})


def _backtest(harness: Harness, **extra) -> str:
    payload = {
        "strategy_id": "ema_cross_fixture", "instrument": "EURUSD", "timeframe": "H1",
        "account_ccy": "USD", **extra,
    }
    return str(_run(harness, JobType.BACKTEST, payload, key=f"bt-{sorted(extra)}")["backtest_id"])


def _family(ledger: ExperimentLedger, harness: Harness, n_rows: int, *, trials_each: int = 1,
            campaign: str = "") -> list[str]:
    ids = []
    for i in range(n_rows):
        outputs = {"n_trials": trials_each}
        if campaign:
            outputs["campaign_id"] = campaign
        ids.append(ledger.create(ExperimentDraft(
            actor_kind=ActorKind.AGENT, actor_name="tests:dsr",
            reason=f"family member {i}", strategy_document=harness.document,
            outputs=outputs, tags=("campaign", campaign) if campaign else (),
        )).id)
    return ids


def test_unknown_search_size_is_not_evaluated_even_if_the_payload_claims_one() -> None:
    harness = _wire(ExperimentLedger.in_memory())  # an EMPTY ledger
    bt = _backtest(harness)
    result = _run(harness, JobType.VALIDATION,
                  {"backtest_id": bt, "n_trials_in_search": 1000}, key="v-unknown")
    report = harness.research.get_validation_report(result["report_id"])
    assert report.metrics["deflated_sharpe_ratio"] is None
    assert report.metrics["trial_count"]["known"] is False
    by_name = {c["name"]: c for c in report.checks}
    assert by_name["deflated_sharpe"]["status"] == "not_evaluated"


def test_a_family_of_fifty_deflates_by_fifty_whatever_the_payload_says() -> None:
    """The R-10 case: n_trials_in_search=1 on a family of 50 uses N >= 50.

    The DSR is recomputed here by hand from the recorded moments:
      V   = (1 - g3*SR + (g4 - 1)/4 * SR^2) / (T - 1)          (Lo 2002)
      SR0 = sqrt(V) * [(1-gamma) Z^-1(1 - 1/50) + gamma Z^-1(1 - 1/(50e))]
      DSR = PSR(SR0)
    """
    ledger = ExperimentLedger.in_memory()
    harness = _wire(ledger)
    _family(ledger, harness, 50)
    bt = _backtest(harness)
    result = _run(harness, JobType.VALIDATION,
                  {"backtest_id": bt, "n_trials_in_search": 1}, key="v-fifty")
    m = harness.research.get_validation_report(result["report_id"]).metrics
    assert m["trial_count"]["known"] is True
    assert m["trial_count"]["n_trials"] == 50
    sr, t, g3, g4 = m["sharpe_per_trade"], m["n_observations"], m["skew"], m["kurtosis"]
    v = (1 - g3 * sr + (g4 - 1) / 4 * sr * sr) / (t - 1)
    assert m["null_sharpe_variance"] == pytest.approx(v, rel=1e-6)
    sr0 = expected_max_sharpe(50, v)
    assert m["expected_max_sharpe_of_search"] == pytest.approx(sr0, rel=1e-6)
    assert m["deflated_sharpe_ratio"] == pytest.approx(
        probabilistic_sharpe_ratio(sr, t, g3, g4, sr0), abs=1e-6
    )
    # The null dispersion of a Sharpe on T observations is ~ 1/sqrt(T): orders
    # of magnitude above the per-trade-return variance the handler used before.
    assert math.sqrt(v) == pytest.approx(1 / math.sqrt(t - 1), rel=0.5)


def test_the_campaign_count_is_used_when_it_is_larger() -> None:
    ledger = ExperimentLedger.in_memory()
    harness = _wire(ledger)
    _family(ledger, harness, 3)                                   # family: 3 trials
    other = ExperimentDraft(actor_kind=ActorKind.AGENT, actor_name="tests:dsr",
                            reason="another family in the same campaign",
                            outputs={"n_trials": 400, "campaign_id": "camp_7"},
                            tags=("campaign", "camp_7"))
    ledger.create(other)
    (member,) = _family(ledger, harness, 1, trials_each=8, campaign="camp_7")
    bt = _backtest(harness, experiment_id=member)
    result = _run(harness, JobType.VALIDATION, {"backtest_id": bt}, key="v-camp")
    m = harness.research.get_validation_report(result["report_id"]).metrics
    # family: 3 + 8 = 11 trials; campaign camp_7: 400 + 8 = 408. The larger wins.
    assert m["trial_count"]["n_trials"] == 408


def test_a_larger_payload_count_can_only_make_deflation_harder() -> None:
    ledger = ExperimentLedger.in_memory()
    harness = _wire(ledger)
    _family(ledger, harness, 5)
    bt = _backtest(harness)
    result = _run(harness, JobType.VALIDATION,
                  {"backtest_id": bt, "n_trials_in_search": 5000}, key="v-more")
    report = harness.research.get_validation_report(result["report_id"])
    assert report.metrics["trial_count"]["n_trials"] == 5000
    assert any("ledger is missing trials" in c for c in report.caveats)


def test_count_trials_reads_the_ledger() -> None:
    ledger = ExperimentLedger.in_memory()
    harness = Harness(register_handlers=False)
    _family(ledger, harness, 4, trials_each=3)
    skipped = ExperimentDraft(actor_kind=ActorKind.AGENT, actor_name="tests:dsr",
                              reason="a skip", strategy_document=harness.document,
                              tags=("campaign", "c1", "skipped"))
    ledger.create(skipped)
    count = ledger.count_trials(structure_hash=structure_hash(harness.document))
    assert count.n_experiments == 5
    assert count.n_trials == 12          # 4 x 3, and the skip spent nothing
    assert count.by_source == {"outputs.n_trials": 12, "skipped": 0}
    assert ledger.count_trials(campaign_id="nope").known is False
    with pytest.raises(ValueError, match="needs a scope"):
        ledger.count_trials()
