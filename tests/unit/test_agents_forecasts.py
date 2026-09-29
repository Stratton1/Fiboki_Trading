"""Pre-registered forecasts and their deterministic scorer.

What is fenced here:

* the forecast vocabulary cannot read as an order, and every claim that could
  not be scored honestly is refused at write time (unpinned clock, backdated
  start, horizon beyond policy, probability below 0.5);
* the scorer's arithmetic, on synthetic bars whose ATR and move are known by
  construction (golden test, arithmetic in the docstring);
* scoring is idempotent, and missing bars are NOT_EVALUABLE, never skipped;
* the new capabilities pass the cardinal-rule guard and sit only in the roles
  that should hold them.
"""
from __future__ import annotations

import math
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from typing import Any

import numpy as np
import pandas as pd
import pytest
from pydantic import ValidationError

from fiboki.agents.capabilities import (
    Capability,
    _reads_as_execution_authority,
    assert_no_execution_capability,
)
from fiboki.agents.roles import AgentRole, get_role
from fiboki.agents.session import ToolNotInRole, open_session
from fiboki.agents.tools import (
    REGISTRY,
    InMemoryBarSource,
    ToolContext,
    ToolExecutionError,
    ToolRegistry,
    WriteDomain,
    order_vocabulary_hits,
)
from fiboki.core.enums import Timeframe
from fiboki.research.artefacts import Forecast, ForecastScore, ResearchStore
from fiboki.research.forecasts import (
    FORECAST_POLICY,
    SCORER_VERSION,
    classify_provenance,
    evaluate_forecast,
    magnitude_bucket,
    n_forecasts_by_actor,
    score_due_forecasts,
    scoreboard,
)
from fiboki.strategy.registry import StrategyRegistry
from tests.agents_fixtures import Harness

#: The agent's pinned clock. Midnight, so the D1 bar stamped 2025-02-09 has
#: just closed and is the start bar.
PIN = datetime(2025, 2, 10, tzinfo=UTC)
HORIZON_END = "2025-02-20T00:00:00Z"
#: After the horizon, so everything above is due.
NOW = datetime(2025, 3, 1, tzinfo=UTC)
EVIDENCE = ("audit_act_0001", "regime_scan_eurusd_d1")
REASON = "Regime classifier reports a persistent up-trend with rising volatility."
INVALID_IF = "The regime classifier flips to mean-reverting before the horizon."


def _bars(
    *,
    periods: int = 90,
    start: str = "2025-01-01",
    level: float = 1.0,
    step_from: str | None = None,
    step_to: float | None = None,
    half_range: float = 0.005,
) -> pd.DataFrame:
    """Flat D1 bars: open = close = level, high/low = level +- half_range.

    With a constant close every true range is exactly ``2 * half_range``, so
    Wilder ATR is that constant from its seed onwards. From ``step_from`` the
    level jumps to ``step_to``.
    """
    idx = pd.date_range(start=start, periods=periods, freq="1440min", tz="UTC", name="timestamp")
    close = np.full(periods, level)
    if step_from is not None and step_to is not None:
        close[idx >= pd.Timestamp(step_from, tz="UTC")] = step_to
    return pd.DataFrame(
        {
            "open": close,
            "high": close + half_range,
            "low": close - half_range,
            "close": close,
            "volume": np.full(periods, 1000.0),
        },
        index=idx,
    )


@pytest.fixture
def source() -> InMemoryBarSource:
    return InMemoryBarSource(
        {
            # Flat at 1.0000 through 2025-02-15, then 1.0150: a +1.5 ATR move.
            ("EURUSD", Timeframe.D1.value): _bars(step_from="2025-02-16", step_to=1.015),
            # Flat throughout: a 0 ATR move.
            ("GBPUSD", Timeframe.D1.value): _bars(level=1.25),
            # Ends on 2025-02-12: the horizon end has no bar.
            ("AUDUSD", Timeframe.D1.value): _bars(periods=43, level=0.65),
            # Starts 2025-02-01: too little history for ATR(14) at the pin.
            ("NZDUSD", Timeframe.D1.value): _bars(periods=40, start="2025-02-01", level=0.6),
        }
    )


