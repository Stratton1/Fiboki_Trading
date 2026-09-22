"""The concrete evaluator: the ladder, finally attached to the real engine.

``validation/evaluation.py`` shipped a protocol and no implementation of it,
because nothing could turn ``{"rsi_period": 21}`` into a runnable strategy.
These tests pin the properties the ladder actually depends on:

* a binding reaches the engine and CHANGES what it does;
* the returns are period-basis and aligned across parameterisations, which is the
  precondition for the trials matrix rungs 3 and 5 are built on;
* the numbers add up -- the return column sums to the reported net profit;
* every evaluation names the dataset version and engine configuration that
  produced it;
* the result is identical across runs, across processes, and from the cache.
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest
from synthetic_prices import synthetic_ohlcv

from fiboki.core.enums import Timeframe
from fiboki.core.money import IdentityFxSource
from fiboki.strategy.dsl import StrategyDocument
from fiboki.validation.engine_evaluator import (
    EngineEvaluator,
    EvaluationCache,
    EvaluatorConfig,
    frame_digest,
)
from fiboki.validation.evaluation import DateWindow, ParameterGrid

ROOT = Path(__file__).resolve().parents[2]
SEED_DIR = ROOT / "research" / "strategies"
DATASET = "synthetic_xauusd_h4_v1"


@pytest.fixture(scope="module")
def bars() -> pd.DataFrame:
    return synthetic_ohlcv(3600, with_volume=False) * 1000.0


@pytest.fixture(scope="module")
def document() -> StrategyDocument:
    return StrategyDocument.from_json(
        (SEED_DIR / "donchian_breakout_atr.json").read_text()
    )


def make_evaluator(document, bars, cache=None, **overrides) -> EngineEvaluator:
    settings = {
        "instrument": "XAUUSD",
        "timeframe": Timeframe.H4,
        "account_ccy": "USD",
        "initial_balance": 100_000.0,
        **overrides,
    }
    config = EvaluatorConfig(**settings)
    return EngineEvaluator(
        document=document,
        frame=bars,
        dataset_version_id=DATASET,
        config=config,
        fx=IdentityFxSource(),
        cache=cache,
    )


@pytest.fixture(scope="module")
def window(bars: pd.DataFrame) -> DateWindow:
    return DateWindow("probe", bars.index[900], bars.index[-1])


@pytest.fixture(scope="module")
def evaluator(document, bars) -> EngineEvaluator:
    return make_evaluator(document, bars)


# ------------------------------------------------------------- the basics


def test_an_evaluation_runs_the_engine_and_returns_period_returns(
    evaluator, window
) -> None:
    result = evaluator(evaluator.document.default_values(), window)
    assert result.returns_basis == "period"
    assert result.returns.size > 100
    assert result.trades, "the probe strategy must trade or this suite tests nothing"
    assert result.n_trades == len(result.trades)


def test_the_return_column_sums_to_the_reported_net_profit(evaluator, window) -> None:
    """A reader must be able to add the column up and get the headline number."""
    result = evaluator(evaluator.document.default_values(), window)
    assert float(result.returns.sum()) == pytest.approx(result.net_profit, abs=1e-6)


def test_the_returns_calendar_is_the_window_not_the_prologue(
    evaluator, window, bars
) -> None:
    result = evaluator(evaluator.document.default_values(), window)
    expected = int(
        ((bars.index >= window.start) & (bars.index < window.end)).sum()
    )
    assert result.returns.size == expected


def test_every_trade_was_entered_inside_the_window(evaluator, window) -> None:
    """The prologue exists to warm indicators, not to let a fold trade early."""
    result = evaluator(evaluator.document.default_values(), window)
    assert all(window.contains(t.entry_time) for t in result.trades)


# ----------------------------------------------------------- provenance


def test_every_evaluation_records_its_dataset_version_and_engine_config(
    evaluator, window
) -> None:
    result = evaluator(evaluator.document.default_values(), window)
    assert result.meta["dataset_version_id"] == DATASET
    assert result.meta["frame_digest"] == frame_digest(evaluator.frame)
    assert result.meta["engine_config_hash"] == evaluator.engine_config_hash()
    assert result.meta["engine_config"]["profile"]["name"]
    assert result.summary()["dataset_version_id"] == DATASET


def test_every_evaluation_names_the_exact_bound_strategy_it_ran(
    evaluator, window
) -> None:
    params = {**evaluator.document.default_values(), "channel_period": 40}
    result = evaluator(params, window)
    bound = evaluator.document.bind(params)
    assert result.meta["strategy_content_hash"] == bound.content_hash()
    assert result.meta["template_content_hash"] == evaluator.document.content_hash()
    assert result.meta["binding"]["channel_period"] == 40


def test_a_different_engine_configuration_is_a_different_fingerprint(
    document, bars
) -> None:
    a = make_evaluator(document, bars)
    b = make_evaluator(document, bars, profile_name="OANDA_REALISTIC")
    assert a.engine_config_hash() != b.engine_config_hash()


# -------------------------------------------------- the binding bites


def test_different_bindings_produce_different_trades(evaluator, window) -> None:
    """The whole point. If this fails, the sweep is measuring nothing."""
    base = evaluator.document.default_values()
    slow = evaluator({**base, "channel_period": 10}, window)
    fast = evaluator({**base, "channel_period": 60}, window)
    assert slow.meta["ledger_sha256"] != fast.meta["ledger_sha256"]
    assert (slow.n_trades, slow.net_profit) != (fast.n_trades, fast.net_profit)


def test_the_bindings_are_aligned_so_a_trials_matrix_can_be_assembled(
    evaluator, window
) -> None:
    base = evaluator.document.default_values()
    sweep = [
        evaluator({**base, "channel_period": p}, window) for p in (10, 20, 40, 60)
    ]
    assert len({e.returns.size for e in sweep}) == 1
    matrix = np.column_stack([e.returns for e in sweep])
    assert matrix.shape[1] == 4
    assert np.isfinite(matrix).all()


def test_an_unswept_parameter_takes_the_declared_default_and_says_so(
    evaluator, window
) -> None:
    result = evaluator({"channel_period": 30}, window)
    binding = result.meta["binding"]
    assert binding["channel_period"] == 30
    assert binding["trend_ema"] == evaluator.document.parameters["trend_ema"].default
    assert set(binding) == set(evaluator.document.parameters)


# ------------------------------------------------------------ determinism


def test_two_calls_give_byte_identical_ledgers(document, bars, window) -> None:
    a = make_evaluator(document, bars)
    b = make_evaluator(document, bars)
    params = document.default_values()
    first = a(params, window)
    second = b(params, window)
    assert first.meta["ledger_sha256"] == second.meta["ledger_sha256"]
    assert np.array_equal(first.returns, second.returns)


def test_the_result_is_identical_in_a_FRESH_PROCESS(document, bars, window) -> None:
    """Determinism that holds only inside one interpreter is not determinism.

    A campaign runs across workers and across days; an evaluation that depends on
    process state would make the cache -- and the walk-forward selection keyed on
    it -- silently non-reproducible.
    """
    local = make_evaluator(document, bars)
    result = local(document.default_values(), window)

    script = ROOT / "tests" / "engine_evaluator_probe.py"
    completed = subprocess.run(
        [sys.executable, str(script), window.start.isoformat(), window.end.isoformat()],
        capture_output=True,
        text=True,
        cwd=str(ROOT),
        check=True,
    )
    remote = json.loads(completed.stdout.strip().splitlines()[-1])
    assert remote["ledger_sha256"] == result.meta["ledger_sha256"]
    assert remote["net_profit"] == pytest.approx(result.net_profit, abs=1e-9)
    assert remote["n_trades"] == result.n_trades
    assert remote["returns_sha256"] == _returns_digest(result.returns)
    assert remote["strategy_content_hash"] == result.meta["strategy_content_hash"]


def _returns_digest(returns: np.ndarray) -> str:
    import hashlib

    return hashlib.sha256(np.ascontiguousarray(returns, dtype=float).tobytes()).hexdigest()


# ----------------------------------------------------------------- cache


def test_a_cached_run_equals_an_uncached_one(document, bars, window, tmp_path) -> None:
    plain = make_evaluator(document, bars)
    cached = make_evaluator(document, bars, cache=EvaluationCache(tmp_path / "cache"))
    params = {**document.default_values(), "channel_period": 40}

    expected = plain(params, window)
    cold = cached(params, window)
    warm = cached(params, window)

    for got in (cold, warm):
        assert got.n_trades == expected.n_trades
        assert got.net_profit == pytest.approx(expected.net_profit, abs=1e-9)
        assert np.array_equal(got.returns, expected.returns)
        assert got.meta["ledger_sha256"] == expected.meta["ledger_sha256"]
        assert [t.net_pnl for t in got.trades] == [t.net_pnl for t in expected.trades]
        assert [t.exit_reason for t in got.trades] == [
            t.exit_reason for t in expected.trades
        ]
    assert cached.n_engine_runs == 1, "the warm call re-ran the engine"
    assert cached.cache.stats()["hits"] == 1


def test_the_cache_survives_a_new_evaluator_on_the_same_directory(
    document, bars, window, tmp_path
) -> None:
    directory = tmp_path / "campaign"
    params = document.default_values()
    first = make_evaluator(document, bars, cache=EvaluationCache(directory))
    expected = first(params, window)

    second = make_evaluator(document, bars, cache=EvaluationCache(directory))
    reused = second(params, window)
    assert second.n_engine_runs == 0
    assert reused.meta["ledger_sha256"] == expected.meta["ledger_sha256"]


def test_the_cache_key_separates_everything_that_can_change_the_answer(
    document, bars, window, tmp_path
) -> None:
    base = make_evaluator(document, bars)
    params = document.default_values()
    key = base.cache_key(params, window)

    other_binding = base.cache_key({**params, "channel_period": 40}, window)
    other_window = base.cache_key(
        params, DateWindow("other", window.start, window.end - pd.Timedelta(days=30))
    )
    other_profile = make_evaluator(
        document, bars, profile_name="SEVERE_STRESS"
    ).cache_key(params, window)
    other_bars = EngineEvaluator(
        document=document,
        frame=bars * 1.01,
        dataset_version_id=DATASET,
        config=base.config,
        fx=IdentityFxSource(),
    ).cache_key(params, window)

    assert len({key, other_binding, other_window, other_profile, other_bars}) == 5


def test_the_window_NAME_is_not_part_of_the_cache_key(document, bars, window) -> None:
    """The same span asked twice is the same question, whatever it is called."""
    base = make_evaluator(document, bars)
    renamed = DateWindow("wf_train_3", window.start, window.end)
    assert base.cache_key(document.default_values(), window) == base.cache_key(
        document.default_values(), renamed
    )


def test_a_cache_hit_still_carries_the_window_it_was_asked_about(
    document, bars, window, tmp_path
) -> None:
    ev = make_evaluator(document, bars, cache=EvaluationCache(tmp_path / "c"))
    params = document.default_values()
    ev(params, window)
    renamed = DateWindow("wf_test_0", window.start, window.end)
    again = ev(params, renamed)
    assert again.window.name == "wf_test_0"
    assert ev.n_engine_runs == 1


# ------------------------------------------------------------ sweep/select


def test_a_sweep_evaluates_every_grid_point_in_grid_order(evaluator, window) -> None:
    grid = ParameterGrid.from_axes({"channel_period": (10, 20, 40)})
    results = evaluator.sweep(grid, window)
    assert [r.params["channel_period"] for r in results] == [10, 20, 40]


def test_select_breaks_ties_towards_the_first_grid_point(evaluator, window) -> None:
    grid = ParameterGrid.from_axes({"channel_period": (10, 20, 40)})
    results = evaluator.sweep(grid, window)
    chosen = evaluator.select(results, "net_profit")
    best = max(r.net_profit for r in results)
    assert results[chosen].net_profit == best
    assert chosen == min(i for i, r in enumerate(results) if r.net_profit == best)


def test_fit_and_transfer_runs_the_SELECTED_parameters_on_the_test_window(
    document, bars
) -> None:
    ev = make_evaluator(document, bars)
    train = DateWindow("train", bars.index[600], bars.index[2200])
    test = DateWindow("test", bars.index[2200], bars.index[-1])
    grid = ParameterGrid.from_axes({"channel_period": (10, 20, 40, 60)})
    outcome = ev.fit_and_transfer(grid, train, test, metric="net_profit")

    selected = outcome["selected_params"]
    assert outcome["out_of_sample"].params == selected
    assert outcome["out_of_sample"].window.name == "test"
    assert outcome["n_trials"] == 4
    # Proved from the evaluator's own call log, not from the returned summary.
    from fiboki.validation.evaluation import params_key

    assert (params_key(selected), "test") in ev.calls


def test_fit_and_transfer_refuses_overlapping_windows(document, bars) -> None:
    ev = make_evaluator(document, bars)
    train = DateWindow("train", bars.index[600], bars.index[2400])
    test = DateWindow("test", bars.index[2200], bars.index[-1])
    with pytest.raises(ValueError, match="overlaps"):
        ev.fit_and_transfer(ParameterGrid.from_axes({"channel_period": (10,)}), train, test)


# --------------------------------------------------------------- refusals


def test_an_instrument_outside_the_universe_is_refused(document, bars) -> None:
    with pytest.raises(ValueError, match="not in the document's universe"):
        make_evaluator(document, bars, instrument="EURGBP")


def test_a_timeframe_the_document_forbids_is_refused(document, bars) -> None:
    with pytest.raises(ValueError, match="not a permitted timeframe"):
        make_evaluator(document, bars, timeframe=Timeframe.M5)


def test_a_dataset_version_is_mandatory(document, bars) -> None:
    with pytest.raises(ValueError, match="dataset_version_id is mandatory"):
        EngineEvaluator(
            document=document,
            frame=bars,
            dataset_version_id="",
            config=EvaluatorConfig(instrument="XAUUSD", timeframe=Timeframe.H4),
            fx=IdentityFxSource(),
        )


def test_a_window_too_short_to_mean_anything_is_refused(evaluator, bars) -> None:
    tiny = DateWindow("tiny", bars.index[1000], bars.index[1005])
    with pytest.raises(ValueError, match="below the"):
        evaluator(evaluator.document.default_values(), tiny)


def test_the_candidate_is_keyed_on_the_default_BINDING_not_the_template(
    evaluator,
) -> None:
    """Two bindings must not share -- and therefore burn -- one holdout look."""
    candidate = evaluator.candidate(max_points=16)
    assert candidate.content_hash == evaluator.document.bind_defaults().content_hash()
    assert candidate.content_hash != evaluator.document.content_hash()
    assert set(candidate.default_params) == set(evaluator.document.parameters)
