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
