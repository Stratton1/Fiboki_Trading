#!/usr/bin/env python
"""Backtest cost per document-series, on synthetic bars: the K6 compute basis.

Usage::

    python scripts/k6_engine_benchmark.py --bars 6000 31200 --docs 8
    python scripts/k6_engine_benchmark.py --bars 31200 --docs 8 --reference   # kernel off
    python scripts/k6_engine_benchmark.py --out bench.json ...

One "document-series" is one :class:`~fiboki.validation.engine_evaluator.EngineEvaluator`
call: a document's DEFAULT binding over the whole frame on one instrument, the
unit K6's screen pays 16 times per document per timeframe (one per sleeve). The
documents are a seeded sample of K5's generated roster plus two seeds, so the
mix of families is the one a campaign sees. Bars are synthetic (fat-tailed
returns, EURUSD-like level and wicks) because this machine holds no market
data; the timing depends on the bar count and the trade count, not on whether
the prices are real, but a real series with more or fewer trades will differ.

``--reference`` turns the signal kernel off (``generate_signal`` per bar). It
cannot turn the realised-volatility fast path off; for a true BEFORE figure run
this script against a checkout of the commit before the engine change (the K6
README records how it was measured). Every run prints each document's ledger
sha256, so a before/after pair can be checked for identical results as well as
compared for time.

Not evidence of anything about markets. A timing instrument.
"""
from __future__ import annotations

import argparse
import json
import platform
import random
import statistics
import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]


def synthetic_h4(n: int, seed: int) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    close = 1.2 * np.exp(np.cumsum(rng.standard_t(4, n) * 0.0025))
    wick = np.abs(rng.normal(0, 0.0015, n)) * close
    open_ = np.concatenate([[close[0]], close[:-1]])
    high = np.maximum.reduce([close + wick, open_, close])
    low = np.minimum.reduce([close - wick, open_, close])
    index = pd.date_range("2006-01-02", periods=n, freq="4h", tz="UTC")
    return pd.DataFrame({"open": open_, "high": high, "low": low, "close": close}, index=index)


def sample_documents(k: int, seed: int) -> list[object]:
    from fiboki.strategy.dsl import StrategyDocument

    gen = sorted(p for p in (ROOT / "research" / "generated").glob("*.json")
                 if p.name != "MANIFEST.json")
    seeds = [ROOT / "research" / "strategies" / f"{s}.json"
             for s in ("donchian_breakout_atr", "ichimoku_kumo_trend")]
    rng = random.Random(seed)
    chosen = rng.sample(gen, max(0, k - len(seeds))) + [s for s in seeds if s.exists()]
    docs = [StrategyDocument.from_json(p.read_text(encoding="utf-8")) for p in chosen]
    return [d for d in docs if "EURUSD" in d.universe and any(t.value == "H4" for t in d.timeframes)]


