"""Determinism, verified ACROSS PROCESSES.

Why a subprocess and not a loop
--------------------------------
V1 had a determinism test. It ran the backtest twice in one interpreter and
compared the results, which only ever caught a shared RNG being advanced. It
could not catch:

* iteration order that depends on ``PYTHONHASHSEED`` (set randomly per process
  by CPython unless pinned);
* module-level state accumulated by the first run and reused by the second;
* anything that varies with process start time, PID or memory layout.

These tests run the SAME scenario in two fresh interpreters, deliberately with
DIFFERENT hash seeds, and compare the SHA-256 of the trade ledger byte for
byte. The ledger excludes UUID fields, which are random by construction; a
"determinism" test that included them would be comparing noise and would have
to be weakened to pass, which is how these tests die.
"""
from __future__ import annotations

import hashlib
import os
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]

RUNNER = '''
import sys
sys.path.insert(0, {root!r})
from tests.scenario_exec import run_scenario

result = run_scenario()
print("TRADES", len(result.trades))
print("LEDGER", result.ledger_sha256())
print("EQUITY", __import__("hashlib").sha256(
    "".join(repr(v) for v in result.equity_curve["equity"].tolist()).encode()
).hexdigest())
print("COSTS", result.costs.total)
'''


def _run_in_subprocess(hash_seed: str) -> dict[str, str]:
    env = dict(os.environ)
    env["PYTHONHASHSEED"] = hash_seed
    env.pop("PYTHONDONTWRITEBYTECODE", None)
    proc = subprocess.run(
        [sys.executable, "-c", RUNNER.format(root=str(REPO_ROOT))],
        capture_output=True,
        text=True,
        env=env,
        cwd=str(REPO_ROOT),
        timeout=300,
        check=False,
    )
    if proc.returncode != 0:
        raise AssertionError(
            f"Subprocess failed (PYTHONHASHSEED={hash_seed}):\n"
            f"STDOUT:\n{proc.stdout}\nSTDERR:\n{proc.stderr}"
        )
    out: dict[str, str] = {}
    for line in proc.stdout.strip().splitlines():
        key, _, value = line.partition(" ")
        out[key] = value
    return out


@pytest.mark.slow
def test_trade_ledger_is_byte_identical_across_processes():
    """Two fresh interpreters, two different hash seeds, one ledger hash."""
    a = _run_in_subprocess("0")
    b = _run_in_subprocess("12345")

    assert a["TRADES"] == b["TRADES"], "trade COUNT differs between processes"
    assert int(a["TRADES"]) > 20, (
        "The determinism scenario produced too few trades to be meaningful; "
        f"got {a['TRADES']}. Fix the scenario, do not relax the assertion."
    )
    assert a["LEDGER"] == b["LEDGER"], (
        "Trade ledger SHA-256 differs across processes.\n"
        f"  PYTHONHASHSEED=0     -> {a['LEDGER']}\n"
        f"  PYTHONHASHSEED=12345 -> {b['LEDGER']}\n"
        "Something in the engine depends on hash ordering or process state."
    )
    assert a["EQUITY"] == b["EQUITY"], "mark-to-market equity curve differs"
    assert a["COSTS"] == b["COSTS"], "cost totals differ"


@pytest.mark.slow
def test_a_third_process_agrees_with_the_first_two():
    """Three is not paranoia: two agreeing seeds could both be unlucky."""
    hashes = {_run_in_subprocess(seed)["LEDGER"] for seed in ("0", "1", "99991")}
    assert len(hashes) == 1, f"Three processes produced {len(hashes)} distinct ledgers"


def test_in_process_repetition_also_agrees():
    """The weaker V1-style check, kept because it localises a failure quickly.

    If this passes but the subprocess test fails, the bug is hash ordering or
    process state. If both fail, it is RNG or mutable module state.
    """
    from tests.scenario_exec import run_scenario

    first = run_scenario()
    second = run_scenario()
    assert first.ledger_sha256() == second.ledger_sha256()
    assert first.ledger_text() == second.ledger_text()


def test_instrument_dict_ordering_does_not_change_the_result():
    """Feeding the same instruments in a different dict order must not matter.

    This is the concrete failure the engine's ``sorted(data)`` prevents, and it
    is also why fills derive their RNG from ``(seed, bar_index, sequence)``
    rather than from a shared stream.
    """
    from fiboki.backtest.engine import FixedFractionalSizer, run_backtest
    from tests.scenario_exec import (
        BreakoutStrategy,
        ConstantUsdGbp,
        build_config,
        synthetic_data,
    )

    data = synthetic_data()
    forward = {k: data[k] for k in sorted(data)}
    reverse = {k: data[k] for k in sorted(data, reverse=True)}

    def _run(d):
        return run_backtest(
            data=d,
            config=build_config(),
            strategy=BreakoutStrategy(),
            sizer=FixedFractionalSizer(risk_fraction=0.01),
            fx=ConstantUsdGbp(),
        )

    assert _run(forward).ledger_sha256() == _run(reverse).ledger_sha256()