def _ctx(
    store: ResearchStore,
    bars: InMemoryBarSource | None,
    *,
    as_of: datetime | None = PIN,
    agent_id: str = "qr_1",
    role: str = "quant_researcher",
    model_id: str = "model-a",
) -> ToolContext:
    return ToolContext(
        research=store,
        strategies=StrategyRegistry(),
        bars=bars,
        as_of=as_of,
        agent_id=agent_id,
        role=role,
        model_id=model_id,
    )


def _inputs(**overrides: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "instrument": "EURUSD",
        "timeframe": "D1",
        "horizon_end": HORIZON_END,
        "direction": "higher",
        "probability": 0.8,
        "invalid_if": INVALID_IF,
        "evidence_ids": list(EVIDENCE),
        "reason": REASON,
    }
    base.update(overrides)
    return base


def _record(ctx: ToolContext, **overrides: Any) -> Any:
    spec = REGISTRY.get("record_forecast")
    out = spec.handler(ctx, spec.input_model(**_inputs(**overrides)))
    return spec.output_model.model_validate(out.model_dump())


def _query(ctx: ToolContext, **inputs: Any) -> Any:
    spec = REGISTRY.get("query_forecast_scores")
    out = spec.handler(ctx, spec.input_model(**inputs))
    return spec.output_model.model_validate(out.model_dump())


# ======================================================== the cardinal rule


#: Pinned, so adding a capability is a deliberate act with a written reason.
#: 19 until 2026-09-28; +2 for the forecast record (agentic plan, Wave 2):
#: WRITE_FORECAST (the agent's scored claim) and READ_FORECAST_SCORES (the
#: scorecard, kept apart from READ_EXPERIMENTS so forecasters cannot read it).
EXPECTED_CAPABILITIES = {
    "READ_MARKET_DATA", "READ_DATA_QUALITY", "READ_REGIME", "READ_EXPERIMENTS",
    "READ_RESEARCH_MEMORY", "READ_STRATEGY", "READ_VALIDATION_REPORT",
    "READ_TRADE_LEDGER", "READ_PORTFOLIO", "READ_EXECUTION_TELEMETRY",
    "READ_AUDIT_LEDGER", "READ_EXTERNAL_WEB", "READ_FORECAST_SCORES",
    "WRITE_HYPOTHESIS", "WRITE_STRATEGY_PROPOSAL", "WRITE_STRATEGY_MUTATION",
    "WRITE_EXPERIMENT_DESIGN", "WRITE_CRITIQUE", "WRITE_RESEARCH_NOTE",
    "WRITE_FORECAST",
    "SUBMIT_JOB",
    # +2 (21 -> 23), agentic plan Wave 4 event channel: READ_NEWS_SNAPSHOT for
    # query_news, WRITE_EVENT_ANNOTATION for the quarantined annotation store.
    "READ_NEWS_SNAPSHOT", "WRITE_EVENT_ANNOTATION",
    # +2 (23 -> 25), agentic plan Wave 4 thesis debate: WRITE_DEBATE_TURN for
    # record_debate_turn and WRITE_CONVICTION for record_conviction. Both are
    # research writes to the append-only thesis store and pass the execution
    # guard (verb WRITE, nouns DEBATE_TURN / CONVICTION are not execution nouns).
    "WRITE_DEBATE_TURN", "WRITE_CONVICTION",
}


def test_capability_set_is_pinned_at_25() -> None:
    assert {c.name for c in Capability} == EXPECTED_CAPABILITIES
    assert len(Capability) == 25


