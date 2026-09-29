"""The paper runtime sizes through portfolio construction (audit F P1-11).

Before: ``SignalEvaluator`` called ``size_trade`` at a flat ``risk_fraction``
and ``portfolio/construction.py`` ran in no runtime, so the tier table, the
drawdown throttle, the correlation-aware budgets and the regime scalar were
unit-tested decoration. After: every bar's signals are allocated by
``PortfolioConstructor`` against the venue's own book, then sized ONCE at the
tier base risk times a weight <= 1.0, and every gateway attempt row says why.
"""
from __future__ import annotations

import ast
from datetime import datetime
from pathlib import Path

import pandas as pd
import pytest

from fiboki.broker.paper import PaperConfig
from fiboki.core.contracts import Signal, VetoAssessment, VetoReason
from fiboki.core.enums import Direction
from fiboki.core.money import IdentityFxSource
from fiboki.core.tier import AgentInfluenceTier, TierReading
from fiboki.portfolio.construction import ConstructionConfig, ConvictionPolicy
from fiboki.portfolio.sizing import SizingPolicy
from fiboki.risk.killswitch import KillSwitch, KillSwitchMode
from fiboki.risk.limits import PAPER_LIMITS
from fiboki.sim.profiles import IG_REALISTIC
from fiboki.workers.base import WorkerStore
from fiboki.workers.live_worker import LiveWorkerConfig
from fiboki.workers.runtime import (
    ConvictionAdapter,
    TierGatedVetoSource,
    build_replay_session,
)
from tests.exec_fixtures import synthetic_frame

SRC = Path(__file__).resolve().parents[2] / "src" / "fiboki"


class _Breakout:
    """Closed-bar breakout on the bars seen so far; predictable from the bars."""

    def __call__(self, symbol: str, history: pd.DataFrame, now: pd.Timestamp) -> list[Signal]:
        if len(history) < 24:
            return []
        window = history.iloc[-21:-1]
        close = float(history["close"].iloc[-1])
        risk = max(float(window["high"].max() - window["low"].min()) * 0.5, close * 0.002)
        if close > float(window["high"].max()):
            direction, sign = Direction.LONG, 1.0
        elif close < float(window["low"].min()):
            direction, sign = Direction.SHORT, -1.0
        else:
            return []
        return [
            Signal(
                strategy_id="sizing_path", instrument=symbol, timeframe="H1",
                direction=direction, bar_time=now, reference_price=close,
                stop_price=close - sign * risk, take_profit_prices=(close + sign * risk * 2,),
            )
        ]


@pytest.fixture()
def store(tmp_path):
    with WorkerStore.sqlite_at(tmp_path / "workers.sqlite") as handle:
        yield handle


def _session(store, tmp_path, *, cycles=600, **kwargs):
    kwargs.setdefault("kill_switch", KillSwitch.at_path(tmp_path / "killswitch.jsonl"))
    frame = synthetic_frame(n=cycles, seed=11)
    return build_replay_session(
        frames={"EURUSD": frame},
        signal_source=_Breakout(),
        store=store,
        paper_config=PaperConfig(
            initial_balance=100_000.0, account_ccy="USD", profile=IG_REALISTIC,
            max_concurrent=2, max_per_instrument=1, strategy_id="sizing_path",
        ),
        sizing_policy=kwargs.pop("sizing_policy", SizingPolicy(risk_fraction=0.01)),
        fx=IdentityFxSource(),
        timeframe="H1",
        warmup=30,
        limits=PAPER_LIMITS,
        allow_empty_calendar=True,
        worker_config=LiveWorkerConfig(
            max_cycles=cycles, idle_sleep_seconds=0.0, busy_sleep_seconds=0.0,
            reconcile_every_cycles=10_000, lifecycle_every_cycles=10_000,
            lease_name=f"sizing-path-{id(frame)}",
        ),
        **kwargs,
    )


# ------------------------------------------------------------ structural