def k6_screen_timing(n_docs: int, n_sleeves: int, n_bars: int, seed: int) -> dict[str, object]:
    """Seconds per document of K6's screen on synthetic USD-quoted sleeves."""
    from dataclasses import dataclass

    sys.path.insert(0, str(ROOT / "scripts"))
    sys.path.insert(0, str(ROOT / "research"))
    import campaign_k6 as k6
    import generate_families_k6 as gen

    from fiboki.core.enums import Timeframe
    from fiboki.core.money import IdentityFxSource
    from fiboki.validation.engine_evaluator import EngineEvaluator, EvaluatorConfig

    @dataclass
    class Series:
        instrument: str
        timeframe: Timeframe
        frame: pd.DataFrame
        dataset_version_id: str

    # USD-quoted instruments (identity FX into a USD account), at roughly their own
    # price levels so per-asset minimum stops and sizing behave as they would.
    levels = {"AUDUSD": 0.7, "EURUSD": 1.2, "GBPUSD": 1.4, "NZDUSD": 0.65, "XAUUSD": 1800.0,
              "XAGUSD": 25.0, "US500": 4000.0, "BCOUSD": 75.0, "WTIUSD": 70.0, "US100": 14000.0,
              "US30": 33000.0, "XPTUSD": 950.0, "XPDUSD": 1500.0, "CORNUSD": 5.0,
              "SUGARUSD": 0.2, "WHEATUSD": 6.0}
    symbols = tuple(levels)[:n_sleeves]
    series = [Series(s, Timeframe.H4, synthetic_h4(n_bars, seed + k) * (levels[s] / 1.2),
                     f"bench_{s}") for k, s in enumerate(symbols)]

    def make(document: object, s: Series, cache: object) -> EngineEvaluator:
        return EngineEvaluator(
            document=document, frame=s.frame, dataset_version_id=s.dataset_version_id,
            config=EvaluatorConfig(instrument=s.instrument, timeframe=Timeframe.H4,
                                   account_ccy="USD", initial_balance=100_000.0),
            fx=IdentityFxSource(), cache=cache)

    cells = gen.read_cells(gen.OUT)
    rows = []
    for index, cell in cells[:n_docs]:
        document = gen.materialise(index, cell, symbols)
        t0 = time.perf_counter()
        row = k6.screen_one(index, document, series, make)
        dt = time.perf_counter() - t0
        rows.append({"index": index, "seconds": round(dt, 3), "stage": row.get("stage"),
                     "passed": row.get("passed"), "signal_bound": row.get("signal_bound"),
                     "n_trades": row.get("n_trades")})
        print(f"K6 screen doc {index}: {dt:7.2f} s  {row.get('stage')}  "
              f"bound {row.get('signal_bound')}  trades {row.get('n_trades')}", flush=True)
    secs = [r["seconds"] for r in rows]
    return {"documents": len(rows), "sleeves": len(symbols), "bars": n_bars,
            "mean_s": round(statistics.mean(secs), 3), "median_s": round(statistics.median(secs), 3),
            "rows": rows}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--bars", type=int, nargs="+", default=[6000, 31200])
    ap.add_argument("--docs", type=int, default=8)
    ap.add_argument("--seed", type=int, default=20261010)
    ap.add_argument("--reference", action="store_true", help="signal kernel off")
    ap.add_argument("--out", type=Path, default=None)
    ap.add_argument("--k6-screen", type=int, default=0, metavar="N",
                    help="also time K6's screen (S0a + rung 0) on N generated documents over "
                         "--sleeves synthetic USD sleeves of the LAST --bars length")
    ap.add_argument("--sleeves", type=int, default=16)
    args = ap.parse_args(argv)

    from fiboki.core.enums import Timeframe
    from fiboki.core.money import IdentityFxSource
    from fiboki.validation import engine_evaluator as ee
    from fiboki.validation.evaluation import DateWindow

    has_kernel = hasattr(ee, "SIGNAL_KERNEL_ENABLED")
    if has_kernel:
        ee.SIGNAL_KERNEL_ENABLED = not args.reference
    docs = sample_documents(args.docs, args.seed)
    rows = []
    for n in args.bars:
        bars = synthetic_h4(n, args.seed)
        window = DateWindow("all", bars.index[0], bars.index[-1])
        for doc in docs:
            evaluator = ee.EngineEvaluator(
                document=doc, frame=bars, dataset_version_id=f"bench_{n}",
                config=ee.EvaluatorConfig(instrument="EURUSD", timeframe=Timeframe.H4,
                                          account_ccy="USD", initial_balance=100_000.0),
                fx=IdentityFxSource(),
            )
            t0 = time.perf_counter()
            result = evaluator(doc.default_values(), window)
            dt = time.perf_counter() - t0
            rows.append({"bars": n, "strategy_id": doc.strategy_id, "seconds": round(dt, 4),
                         "n_trades": result.n_trades, "ledger_sha256": result.meta["ledger_sha256"]})
            print(f"{n:>6} bars  {dt:8.3f} s  {result.n_trades:>5} trades  {doc.strategy_id}",
                  flush=True)
    summary = {}
    for n in args.bars:
        secs = [r["seconds"] for r in rows if r["bars"] == n]
        summary[str(n)] = {"docs": len(secs), "mean_s": round(statistics.mean(secs), 4),
                           "median_s": round(statistics.median(secs), 4),
                           "max_s": round(max(secs), 4),
                           "us_per_bar_mean": round(1e6 * statistics.mean(secs) / n, 2)}
    screen = k6_screen_timing(args.k6_screen, args.sleeves, args.bars[-1], args.seed) \
        if args.k6_screen else None
    out = {
        "k6_screen": screen,
        "mode": ("reference (kernel off)" if args.reference else "kernel on")
        if has_kernel else "pre-kernel checkout",
        "python": sys.version.split()[0], "machine": platform.machine(),
        "platform": platform.platform(), "summary": summary, "runs": rows,
    }
    print(json.dumps(summary, indent=2))
    if args.out is not None:
        args.out.write_text(json.dumps(out, indent=2) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
