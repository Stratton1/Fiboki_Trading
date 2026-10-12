"""The array signal kernel is the reference path, signal for signal.

``strategy/kernel.py`` replaces ``generate_signal``'s per-bar frame slice with
column arrays and a vectorised pre-filter. That is only admissible if NOTHING a
backtest reads changes. These tests compare the two paths on every bar, on the
six seed documents and the hundred K5 generated documents (each bound to its
declared defaults and to a seeded random grid point), on three seeded frames,
and then compare whole engine runs (ledger, leg ledger, allocation ledger and
per-bar returns) with the kernel on and off.

A mismatch here means the kernel is wrong. Do not loosen the comparison.
"""
from __future__ import annotations

import json
import random
import sys
from dataclasses import asdict
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tests"))

from synthetic_prices import choppy_ohlc, synthetic_ohlcv  # noqa: E402

from fiboki.core.enums import Timeframe  # noqa: E402
from fiboki.core.money import IdentityFxSource  # noqa: E402
from fiboki.strategy.compiler import compile_strategy  # noqa: E402
from fiboki.strategy.dsl import StrategyDocument  # noqa: E402
from fiboki.strategy.kernel import SignalKernel  # noqa: E402
from fiboki.validation import engine_evaluator as ee  # noqa: E402
from fiboki.validation.evaluation import DateWindow  # noqa: E402

SEEDS = sorted((ROOT / "research" / "strategies").glob("*.json"))
GENERATED = sorted(
    p for p in (ROOT / "research" / "generated").glob("*.json") if p.name != "MANIFEST.json"
)


def _documents() -> list[StrategyDocument]:
    out = []
    for path in [*SEEDS, *GENERATED]:
        try:
            out.append(StrategyDocument.from_json(path.read_text()))
        except Exception:  # pragma: no cover - a non-document json in the folder
            continue
    return out


DOCUMENTS = _documents()


def _fat_tailed(n: int, seed: int, freq: str = "4h") -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    close = 1.2 * np.exp(np.cumsum(rng.standard_t(4, n) * 0.0025))
    wick = np.abs(rng.normal(0, 0.0015, n)) * close
    open_ = np.concatenate([[close[0]], close[:-1]])
    high = np.maximum.reduce([close + wick, open_, close])
    low = np.minimum.reduce([close - wick, open_, close])
    index = pd.date_range("2015-01-05", periods=n, freq=freq, tz="UTC")
    return pd.DataFrame({"open": open_, "high": high, "low": low, "close": close}, index=index)


FRAMES = {
    "trend": lambda: synthetic_ohlcv(900, with_volume=False),
    "choppy_h1": lambda: choppy_ohlc(900),
    "fat_tailed": lambda: _fat_tailed(900, 20261010),
}


def _signal_view(sig: object) -> str | None:
    if sig is None:
        return None
    raw = asdict(sig)  # type: ignore[call-overload]
    raw.pop("signal_id", None)  # random by construction
    return json.dumps(raw, sort_keys=True, default=str)


def _bindings(doc: StrategyDocument, rng: random.Random) -> list[dict[str, object]]:
    out = [doc.default_values()]
    if doc.parameters:
        out.append({k: rng.choice(list(p.domain())) for k, p in doc.parameters.items()})
    return out


def _timeframe(doc: StrategyDocument) -> Timeframe:
    return doc.timeframes[0]


@pytest.mark.slow
@pytest.mark.timeout(900)
@pytest.mark.parametrize("frame_name", sorted(FRAMES))
def test_kernel_equals_generate_signal_on_every_bar(frame_name: str) -> None:
    frame = FRAMES[frame_name]()
    rng = random.Random(f"kernel-parity-{frame_name}")
    checked = signals = skipped = 0
    for doc in DOCUMENTS:
        for binding in _bindings(doc, rng):
            compiled = compile_strategy(doc.bind({**doc.default_values(), **binding}))
            prepared = compiled.prepare(frame)
            kernel = SignalKernel(compiled, prepared)
            symbol, tf = doc.universe[0], _timeframe(doc)
            for idx in range(len(prepared)):
                ref = compiled.generate_signal(prepared, idx, instrument=symbol, timeframe=tf)
                fast = kernel.signal_at(idx, instrument=symbol, timeframe=tf)
                assert _signal_view(fast) == _signal_view(ref), (
                    f"{doc.strategy_id} {binding} bar {idx} on {frame_name}"
                )
                # The pre-filter must be a SUPERSET of the signalling bars.
                if ref is not None:
                    assert kernel.candidates[idx], (doc.strategy_id, idx)
                    signals += 1
                checked += 1
            skipped += int((~kernel.candidates).sum())
    assert checked > 100_000
    assert signals > 1_000, "the fixture must make the documents trade"
    assert skipped > checked // 2, "the pre-filter should skip most bars"