def test_evaluate_allocates_the_batch_then_sizes_each_plan_once() -> None:
    tree = ast.parse((SRC / "workers" / "runtime.py").read_text(encoding="utf-8"))
    size_calls, evaluate = [], None
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and getattr(node.func, "id", "") == "size_trade":
            size_calls.append(node)
        if isinstance(node, ast.ClassDef) and node.name == "SignalEvaluator":
            evaluate = next(
                f for f in node.body if isinstance(f, ast.FunctionDef) and f.name == "evaluate"
            )
    assert evaluate is not None
    inside = [n for n in ast.walk(evaluate) if isinstance(n, ast.Call)]
    allocate = [n for n in inside if getattr(n.func, "attr", "") == "allocate"]
    sized = [n for n in inside if getattr(n.func, "id", "") == "size_trade"]
    assert len(allocate) == 1 and len(sized) == 1
    assert len(size_calls) == 1, "a second size_trade call site appeared in the runtime"
    assert allocate[0].lineno < sized[0].lineno
    keywords = {k.arg for k in sized[0].keywords}
    assert {"portfolio_weight", "policy"} <= keywords


# -------------------------------------------------------------- behaviour


def test_the_session_sizes_through_the_constructor_and_stamps_every_attempt(
    store, tmp_path
) -> None:
    session = _session(store, tmp_path)
    assert session.worker.run(install_signals=False) == 0
    evaluator = session.evaluator
    assert evaluator.allocations, "no allocation ran; the constructor is still unwired"
    assert evaluator.plan_stamps, "nothing was sized; the assertions below would be vacuous"
    for stamp in evaluator.plan_stamps.values():
        assert stamp["construction_version"] == "construction_v2"
        assert stamp["strategy_tier"] == "probationary"
        assert stamp["base_risk_pct"] == pytest.approx(0.25)
        assert 0.0 < stamp["portfolio_weight"] <= 1.0
        assert stamp["allocated_risk_pct"] <= stamp["base_risk_pct"] + 1e-12
        assert stamp["agent_tier"] == "t1_annotate_shadow"
    attempts = list(session.gateway.recorder.attempts)
    assert attempts
    for attempt in attempts:
        assert attempt.extra["agent_tier"] == "t1_annotate_shadow"
        if attempt.plan_id in evaluator.plan_stamps:
            assert attempt.extra["sizing"]["portfolio_weight"] == attempt.extra["portfolio_weight"]
    summary = session.summary()["sizing"]
    assert summary["construction_version"] == "construction_v2"
    assert summary["kill_switch_journal"] == "durable"
    assert summary["conviction_effective"] is False
    assert summary["allocations"] == len(evaluator.allocations)


def test_the_sizing_policy_is_a_ceiling_on_the_tier_base(store, tmp_path) -> None:
    session = _session(store, tmp_path, sizing_policy=SizingPolicy(risk_fraction=0.001))
    session.worker.run(install_signals=False)
    stamps = list(session.evaluator.plan_stamps.values())
    assert stamps
    assert all(s["base_risk_pct"] == pytest.approx(0.10) for s in stamps)


def test_the_gateway_reads_the_operators_durable_journal(store, tmp_path, monkeypatch) -> None:
    """No kill switch passed: the gateway resolves the SAME journal the CLI writes."""
    monkeypatch.setenv("FIBOKI_STATE_DIR", str(tmp_path / "state"))
    session = _session(store, tmp_path, kill_switch=None, cycles=300)
    switch = session.gateway.kill_switch
    assert switch.durable
    assert Path(switch.journal.path) == tmp_path / "state" / "killswitch.jsonl"
    KillSwitch.at_path(tmp_path / "state" / "killswitch.jsonl").activate(
        KillSwitchMode.PAUSE, reason="operator pause from another process", operator="joe"
    )
    session.worker.run(install_signals=False)
    assert session.evaluator.plan_stamps, "no plan reached the gateway; vacuous"
    assert not [s for s in session.worker.submissions if s.accepted]