def test_the_forecast_capabilities_pass_the_execution_guard() -> None:
    assert_no_execution_capability()
    assert_no_execution_capability([Capability.WRITE_FORECAST, Capability.READ_FORECAST_SCORES])
    for cap in (Capability.WRITE_FORECAST, Capability.READ_FORECAST_SCORES):
        assert _reads_as_execution_authority(cap.name) is None
        assert _reads_as_execution_authority(cap.value) is None


def test_the_forecast_tools_pass_the_registry_guard() -> None:
    fresh = ToolRegistry()
    for name in ("record_forecast", "query_forecast_scores"):
        fresh.register(REGISTRY.get(name))  # re-runs every registration check
    rec = REGISTRY.get("record_forecast")
    assert rec.capability is Capability.WRITE_FORECAST
    assert rec.write_domain is WriteDomain.RESEARCH_FORECAST and rec.mutates
    query = REGISTRY.get("query_forecast_scores")
    assert query.capability is Capability.READ_FORECAST_SCORES
    assert query.write_domain is WriteDomain.NONE and not query.mutates


def test_forecast_capabilities_sit_only_in_the_intended_roles() -> None:
    writers = {
        r.value for r in AgentRole if Capability.WRITE_FORECAST in get_role(r).capabilities
    }
    readers = {
        r.value for r in AgentRole if Capability.READ_FORECAST_SCORES in get_role(r).capabilities
    }
    assert writers == {"quant_researcher", "market_regime_analyst"}
    assert readers == {"research_director", "statistical_auditor"}


def test_no_agent_tool_writes_a_forecast_score() -> None:
    """Scores are written by the deterministic scorer only."""
    for tool in REGISTRY.all():
        assert "score" not in tool.write_domain.value, tool.name
        if tool.mutates:
            assert "score" not in tool.name, tool.name


# ======================================================== schema refusals


@pytest.mark.parametrize(
    "text",
    [
        "Buy EURUSD on the open because the trend is persistent.",
        "The model says SELL into strength over the horizon period.",
        "A good moment to go long given the regime classification.",
        "Short the pair while volatility is rising into the print.",
        "Enter after the next close above the cloud boundary.",
        "Exit if the regime classifier flips to mean-reverting.",
        "Position size should be halved given the volatility state.",
        "Place a stop-loss below the recent swing low for safety.",
        "Use a take profit at twice the recent average range here.",
    ],
)
def test_order_vocabulary_is_refused(source: InMemoryBarSource, text: str) -> None:
    ctx = _ctx(ResearchStore(), source)
    with pytest.raises(ToolExecutionError, match="order vocabulary"):
        _record(ctx, reason=text)
    with pytest.raises(ToolExecutionError, match="order vocabulary"):
        _record(ctx, invalid_if=text)
    assert ctx.research.forecasts() == ()


def test_research_phrases_that_contain_an_order_word_are_not_refused() -> None:
    assert order_vocabulary_hits(
        "Short-term momentum and long-term carry disagree; the sample size is small."
    ) == ()
    assert order_vocabulary_hits("longer horizons and a shorter window") == ()
    assert order_vocabulary_hits("a sell-off") == ("sell",)


@pytest.mark.parametrize("probability", [0.0, 0.3, 0.49, 0.96, 1.0])
def test_probability_outside_half_to_095_is_refused_by_the_schema(probability: float) -> None:
    spec = REGISTRY.get("record_forecast")
    with pytest.raises(ValidationError):
        spec.input_model(**_inputs(probability=probability))


def test_unknown_fields_are_refused_by_the_schema() -> None:
    spec = REGISTRY.get("record_forecast")
    with pytest.raises(ValidationError):
        spec.input_model(**_inputs(position_size=1.0))


def test_empty_evidence_is_refused_by_the_schema() -> None:
    spec = REGISTRY.get("record_forecast")
    with pytest.raises(ValidationError):
        spec.input_model(**_inputs(evidence_ids=[]))


