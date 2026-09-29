"""The ``locks`` block in the Strategy DSL: schema, hashing, binding, compiling.

The properties that matter most are about what does NOT change: a document with
no locks (or an inert block) is the same document it always was, and a document
with locks is a different strategy with a different content hash -- which is
correct, because a lock changes which trades exist.
"""
from __future__ import annotations

import json

import pytest
from pydantic import ValidationError

from fiboki.backtest.exits import exit_policy_from_document, lock_policy_from_document
from fiboki.backtest.locks import LockScope
from fiboki.strategy.compiler import CompilationError, compile_strategy
from fiboki.strategy.dsl import (
    LOCKS_DECLARED_FEATURE,
    ParameterSpec,
    StrategyDocument,
)
from tests.lock_fixtures import lock_document, lock_document_payload, session_frame

STREAK = {"n_stops": 2, "lookback_bars": 30, "lock_bars": 12, "scope": "instrument"}
LOCKS = {"cooldown_bars_after_close": 3, "stop_streak": STREAK}


def test_absent_locks_are_absent_from_every_dump() -> None:
    doc = lock_document(None)
    assert doc.locks is None
    assert "locks" not in doc.model_dump(mode="json")
    assert "locks" not in json.loads(doc.to_json())
    assert "locks" not in doc.semantic_payload()


def test_declaring_locks_changes_the_content_hash() -> None:
    plain = lock_document(None)
    locked = lock_document(LOCKS)
    assert locked.content_hash() != plain.content_hash()
    assert locked.semantic_payload()["locks"]["cooldown_bars_after_close"] == 3
    # and a different lock is a different strategy again
    other = lock_document({**LOCKS, "cooldown_bars_after_close": 4})
    assert other.content_hash() != locked.content_hash()


@pytest.mark.parametrize(
    "inert",
    [None, {}, {"cooldown_bars_after_close": 0}, {"stop_streak": None},
     {"cooldown_bars_after_close": 0, "stop_streak": None}],
)
def test_an_inert_block_is_the_same_document_as_none(inert) -> None:
    """Otherwise ``"locks": {}`` would mint a new hash -- a new holdout look --
    for a strategy that behaves identically."""
    payload = lock_document_payload(None)
    payload["locks"] = inert
    doc = StrategyDocument.model_validate(payload)
    assert doc.locks is None
    assert doc.content_hash() == lock_document(None).content_hash()
    assert doc.to_json() == lock_document(None).to_json()


def test_round_trip_preserves_locks_and_hash() -> None:
    doc = lock_document(LOCKS)
    again = StrategyDocument.from_json(doc.to_json())
    assert again == doc
    assert again.content_hash() == doc.content_hash()


@pytest.mark.parametrize(
    "bad",
    [
        {"cooldown_bars_after_close": -1},
        {"stop_streak": {**STREAK, "n_stops": 1}},
        {"stop_streak": {**STREAK, "lookback_bars": 0}},
        {"stop_streak": {**STREAK, "lock_bars": 0}},
        {"stop_streak": {**STREAK, "scope": "portfolio"}},
        {"stop_streak": {**STREAK, "max_drawdown": 0.1}},
        {"cooldown_minutes": 60},
    ],
)
def test_the_schema_refuses_bad_locks(bad) -> None:
    with pytest.raises(ValidationError):
        lock_document(bad)


def test_complexity_counts_each_active_rule() -> None:
    base = lock_document(None).complexity_score
    assert lock_document({"cooldown_bars_after_close": 2}).complexity_score == base + 1.0
    assert lock_document({"stop_streak": STREAK}).complexity_score == base + 1.0
    assert lock_document(LOCKS).complexity_score == base + 2.0