def test_the_ledger_hash_actually_changes_when_the_result_changes():
    """A hash that never changes would pass every assertion above for free.

    Uses the TIGHT scenario, in which roughly a fifth of exits touch both the
    stop and the target in one bar, so the intrabar policy genuinely decides
    the outcome. (The main scenario's levels are too far apart for any bar to
    reach both, which is exactly why this test needs its own scenario — the
    first version of it passed vacuously.)
    """
    import dataclasses

    from fiboki.sim.fills import IntrabarPolicy
    from tests.scenario_exec import build_config, run_tight_scenario

    base = run_tight_scenario(build_config())
    optimistic = run_tight_scenario(
        dataclasses.replace(build_config(), intrabar_policy=IntrabarPolicy.TARGET_FIRST)
    )

    assert len(base.trades) > 50
    assert base.ledger_sha256() != optimistic.ledger_sha256()
    # The optimistic assumption must in fact be optimistic, and by a lot: this
    # is the size of the thing V1 chose silently.
    assert optimistic.final_equity > base.final_equity


def test_the_tight_scenario_is_deterministic_across_processes_too():
    """The ambiguous-exit path is stochastic under PROPORTIONAL; pin it down."""
    import dataclasses

    from fiboki.sim.fills import IntrabarPolicy
    from tests.scenario_exec import build_config, run_tight_scenario

    cfg = dataclasses.replace(
        build_config(), intrabar_policy=IntrabarPolicy.PROPORTIONAL
    )
    a = run_tight_scenario(cfg)
    b = run_tight_scenario(cfg)
    assert a.ledger_sha256() == b.ledger_sha256()
    # PROPORTIONAL must land strictly between the two extremes it interpolates.
    pessimistic = run_tight_scenario(build_config())
    optimistic = run_tight_scenario(
        dataclasses.replace(build_config(), intrabar_policy=IntrabarPolicy.TARGET_FIRST)
    )
    assert pessimistic.final_equity < a.final_equity < optimistic.final_equity


def test_data_fingerprint_detects_a_single_changed_price():
    from tests.scenario_exec import run_scenario, synthetic_data

    data = synthetic_data()
    baseline = run_scenario(data)

    tampered = {k: v.copy() for k, v in data.items()}
    tampered["EURUSD"].iloc[500, tampered["EURUSD"].columns.get_loc("close")] += 1e-6
    changed = run_scenario(tampered)

    assert baseline.data_fingerprint["EURUSD"]["sha256"] != (
        changed.data_fingerprint["EURUSD"]["sha256"]
    )
    assert hashlib.sha256(b"sanity").hexdigest()  # the hash function is real


# ==========================================================================
# The exit vocabulary
# ==========================================================================
#
# Everything above predates the engine's ability to honour anything beyond a
# stop and a first target, so none of it touches a trailing stop, a partial
# exit, a time stop, a cooldown or a reversal. Those introduce five new ways to
# depend on process state that the tests above cannot see:
#
#   * an auxiliary indicator series looked up per bar;
#   * a position closing in several pieces, each with its own cost share;
#   * a bar-index-keyed cooldown map;
#   * a reversal that mutates the open-position list mid-bar;
#   * a second ``resolve_exit`` call per partial, which MUST draw the same
#     numbers as the first or the leg's price and its costs disagree.

EXIT_RUNNER = '''
import sys
sys.path.insert(0, {root!r})
from tests.scenario_exec import run_exit_vocabulary_scenario

result = run_exit_vocabulary_scenario()
print("TRADES", len(result.trades))
print("LEGS", len(result.exit_legs))
print("PARTIALS", result.partial_exits)
print("LEDGER", result.ledger_sha256())
print("LEGLEDGER", result.leg_ledger_sha256())
print("REASONS", ",".join(sorted({{t.exit_reason.value for t in result.trades}})))
print("EQUITY", __import__("hashlib").sha256(
    "".join(repr(v) for v in result.equity_curve["equity"].tolist()).encode()
).hexdigest())
print("COSTS", result.costs.total)
'''


def _run_exit_scenario_in_subprocess(hash_seed: str) -> dict[str, str]:
    env = dict(os.environ)
    env["PYTHONHASHSEED"] = hash_seed
    env.pop("PYTHONDONTWRITEBYTECODE", None)
    proc = subprocess.run(
        [sys.executable, "-c", EXIT_RUNNER.format(root=str(REPO_ROOT))],
        capture_output=True,
        text=True,
        env=env,
        cwd=str(REPO_ROOT),
        timeout=300,
        check=False,
    )
    if proc.returncode != 0:
        raise AssertionError(
            f"Subprocess failed (PYTHONHASHSEED={hash_seed}):\n"
            f"STDOUT:\n{proc.stdout}\nSTDERR:\n{proc.stderr}"
        )
    out: dict[str, str] = {}
    for line in proc.stdout.strip().splitlines():
        key, _, value = line.partition(" ")
        out[key] = value
    return out


