"""The twentieth gateway check, ``event_veto``: veto-only, new entries only, shadow first.

What is pinned here:

* disabled (the default): the would-be verdict is recorded as
  ``event_veto_shadow:<code>`` and the order is ALLOWED; the attempt row's
  blocking reason stays empty;
* enabled: a matching annotation blocks a NEW entry as ``event_veto:<code>``;
* an unavailable source never blocks (fail-open by design) but says so;
* an INCREASE and every exit are not screened;
* a raise inside the source still fails closed, like every other check;
* the check reads no size, stop, exit, limit or kill-switch state (AST).
"""
from __future__ import annotations

import ast
import inspect
import textwrap
from dataclasses import dataclass, field

import pandas as pd

from fiboki.core.contracts import RiskDecision, VetoAssessment, VetoReason
from fiboki.core.enums import ExecutionMode
from fiboki.marketstate.events import EventVetoPolicy, EventVetoSource
from fiboki.risk import gateway as gateway_module
from fiboki.risk.gateway import (
    EventVetoProvider,
    ExitContext,
    InMemoryAttemptRecorder,
    RiskGateway,
)
from fiboki.risk.killswitch import RequestKind
from tests.event_fixtures import AT, annotation_store, draft
from tests.exec_fixtures import NOW, healthy_market, healthy_venue, make_context

REASON = VetoReason(
    annotation_id="evt_x",
    event_type="geopolitical",
    buckets=("USD",),
    severity=3,
    confidence=0.9,
    observed_at=NOW - pd.Timedelta(minutes=30),
    policy_version="event_veto_v1",
)


@dataclass
class FakeSource:
    enabled: bool = False
    available: bool = True
    veto: VetoReason | None = REASON
    detail: str = ""
    calls: list = field(default_factory=list)
    policy_version: str = "event_veto_v1"

    def assess(self, instrument, at) -> VetoAssessment:
        self.calls.append((instrument, at))
        return VetoAssessment(
            self.policy_version, self.enabled, self.available, self.veto, self.detail
        )


def _evaluate(source, **kwargs) -> tuple[RiskDecision, InMemoryAttemptRecorder]:
    recorder = InMemoryAttemptRecorder()
    decision = RiskGateway(recorder=recorder).evaluate(make_context(event_veto=source, **kwargs))
    return decision, recorder


def test_the_fake_satisfies_the_protocol_and_so_does_the_real_source(tmp_path) -> None:
    assert isinstance(FakeSource(), EventVetoProvider)
    assert isinstance(EventVetoSource(tmp_path / "x.sqlite"), EventVetoProvider)


def test_event_veto_is_the_twentieth_named_check_and_not_an_exit_check() -> None:
    assert len(RiskGateway.CHECKS) == 20
    assert RiskGateway.CHECKS.index("event_veto") == RiskGateway.CHECKS.index("event_blackout") + 1
    assert "event_veto" not in RiskGateway.EXIT_CHECKS


def test_disabled_policy_records_the_shadow_verdict_and_allows() -> None:
    decision, recorder = _evaluate(FakeSource(enabled=False))
    assert decision.allowed, decision.reasons
    assert decision.reasons == ("event_veto_shadow:geopolitical:USD:sev3:evt_x",)
    assert decision.shadow_reasons == decision.reasons
    assert decision.blocking_reasons == ()
    attempt = recorder.attempts[0]
    assert attempt.allowed and attempt.reason == ""
    telemetry = attempt.to_telemetry()
    assert telemetry.venue_error is None, "a shadow note must never read as a venue refusal"
    assert telemetry.extra["shadow_reasons"] == list(decision.reasons)
    assert telemetry.extra["event_veto_policy"] == "event_veto_v1"


def test_enabled_policy_blocks_a_new_entry_with_a_named_reason() -> None:
    decision, recorder = _evaluate(FakeSource(enabled=True))
    assert not decision.allowed
    assert decision.blocking_reasons == ("event_veto:geopolitical:USD:sev3:evt_x",)
    assert recorder.attempts[0].reason == "event_veto:geopolitical:USD:sev3:evt_x"


def test_no_matching_annotation_leaves_no_trace_in_the_reasons() -> None:
    for enabled in (False, True):
        decision, _ = _evaluate(FakeSource(enabled=enabled, veto=None))
        assert decision.allowed and decision.reasons == ()


def test_an_unavailable_source_never_blocks_but_says_so() -> None:
    for enabled in (False, True):
        decision, _ = _evaluate(
            FakeSource(enabled=enabled, available=False, veto=None, detail="store_missing")
        )
        assert decision.allowed, (enabled, decision.reasons)
        assert decision.reasons == ("event_veto_shadow:unavailable:store_missing",)


def test_a_stale_source_with_an_annotation_still_vetoes_when_enabled() -> None:
    decision, _ = _evaluate(FakeSource(enabled=True, available=False, detail="stale:9000s>3600s"))
    assert not decision.allowed
    assert decision.blocking_reasons == ("event_veto:geopolitical:USD:sev3:evt_x",)
    shadow, _ = _evaluate(FakeSource(enabled=False, available=False, detail="stale:9000s>3600s"))
    assert shadow.allowed
    assert shadow.reasons == (
        "event_veto_shadow:unavailable:stale:9000s>3600s",
        "event_veto_shadow:geopolitical:USD:sev3:evt_x",
    )