def test_an_unpinned_context_cannot_record_a_forecast(source: InMemoryBarSource) -> None:
    ctx = _ctx(ResearchStore(), source, as_of=None)
    with pytest.raises(ToolExecutionError, match="pinned clock"):
        _record(ctx)
    assert ctx.research.forecasts() == ()


def test_a_horizon_beyond_the_policy_maximum_is_refused(source: InMemoryBarSource) -> None:
    ctx = _ctx(ResearchStore(), source)
    too_far = PIN + FORECAST_POLICY.max_horizon + timedelta(hours=1)
    with pytest.raises(ToolExecutionError, match="policy maximum"):
        _record(ctx, horizon_end=too_far.isoformat())
    # Exactly the maximum is allowed.
    _record(ctx, horizon_end=(PIN + FORECAST_POLICY.max_horizon).isoformat())


def test_a_horizon_shorter_than_one_bar_is_refused(source: InMemoryBarSource) -> None:
    ctx = _ctx(ResearchStore(), source)
    with pytest.raises(ToolExecutionError, match="shorter than one D1 bar"):
        _record(ctx, horizon_end=(PIN + timedelta(hours=6)).isoformat())


def test_horizon_end_before_start_is_refused(source: InMemoryBarSource) -> None:
    ctx = _ctx(ResearchStore(), source)
    with pytest.raises(ToolExecutionError, match="must be after"):
        _record(ctx, horizon_end=PIN.isoformat())


def test_a_later_start_is_clamped_to_the_pin(source: InMemoryBarSource) -> None:
    ctx = _ctx(ResearchStore(), source)
    out = _record(ctx, horizon_start=(PIN + timedelta(days=2)).isoformat())
    assert out.horizon_start_clamped is True
    assert ctx.research.get_forecast(out.forecast_id).horizon_start == PIN


def test_a_default_start_is_the_pin(source: InMemoryBarSource) -> None:
    ctx = _ctx(ResearchStore(), source)
    out = _record(ctx)
    assert out.horizon_start_clamped is False
    assert ctx.research.get_forecast(out.forecast_id).horizon_start == PIN


def test_a_backdated_start_is_refused(source: InMemoryBarSource) -> None:
    ctx = _ctx(ResearchStore(), source)
    with pytest.raises(ToolExecutionError, match="backdated"):
        _record(ctx, horizon_start=(PIN - timedelta(days=1)).isoformat())


def test_an_unregistered_instrument_is_refused(source: InMemoryBarSource) -> None:
    ctx = _ctx(ResearchStore(), source)
    with pytest.raises(ToolExecutionError, match="unregistered instrument"):
        _record(ctx, instrument="NOTREAL")


@pytest.mark.parametrize(
    ("direction", "bucket"),
    [("higher", "<0.5atr"), ("lower", "<0.5atr"), ("range", "1-2atr"), ("range", ">2atr")],
)
def test_an_incoherent_magnitude_is_refused(
    source: InMemoryBarSource, direction: str, bucket: str
) -> None:
    ctx = _ctx(ResearchStore(), source)
    with pytest.raises(ToolExecutionError, match="incoherent claim"):
        _record(ctx, direction=direction, magnitude_bucket=bucket)


def test_the_forecast_stamps_its_scoring_unit_and_attribution(source: InMemoryBarSource) -> None:
    ctx = _ctx(ResearchStore(), source)
    out = _record(ctx)
    stored = ctx.research.get_forecast(out.forecast_id)
    assert stored.atr_period == FORECAST_POLICY.atr_period
    assert stored.range_band_atr == FORECAST_POLICY.range_band_atr
    assert stored.policy_version == FORECAST_POLICY.version
    assert (stored.created_by, stored.role, stored.model_id) == (
        "qr_1", "quant_researcher", "model-a"
    )
    assert stored.evidence_ids == EVIDENCE