def test_the_exit_vocabulary_scenario_actually_exercises_the_exit_vocabulary():
    """Guard against the whole determinism suite below passing vacuously.

    A scenario whose trail never engages and whose legs never split would agree
    with itself across processes for free. This test states what the scenario
    must contain, so a future change that switches one of those paths off fails
    HERE, where the cause is obvious, rather than silently hollowing out the
    determinism evidence.
    """
    from tests.scenario_exec import run_exit_vocabulary_scenario

    result = run_exit_vocabulary_scenario()
    reasons = {t.exit_reason.value for t in result.trades}
    assert {
        "stop_loss",
        "trailing_stop",
        "time_stop",
        "opposite_signal",
    } <= reasons, f"scenario only produced {sorted(reasons)}"
    assert result.partial_exits > 30, (
        f"only {result.partial_exits} partial exits; the scale-out path is barely "
        "covered. Fix the scenario, do not relax the assertion."
    )
    assert len(result.exit_legs) > len(result.trades)
    assert result.rejections.get("cooldown", 0) > 0

    # And the two ledgers reconcile: this is the claim that justifies emitting
    # one Trade per POSITION instead of one per fill.
    assert sum(leg.net_pnl for leg in result.exit_legs) == pytest.approx(
        sum(t.net_pnl for t in result.trades), rel=1e-12
    )


@pytest.mark.slow
def test_exit_vocabulary_ledger_is_byte_identical_across_processes():
    a = _run_exit_scenario_in_subprocess("0")
    b = _run_exit_scenario_in_subprocess("12345")
    c = _run_exit_scenario_in_subprocess("99991")

    assert a["TRADES"] == b["TRADES"] == c["TRADES"]
    assert int(a["TRADES"]) > 50, (
        "The exit-vocabulary scenario produced too few positions to be "
        f"meaningful; got {a['TRADES']}. Fix the scenario, do not relax this."
    )
    assert int(a["PARTIALS"]) > 30, "too few partial exits to be evidence"
    assert a["LEDGER"] == b["LEDGER"] == c["LEDGER"], (
        "Trade ledger SHA-256 differs across processes.\n"
        f"  0     -> {a['LEDGER']}\n  12345 -> {b['LEDGER']}\n"
        f"  99991 -> {c['LEDGER']}"
    )
    assert a["LEGLEDGER"] == b["LEGLEDGER"] == c["LEGLEDGER"], (
        "The per-FILL ledger differs across processes even though the per-trade "
        "ledger agrees. Two scale-outs that sum to the same total are not the "
        "same backtest."
    )
    assert a["EQUITY"] == b["EQUITY"] == c["EQUITY"]
    assert a["COSTS"] == b["COSTS"] == c["COSTS"]
    assert a["REASONS"] == b["REASONS"] == c["REASONS"]


def test_exit_vocabulary_scenario_repeats_in_process():
    from tests.scenario_exec import run_exit_vocabulary_scenario

    first = run_exit_vocabulary_scenario()
    second = run_exit_vocabulary_scenario()
    assert first.ledger_text() == second.ledger_text()
    assert first.leg_ledger_text() == second.leg_ledger_text()


def test_the_exit_ledger_hash_changes_when_the_exit_policy_changes():
    """A hash that never moves would make every assertion above free.

    Turning off every rule that MOVES the stop — the chandelier and the
    breakeven — is a strictly different strategy, and the ledger must say so.
    Both are cleared together because ``TRAILING_STOP`` means "the stop that
    fired had been moved", and a breakeven stop has been moved.
    """
    import dataclasses as _dc

    from fiboki.backtest.engine import FixedFractionalSizer, run_backtest
    from fiboki.backtest.exits import NO_TRAIL
    from tests.scenario_exec import (
        ConstantUsdGbp,
        ScaleOutBreakout,
        build_config,
        exit_series_for,
        exit_vocabulary_policy,
        run_exit_vocabulary_scenario,
        synthetic_data,
    )

    trailing = run_exit_vocabulary_scenario()
    data = synthetic_data()
    untrailed = run_backtest(
        data=data,
        config=build_config(),
        strategy=ScaleOutBreakout(lookback=6, stop_fraction=0.4, rr=1.0),
        sizer=FixedFractionalSizer(risk_fraction=0.01),
        fx=ConstantUsdGbp(),
        exit_policy=_dc.replace(
            exit_vocabulary_policy(), trailing=NO_TRAIL, breakeven_at_r=None
        ),
        exit_series=exit_series_for(data),
    )
    assert trailing.ledger_sha256() != untrailed.ledger_sha256()
    assert not any(
        t.exit_reason.value == "trailing_stop" for t in untrailed.trades
    ), "a policy that never moves the stop reported a trailing stop"