def test_kernel_refuses_exactly_what_generate_signal_refuses() -> None:
    doc = DOCUMENTS[0]
    compiled = compile_strategy(doc.bind_defaults())
    prepared = compiled.prepare(synthetic_ohlcv(300, with_volume=False))
    kernel = SignalKernel(compiled, prepared)
    for bad in (-1, len(prepared)):
        with pytest.raises(IndexError):
            compiled.generate_signal(prepared, bad)
        with pytest.raises(IndexError):
            kernel.signal_at(bad)
    with pytest.raises(Exception) as ref_exc:
        compiled.generate_signal(prepared, 250, instrument="NOT_IN_UNIVERSE")
    with pytest.raises(type(ref_exc.value)):
        kernel.signal_at(250, instrument="NOT_IN_UNIVERSE")


def test_kernel_needs_a_prepared_utc_frame() -> None:
    doc = DOCUMENTS[0]
    compiled = compile_strategy(doc.bind_defaults())
    raw = synthetic_ohlcv(300, with_volume=False)
    with pytest.raises(ValueError):
        SignalKernel(compiled, raw)  # indicators not computed
    naive = compiled.prepare(raw)
    naive.index = naive.index.tz_localize(None)
    with pytest.raises(ValueError):
        SignalKernel(compiled, naive)


def _evaluate(doc: StrategyDocument, frame: pd.DataFrame, use_kernel: bool,
              monkeypatch: pytest.MonkeyPatch) -> dict[str, object]:
    monkeypatch.setattr(ee, "SIGNAL_KERNEL_ENABLED", use_kernel)
    evaluator = ee.EngineEvaluator(
        document=doc,
        frame=frame,
        dataset_version_id="kernel_parity_v1",
        config=ee.EvaluatorConfig(
            instrument="EURUSD",
            timeframe=Timeframe.H4,
            account_ccy="USD",
            initial_balance=100_000.0,
        ),
        fx=IdentityFxSource(),
    )
    window = DateWindow("all", frame.index[0], frame.index[-1])
    result = evaluator(doc.default_values(), window)
    return {
        "ledger": result.meta["ledger_sha256"],
        "legs": result.meta["leg_ledger_sha256"],
        "alloc": result.meta["allocation_ledger_sha256"],
        "signals": result.meta["signals_emitted"],
        "rejections": result.meta["rejections"],
        "n_trades": result.n_trades,
        "net": result.net_profit,
        "returns": np.ascontiguousarray(result.returns, dtype=float).tobytes(),
        "cache_key": result.meta["cache_key"],
    }


@pytest.mark.slow
@pytest.mark.timeout(900)
def test_engine_runs_are_byte_identical_with_and_without_the_kernel(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    frame = _fat_tailed(2500, 7)
    eligible = [d for d in DOCUMENTS
                if "EURUSD" in d.universe and Timeframe.H4 in d.timeframes]
    sample = random.Random("engine-parity").sample(eligible, 20)
    traded = 0
    for doc in sample:
        ref = _evaluate(doc, frame, False, monkeypatch)
        fast = _evaluate(doc, frame, True, monkeypatch)
        assert fast == ref, doc.strategy_id
        traded += int(ref["n_trades"] > 0)  # type: ignore[operator]
    assert traded >= 8


@pytest.mark.timeout(600)
def test_kernel_equals_generate_signal_on_k6_grammar_documents() -> None:
    """Forty seeded cells of the K6 grammar (every rule shape it can emit)."""
    sys.path.insert(0, str(ROOT / "research"))
    import generate_families_k6 as k6gen

    rng = random.Random("k6-kernel-parity")
    total = k6gen.grammar_size()
    cells = []
    while len(cells) < 40:
        cell = k6gen.cell_at(rng.randrange(total))
        if k6gen.exclusion(cell) is None:
            cells.append(cell)
    frame = _fat_tailed(700, 99)
    signals = 0
    for n, cell in enumerate(cells):
        doc = k6gen.build_document(n, cell)
        for binding in _bindings(doc, rng):
            compiled = compile_strategy(doc.bind({**doc.default_values(), **binding}))
            prepared = compiled.prepare(frame)
            kernel = SignalKernel(compiled, prepared)
            for idx in range(len(prepared)):
                ref = compiled.generate_signal(prepared, idx, timeframe=Timeframe.H4)
                fast = kernel.signal_at(idx, timeframe=Timeframe.H4)
                assert _signal_view(fast) == _signal_view(ref), (cell.code, binding, idx)
                signals += ref is not None
    assert signals > 200