def test_provenance_is_forward_only_when_written_before_the_horizon() -> None:
    start = datetime(2026, 1, 5, 12, tzinfo=UTC)
    assert classify_provenance(start, start) == "forward"
    assert classify_provenance(start + timedelta(minutes=59), start) == "forward"
    assert classify_provenance(start + timedelta(hours=2), start) == "backfill"
    # A pin in the past is a backfill: the wall clock is years after the start.
    ctx = _ctx(ResearchStore(), None)
    assert _record(ctx).provenance == "backfill"
    # A pin at "now" is forward.
    now_ctx = replace(ctx, as_of=datetime.now(tz=UTC))
    out = _record(
        now_ctx, horizon_end=(datetime.now(tz=UTC) + timedelta(days=3)).isoformat()
    )
    assert out.provenance == "forward"


# ======================================================== golden scoring


def _golden_store(source: InMemoryBarSource) -> tuple[ResearchStore, dict[str, str]]:
    store = ResearchStore()
    qr = _ctx(store, source)
    mra = _ctx(store, source, agent_id="mra_1", role="market_regime_analyst", model_id="model-b")
    ids = {
        "A": _record(qr, direction="higher", probability=0.8, magnitude_bucket="1-2atr").forecast_id,
        "B": _record(qr, direction="lower", probability=0.6).forecast_id,
        "C": _record(qr, direction="range", probability=0.7).forecast_id,
        "D": _record(mra, instrument="GBPUSD", direction="range", probability=0.9).forecast_id,
        "E": _record(mra, instrument="GBPUSD", direction="higher", probability=0.55).forecast_id,
    }
    return store, ids


