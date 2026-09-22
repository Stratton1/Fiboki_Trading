"""Run one evaluation in a FRESH interpreter and print its fingerprint as JSON.

Invoked by ``tests/unit/test_engine_evaluator.py`` to prove that an evaluation is
reproducible across processes and not merely within one. Determinism that holds
only inside a single interpreter would not survive a campaign spread over
workers and days -- and the cache, which reuses results between those runs, is
only safe if it does.
"""
from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tests"))

from synthetic_prices import synthetic_ohlcv  # noqa: E402

from fiboki.core.enums import Timeframe  # noqa: E402
from fiboki.core.money import IdentityFxSource  # noqa: E402
from fiboki.strategy.dsl import StrategyDocument  # noqa: E402
from fiboki.validation.engine_evaluator import (  # noqa: E402
    EngineEvaluator,
    EvaluatorConfig,
)
from fiboki.validation.evaluation import DateWindow  # noqa: E402


def main(start: str, end: str) -> None:
    bars = synthetic_ohlcv(3600, with_volume=False) * 1000.0
    document = StrategyDocument.from_json(
        (ROOT / "research" / "strategies" / "donchian_breakout_atr.json").read_text()
    )
    evaluator = EngineEvaluator(
        document=document,
        frame=bars,
        dataset_version_id="synthetic_xauusd_h4_v1",
        config=EvaluatorConfig(
            instrument="XAUUSD",
            timeframe=Timeframe.H4,
            account_ccy="USD",
            initial_balance=100_000.0,
        ),
        fx=IdentityFxSource(),
    )
    result = evaluator(document.default_values(), DateWindow("probe", start, end))
    print(
        json.dumps(
            {
                "ledger_sha256": result.meta["ledger_sha256"],
                "strategy_content_hash": result.meta["strategy_content_hash"],
                "engine_config_hash": result.meta["engine_config_hash"],
                "n_trades": result.n_trades,
                "net_profit": result.net_profit,
                "returns_sha256": hashlib.sha256(
                    np.ascontiguousarray(result.returns, dtype=float).tobytes()
                ).hexdigest(),
            },
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])