def test_config_enabled_conviction_below_t3_runs_in_shadow_and_alerts(store, tmp_path) -> None:
    alerts: list[tuple[str, dict]] = []
    baseline = _session(store, tmp_path, cycles=400)
    baseline.worker.run(install_signals=False)
    session = _session(
        store, tmp_path / "b", cycles=400,
        construction=ConstructionConfig(conviction=ConvictionPolicy(enabled=True)),
        alert=lambda message, context: alerts.append((message, dict(context))),
    )
    session.worker.run(install_signals=False)
    assert len(alerts) == 1 and "SHADOW" in alerts[0][0]
    assert session.summary()["sizing"]["conviction_gated_by_tier"] is True
    weights = sorted(s["portfolio_weight"] for s in session.evaluator.plan_stamps.values())
    assert weights == sorted(
        s["portfolio_weight"] for s in baseline.evaluator.plan_stamps.values()
    )


# ------------------------------------------------------------ the veto gate


class _Veto:
    enabled = True
    policy_version = "event_veto_v1"

    def assess(self, instrument: str, at: datetime) -> VetoAssessment:
        reason = VetoReason("ann_1", "central_bank", ("USD",), 3, 0.9,
                            pd.Timestamp("2026-09-29", tz="UTC"), "event_veto_v1")
        return VetoAssessment("event_veto_v1", True, True, reason, "")


def test_the_event_veto_cannot_block_below_t2() -> None:
    alerts: list[str] = []
    gated = TierGatedVetoSource(
        _Veto(), TierReading.default("absent"), alert=lambda m, c: alerts.append(m)
    )
    verdict = gated.assess("EURUSD", datetime(2026, 9, 29))
    assert gated.enabled is False and verdict.enabled is False
    assert verdict.veto is not None, "the shadow record keeps the would-be veto"
    assert len(alerts) == 1
    open_ = TierGatedVetoSource(
        _Veto(), TierReading(tier=AgentInfluenceTier.T2_VETO_ENTRIES, source="record")
    )
    assert open_.enabled is True and open_.assess("EURUSD", datetime(2026, 9, 29)).enabled


def test_a_disabled_veto_policy_does_not_alert() -> None:
    class _Off(_Veto):
        enabled = False

    alerts: list[str] = []
    TierGatedVetoSource(_Off(), TierReading.default(), alert=lambda m, c: alerts.append(m))
    assert alerts == []


# ------------------------------------------------------ the conviction adapter


NOW = pd.Timestamp("2026-09-29 12:00", tz="UTC")


def _row(**kw):
    row = {
        "conviction_id": "cv_1", "instrument": "EURUSD", "stance": "short", "strength": 2,
        "as_of": "2026-09-29T08:00:00.000000Z", "available_at": "2026-09-29T08:00:30.000000Z",
        "valid_until": "2026-09-29T12:00:00.000000Z", "policy_version": "thesis_debate_v1",
    }
    row.update(kw)
    return row


def test_the_adapter_builds_a_point_in_time_reading() -> None:
    reading = ConvictionAdapter(lambda inst, at: _row()).reading("eurusd", NOW)
    assert reading is not None and reading.instrument == "EURUSD"
    assert reading.as_of == pd.Timestamp("2026-09-29 08:00:30", tz="UTC")
    assert reading.artefact_id == "cv_1"


@pytest.mark.parametrize(
    "rows",
    [
        lambda inst, at: None,
        lambda inst, at: _row(stance="buy"),
        lambda inst, at: _row(strength=7),
        lambda inst, at: {"conviction_id": "cv"},
        lambda inst, at: (_ for _ in ()).throw(OSError("disk gone")),
    ],
)
def test_the_adapter_is_fail_neutral(rows) -> None:
    adapter = ConvictionAdapter(rows)
    assert adapter.reading("EURUSD", NOW) is None