@pytest.mark.golden
def test_golden_scoring_on_bars_with_known_atr_and_move(source: InMemoryBarSource) -> None:
    """Hand-calculated scores.

    EURUSD D1: every bar has close 1.0000, high 1.0050, low 0.9950 up to the bar
    stamped 2025-02-15, then close 1.0150 (high/low +-0.0050). With a constant
    close every true range is 0.0100, so Wilder ATR(14) = 0.0100 at every bar
    after its seed, including the start bar.

    Pin 2025-02-10 00:00: the start bar is the one stamped 2025-02-09 (it closes
    at the pin), close 1.0000. Horizon end 2025-02-20 00:00: the end bar is the
    one stamped 2025-02-19, close 1.0150.

        m = (1.0150 - 1.0000) / 0.0100 = +1.5 ATR  ->  higher, bucket 1-2atr

    A  higher p=0.80, bucket 1-2atr -> hit.   Brier (0.8-1)^2 = 0.04,  log ln 0.8
    B  lower  p=0.60                -> miss.  Brier (0.6-0)^2 = 0.36,  log ln 0.4
    C  range  p=0.70                -> miss.  Brier (0.7-0)^2 = 0.49,  log ln 0.3

    GBPUSD D1 is flat at 1.2500: m = 0 -> range.

    D  range  p=0.90 -> hit.                 Brier 0.01,   log ln 0.9
    E  higher p=0.55 -> 'range' (directional claim, no move): scored as the
       event not occurring.                   Brier 0.3025, log ln 0.45

    quant_researcher (A, B, C): hit rate 1/3, Brier (0.04+0.36+0.49)/3 = 0.89/3,
    log (ln 0.8 + ln 0.4 + ln 0.3)/3.
    market_regime_analyst (D, E): hit rate 1/2, Brier (0.01+0.3025)/2 = 0.15625,
    log (ln 0.9 + ln 0.45)/2.
    """
    store, ids = _golden_store(source)
    report = score_due_forecasts(store, source, NOW)
    assert report.n_scored_now == 5 and report.n_not_evaluable_now == 0

    by_id = {s.forecast_id: s for s in store.forecast_scores()}
    a = by_id[ids["A"]]
    assert a.atr_at_start == pytest.approx(0.01, abs=1e-12)
    assert a.start_price == pytest.approx(1.0) and a.end_price == pytest.approx(1.015)
    assert a.start_bar_closed_at == PIN
    assert a.end_bar_closed_at == datetime(2025, 2, 20, tzinfo=UTC)
    assert a.realised_move_atr == pytest.approx(1.5, abs=1e-9)
    assert a.realised_direction == "higher"
    assert a.realised_magnitude_bucket == "1-2atr" and a.magnitude_hit is True
    assert a.scorer_version == SCORER_VERSION and a.created_at == NOW

    expected = {
        "A": ("hit", 0.04, math.log(0.8)),
        "B": ("miss", 0.36, math.log(0.4)),
        "C": ("miss", 0.49, math.log(0.3)),
        "D": ("hit", 0.01, math.log(0.9)),
        "E": ("range", 0.3025, math.log(0.45)),
    }
    for key, (outcome, brier, log_score) in expected.items():
        score = by_id[ids[key]]
        assert score.outcome == outcome, key
        assert score.brier == pytest.approx(brier, abs=1e-12), key
        assert score.log_score == pytest.approx(log_score, abs=1e-12), key

    # All five were written with a pin in the past: backfill, never forward.
    assert all(r.provenance == "backfill" for r in report.by_role)
    roles = {r.group: r for r in report.by_role}
    qr, mra = roles["quant_researcher"], roles["market_regime_analyst"]
    assert (qr.n_evaluable, qr.n_hit, qr.n_miss, qr.n_range) == (3, 1, 2, 0)
    assert qr.hit_rate == pytest.approx(1 / 3)
    assert qr.brier == pytest.approx(0.89 / 3)
    assert qr.log_score == pytest.approx((math.log(0.8) + math.log(0.4) + math.log(0.3)) / 3)
    assert (qr.n_with_magnitude, qr.magnitude_hit_rate) == (1, 1.0)
    assert (mra.n_evaluable, mra.n_hit, mra.n_range) == (2, 1, 1)
    assert mra.hit_rate == pytest.approx(0.5)
    assert mra.brier == pytest.approx(0.15625)
    assert mra.log_score == pytest.approx((math.log(0.9) + math.log(0.45)) / 2)
    assert not qr.sufficient and not mra.sufficient  # n < 30: anecdote

    # Calibration: qr stated 0.6, 0.7, 0.8 once each; only the 0.8 came true.
    bins = {(b.lower, b.upper): b for b in qr.calibration}
    assert bins[(0.5, 0.6)].n == 0 and bins[(0.5, 0.6)].observed_frequency is None
    assert (bins[(0.6, 0.7)].n, bins[(0.6, 0.7)].observed_frequency) == (1, 0.0)
    assert (bins[(0.8, 0.9)].n, bins[(0.8, 0.9)].observed_frequency) == (1, 1.0)
    assert bins[(0.8, 0.9)].mean_probability == pytest.approx(0.8)

    models = {r.group for r in report.by_model}
    assert models == {"model-a", "model-b"}


def test_magnitude_bucket_edges_are_lower_inclusive() -> None:
    assert [magnitude_bucket(x) for x in (0.0, 0.4999, 0.5, 0.999, 1.0, 1.999, 2.0, 9.0)] == [
        "<0.5atr", "<0.5atr", "0.5-1atr", "0.5-1atr", "1-2atr", "1-2atr", ">2atr", ">2atr"
    ]


def test_evaluation_is_deterministic(source: InMemoryBarSource) -> None:
    store, ids = _golden_store(source)
    forecast = store.get_forecast(ids["A"])
    first = evaluate_forecast(forecast, source, scored_at=NOW)
    second = evaluate_forecast(forecast, source, scored_at=NOW)
    assert first == second


# ======================================================== idempotency