def test_locks_can_be_parameterised_and_bound() -> None:
    payload = lock_document_payload(
        {
            "cooldown_bars_after_close": {"$param": "cooldown"},
            "stop_streak": {**STREAK, "lock_bars": {"$param": "lock_len"}},
        }
    )
    payload["parameters"] = {
        "cooldown": ParameterSpec(kind="int", default=3, min_value=0, max_value=10).model_dump(),
        "lock_len": ParameterSpec(kind="int", default=12, min_value=1, max_value=48).model_dump(),
    }
    template = StrategyDocument.model_validate(payload)
    assert set(template.unbound_parameters()) == {"cooldown", "lock_len"}
    with pytest.raises(CompilationError):
        compile_strategy(template)

    bound = template.bind({"cooldown": 5, "lock_len": 20})
    assert bound.locks.cooldown_bars_after_close == 5
    assert bound.locks.stop_streak.lock_bars == 20
    policy = lock_policy_from_document(bound)
    assert policy.cooldown_bars_after_close == 5
    assert policy.stop_streak.lock_bars == 20
    assert policy.stop_streak.scope is LockScope.INSTRUMENT

    # Binding a cooldown to zero with no streak leaves an inert block: dropped.
    only_cd = lock_document_payload({"cooldown_bars_after_close": {"$param": "cooldown"}})
    only_cd["parameters"] = {"cooldown": payload["parameters"]["cooldown"]}
    zero = StrategyDocument.model_validate(only_cd).bind({"cooldown": 0})
    assert zero.locks is None


def test_the_compiler_refuses_a_streak_that_can_never_arm() -> None:
    """One position at a time, a 3-bar cooldown: 2 stops span at least
    (2 - 1) * (3 + 1) + 1 = 5 session bars. A 4-bar lookback cannot see them."""
    with pytest.raises(CompilationError, match="can never arm"):
        compile_strategy(
            lock_document(
                {"cooldown_bars_after_close": 3, "stop_streak": {**STREAK, "lookback_bars": 4}}
            )
        )
    compile_strategy(
        lock_document(
            {"cooldown_bars_after_close": 3, "stop_streak": {**STREAK, "lookback_bars": 5}}
        )
    )
    # Strategy scope can see stops on several instruments at once: no floor.
    compile_strategy(
        lock_document({"stop_streak": {**STREAK, "lookback_bars": 1, "scope": "strategy"}})
    )


def test_signals_carry_the_declaration_only_when_locks_are_declared() -> None:
    frame = session_frame(400)
    seen: dict[bool, set[str]] = {}
    for declared, locks in ((False, None), (True, LOCKS)):
        compiled = compile_strategy(lock_document(locks))
        prepared = compiled.prepare(frame)
        keys: set[str] = set()
        for j in range(len(prepared)):
            signal = compiled.generate_signal(prepared, j, "EURUSD", "H1")
            if signal is not None:
                keys.update(signal.features)
        assert keys, "the fixture produced no signals"
        seen[declared] = keys
    assert LOCKS_DECLARED_FEATURE not in seen[False]
    assert LOCKS_DECLARED_FEATURE in seen[True]
    assert seen[True] - {LOCKS_DECLARED_FEATURE} == seen[False]


def test_the_exit_policy_carries_locks_only_when_declared() -> None:
    plain = exit_policy_from_document(lock_document(None))
    locked = exit_policy_from_document(lock_document(LOCKS))
    assert plain.locks is None and "locks" not in plain.fingerprint()
    assert locked.locks is not None
    assert locked.fingerprint()["locks"] == {
        "cooldown_bars_after_close": 3,
        "stop_streak": {"n_stops": 2, "lookback_bars": 30, "lock_bars": 12,
                        "scope": "instrument"},
        "calendar": "session_bars:interbank_weekend:fri17-sun17_america_new_york",
    }
    # Every other key of the fingerprint is untouched by declaring locks.
    assert {k: v for k, v in locked.fingerprint().items() if k != "locks"} == plain.fingerprint()


def test_the_engine_refuses_a_signal_on_the_wrong_timeframe() -> None:
    """A lock counted in H1 bars on an H4 signal is a lock of the wrong length."""
    from fiboki.backtest.engine import (
        BacktestConfig,
        FixedSizeSizer,
        PrecomputedSignals,
        run_backtest,
    )
    from fiboki.core.contracts import Signal
    from fiboki.core.enums import Direction
    from fiboki.core.money import IdentityFxSource

    frame = session_frame(50)
    ts = frame.index[10]
    ref = float(frame["close"].iloc[10])
    signal = Signal(
        strategy_id="x", instrument="EURUSD", timeframe="H4", direction=Direction.LONG,
        bar_time=ts, reference_price=ref, stop_price=ref - 0.002,
    )
    with pytest.raises(ValueError, match="wrong size"):
        run_backtest(
            data={"EURUSD": frame},
            config=BacktestConfig(initial_balance=10_000.0, account_ccy="USD"),
            strategy=PrecomputedSignals([signal]),
            sizer=FixedSizeSizer(1000.0),
            fx=IdentityFxSource(),
            exit_policy=exit_policy_from_document(lock_document(LOCKS)),
        )