def test_an_increase_is_not_screened_only_new_entries_are() -> None:
    source = FakeSource(enabled=True)
    decision, _ = _evaluate(source, request_kind=RequestKind.INCREASE)
    assert "event_veto" in decision.checks_run
    assert not any(r.startswith("event_veto") for r in decision.reasons)
    assert source.calls == []


def test_an_unwired_source_passes_and_the_attempt_says_not_wired() -> None:
    decision, recorder = _evaluate(None)
    assert decision.allowed and decision.reasons == ()
    assert recorder.attempts[0].extra["event_veto_policy"] == "not_wired"


def test_the_source_is_asked_about_this_instrument_at_this_instant() -> None:
    source = FakeSource()
    _evaluate(source)
    assert source.calls == [("EURUSD", NOW)]


def test_a_source_that_raises_fails_closed_like_every_check() -> None:
    class Exploding(FakeSource):
        def assess(self, instrument, at):
            raise RuntimeError("bug")

    decision, _ = _evaluate(Exploding())
    assert not decision.allowed
    assert any(r.startswith("check_error:event_veto:RuntimeError") for r in decision.reasons)


def test_a_mislabelled_shadow_note_blocks(monkeypatch) -> None:
    """Only the declared prefixes are non-blocking; anything else fails closed."""
    gw = RiskGateway(recorder=InMemoryAttemptRecorder())

    def bogus(ctx, limits):
        raise gateway_module._ShadowNote("not_a_shadow_prefix:x")

    monkeypatch.setattr(gw, "_check_event_veto", bogus)
    decision = gw.evaluate(make_context())
    assert not decision.allowed
    assert decision.blocking_reasons == ("not_a_shadow_prefix:x",)


def test_an_exit_never_consults_the_event_source() -> None:
    source = FakeSource(enabled=True)
    ctx = ExitContext(
        instrument="EURUSD", strategy_id="s", size=1.0, now=NOW, mode=ExecutionMode.PAPER,
        market=healthy_market(), venue=healthy_venue(),
    )
    decision = RiskGateway(recorder=InMemoryAttemptRecorder()).evaluate_exit(ctx)
    assert decision.allowed
    assert source.calls == []


def test_end_to_end_with_the_real_source_and_store(tmp_path) -> None:
    path = tmp_path / "events" / "annotations.sqlite"
    annotation_store(path, (draft(),), scans=((AT - pd.Timedelta(minutes=10), "ok"),)).close()
    assert AT == NOW
    shadow, _ = _evaluate(EventVetoSource(path))
    assert shadow.allowed
    (note,) = shadow.reasons
    assert note.startswith("event_veto_shadow:geopolitical:USD:sev3:evt_")
    live, _ = _evaluate(EventVetoSource(path, EventVetoPolicy(enabled=True)))
    assert not live.allowed
    assert live.blocking_reasons[0].startswith("event_veto:geopolitical:USD:sev3:evt_")
    # EURGBP carries no USD: neither policy has anything to say.
    from tests.exec_fixtures import make_plan, make_signal

    plan = make_plan(make_signal(instrument="EURGBP", reference_price=0.85, stop_distance=0.003))
    quiet, _ = _evaluate(
        EventVetoSource(path, EventVetoPolicy(enabled=True)), plan=plan,
        market=healthy_market(instrument="EURGBP", mid_price=0.85),
    )
    assert not any(r.startswith("event_veto") for r in quiet.reasons), quiet.reasons


# -------------------------------------------------------------- structure

#: Attributes that would mean the veto reads or touches sizing, stops, exits,
#: limits or the kill switch.
_FORBIDDEN_ATTRS = {
    "size", "risk_amount", "stop_price", "take_profit_prices", "take_profit_allocations",
    "adjusted_size", "kill_switch", "snapshot", "open_risk_amount", "portfolio_weight",
    "max_leverage_applied", "limits", "default_limits", "correlated_exposure",
}


def _check_source_tree() -> ast.FunctionDef:
    source = textwrap.dedent(inspect.getsource(RiskGateway._check_event_veto))
    fn = ast.parse(source).body[0]
    assert isinstance(fn, ast.FunctionDef)
    return fn


def test_the_veto_check_never_touches_sizing_stops_exits_limits_or_the_kill_switch() -> None:
    fn = _check_source_tree()
    attrs = {n.attr for n in ast.walk(fn) if isinstance(n, ast.Attribute)}
    assert not attrs & _FORBIDDEN_ATTRS, attrs & _FORBIDDEN_ATTRS
    names = {n.id for n in ast.walk(fn) if isinstance(n, ast.Name)}
    assert "limits" not in names, "the veto must not read the limit set"
    # It can only return, block or leave a shadow note. It never mutates anything.
    for node in ast.walk(fn):
        targets = node.targets if isinstance(node, ast.Assign) else (
            [node.target] if isinstance(node, ast.AugAssign | ast.AnnAssign) else []
        )
        assert all(isinstance(t, ast.Name) for t in targets), "only local names are assigned"
    raises = [n for n in ast.walk(fn) if isinstance(n, ast.Raise)]
    assert all(
        isinstance(r.exc, ast.Call) and getattr(r.exc.func, "id", "") == "_ShadowNote"
        for r in raises
    )


def test_the_event_veto_check_is_only_referenced_by_its_own_name() -> None:
    """Nothing else in the gateway calls it, so it cannot leak into the exit set."""
    tree = ast.parse(inspect.getsource(gateway_module))
    calls = [
        n for n in ast.walk(tree)
        if isinstance(n, ast.Attribute) and n.attr == "_check_event_veto"
    ]
    assert calls == []  # dispatched only by name from CHECKS via getattr