def test_rescoring_scores_nothing_twice(source: InMemoryBarSource) -> None:
    store, _ids = _golden_store(source)
    first = score_due_forecasts(store, source, NOW)
    second = score_due_forecasts(store, source, NOW + timedelta(days=5))
    assert first.n_scored_now == 5
    assert second.n_scored_now == 0
    assert second.n_already_scored == 5
    assert len(store.forecast_scores()) == 5
    assert first.by_role == second.by_role


def test_the_primary_key_refuses_a_second_score_for_one_forecast(
    source: InMemoryBarSource,
) -> None:
    store, ids = _golden_store(source)
    score_due_forecasts(store, source, NOW)
    duplicate = evaluate_forecast(store.get_forecast(ids["A"]), source, scored_at=NOW)
    with pytest.raises(ValueError, match="append-only"):
        store.add_forecast_score(duplicate)


def test_a_score_id_must_derive_from_its_forecast(source: InMemoryBarSource) -> None:
    store, ids = _golden_store(source)
    good = evaluate_forecast(store.get_forecast(ids["A"]), source, scored_at=NOW)
    payload = good.model_dump()
    payload["score_id"] = "fscore_something_else"
    with pytest.raises(ValidationError, match="one forecast, one score"):
        ForecastScore.model_validate(payload)


def test_forecasts_not_yet_due_are_left_alone(source: InMemoryBarSource) -> None:
    store, _ids = _golden_store(source)
    early = score_due_forecasts(store, source, datetime(2025, 2, 19, 23, tzinfo=UTC))
    assert (early.n_due, early.n_not_yet_due, early.n_scored_now) == (0, 5, 0)
    lagged = score_due_forecasts(store, source, NOW, settlement_lag=timedelta(days=30))
    assert lagged.n_scored_now == 0
    assert store.forecast_scores() == ()


# ======================================================== NOT_EVALUABLE


def test_missing_bars_are_not_evaluable_never_skipped(source: InMemoryBarSource) -> None:
    store = ResearchStore()
    ctx = _ctx(store, source)
    no_source = _record(ctx, instrument="USDJPY").forecast_id  # nothing in the source
    ends_early = _record(ctx, instrument="AUDUSD").forecast_id  # data stops 2025-02-12
    no_history = _record(ctx, instrument="NZDUSD").forecast_id  # 9 bars before the pin
    report = score_due_forecasts(store, source, NOW)
    assert report.n_scored_now == 3
    assert report.n_not_evaluable_now == 3
    by_id = {s.forecast_id: s for s in store.forecast_scores()}
    assert "bars unavailable" in by_id[no_source].not_evaluable_reason
    assert "missing data, not a price" in by_id[ends_early].not_evaluable_reason
    assert "ATR(14)" in by_id[no_history].not_evaluable_reason
    for score in by_id.values():
        assert score.outcome == "not_evaluable"
        assert score.brier is None and score.log_score is None
    row = next(r for r in report.by_role if r.group == "quant_researcher")
    assert (row.n_scored, row.n_evaluable, row.n_not_evaluable) == (3, 0, 3)
    assert row.hit_rate is None and row.brier is None  # absence, not zero
    # And they are final: a rerun does not retry them into a number.
    assert score_due_forecasts(store, source, NOW).n_scored_now == 0


def test_a_scorer_clock_must_be_timezone_aware(source: InMemoryBarSource) -> None:
    with pytest.raises(ValueError, match="timezone-naive"):
        score_due_forecasts(ResearchStore(), source, datetime(2025, 3, 1))


# ======================================================== trial count and reads


def test_every_forecast_counts_toward_the_trial_ledger(source: InMemoryBarSource) -> None:
    store, _ids = _golden_store(source)
    _record(_ctx(store, source), instrument="USDJPY")  # will be not_evaluable
    _record(_ctx(store, source), horizon_end=(PIN + timedelta(days=29)).isoformat())  # pending
    score_due_forecasts(store, source, NOW)
    assert n_forecasts_by_actor(store) == {"mra_1": 2, "qr_1": 5}


def test_query_forecast_scores_reports_backfills_only_when_asked(
    source: InMemoryBarSource,
) -> None:
    store, _ids = _golden_store(source)
    score_due_forecasts(store, source, NOW)
    reader = _ctx(store, source, as_of=None, agent_id="rd_1", role="research_director")
    forward = _query(reader)
    assert forward.aggregates == ()  # nothing here was written before its horizon
    assert forward.n_forecasts == 5 and forward.n_scored == 5
    backfill = _query(reader, provenance="backfill", group_by="model")
    assert {a.group for a in backfill.aggregates} == {"model-a", "model-b"}
    assert any("not evidence of skill" in c for c in backfill.caveats)
    assert backfill.n_forecasts_by_actor == {"mra_1": 2, "qr_1": 3}


def test_a_pinned_reader_cannot_see_outcomes_after_its_clock(
    source: InMemoryBarSource,
) -> None:
    store, _ids = _golden_store(source)
    score_due_forecasts(store, source, NOW)
    before_end = _query(
        _ctx(store, source, as_of=datetime(2025, 2, 15, tzinfo=UTC)),
        provenance="backfill",
    )
    assert before_end.as_of_applied is True
    assert before_end.n_forecasts == 5 and before_end.n_scored == 0
    assert before_end.aggregates == ()
    board = scoreboard(store, group_by="role", provenance="all", as_of=NOW)
    assert board.n_scored == 5


# ======================================================== through a session


def test_a_forecasting_role_records_through_its_session() -> None:
    harness = Harness()
    session = open_session(
        agent_id="mra@wf",
        role=AgentRole.MARKET_REGIME_ANALYST,
        context=replace(harness.context, as_of=datetime(2024, 1, 20, tzinfo=UTC)),
        resolver=harness.resolver,
        ledger=harness.ledger,
    )
    out = session.call(
        "record_forecast",
        _inputs(horizon_end="2024-01-25T00:00:00Z", timeframe="H1"),
        reason="pre-register the regime call",
    )
    stored = harness.research.get_forecast(out.forecast_id)
    assert stored.created_by == "mra@wf" and stored.role == "market_regime_analyst"
    with pytest.raises(ToolNotInRole):
        session.call("query_forecast_scores", {}, reason="peek at my own scorecard")


def test_the_execution_and_portfolio_analysts_cannot_forecast() -> None:
    harness = Harness()
    for role in (AgentRole.EXECUTION_ANALYST, AgentRole.PORTFOLIO_ANALYST):
        session = open_session(
            agent_id=f"{role.value}@wf",
            role=role,
            context=replace(harness.context, as_of=PIN),
            resolver=harness.resolver,
            ledger=harness.ledger,
        )
        with pytest.raises(ToolNotInRole):
            session.call("record_forecast", _inputs(), reason="try")
    assert harness.research.forecasts() == ()


def test_the_research_director_reads_scores_through_its_session() -> None:
    harness = Harness()
    session = open_session(
        agent_id="rd@wf",
        role=AgentRole.RESEARCH_DIRECTOR,
        context=harness.context,
        resolver=harness.resolver,
        ledger=harness.ledger,
    )
    out = session.call("query_forecast_scores", {"group_by": "model"}, reason="who is calibrated")
    assert out.n_forecasts == 0 and out.aggregates == ()


def test_the_forecast_artefact_rejects_an_inverted_horizon() -> None:
    with pytest.raises(ValidationError, match="strictly after"):
        Forecast(
            instrument="EURUSD",
            timeframe="D1",
            horizon_start=PIN,
            horizon_end=PIN,
            direction="higher",
            probability=0.7,
            invalid_if=INVALID_IF,
            evidence_ids=EVIDENCE,
            reason=REASON,
            provenance="forward",
            atr_period=14,
            range_band_atr=0.5,
            policy_version=FORECAST_POLICY.version,
        )
