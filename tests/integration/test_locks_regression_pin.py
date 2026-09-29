"""A document WITHOUT locks is byte-identical to before locks existed.

Adding an optional field to a pydantic model adds a key to every dump, and every
key this project derives from a strategy -- the content hash, the structural
hash, the serialised document, the exit-policy fingerprint -- is read off a dump.
Done carelessly, adding ``locks`` would have moved every stored key at once and
handed every strategy a second look at its holdout (see
``tests/unit/test_holdout_key_versions.py`` for why that is the worst failure
this platform can have).

So the values below were computed from the five seed documents BEFORE the
``locks`` field, the ``instrument_lock`` gateway check and the engine's lock
book existed, and this test demands them unchanged. The backtest half runs the
real engine on pinned synthetic bars and compares the ledger hash, which is only
equal when every fill, cost and exit reason agrees.

If an unrelated, deliberate engine change moves a ledger hash here, that change
invalidates stored results and bumps ``ENGINE_VERSION``; update the ledger pins
in the same commit and say so. The document pins (content, JSON, structure,
complexity) must never move without a DSL schema decision.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pandas as pd
import pytest
from synthetic_prices import synthetic_ohlcv

from fiboki.backtest.engine import BacktestConfig, FixedFractionalSizer, run_backtest
from fiboki.backtest.exits import exit_policy_from_document
from fiboki.core.enums import Provenance
from fiboki.core.instruments import get as get_instrument
from fiboki.core.money import IdentityFxSource
from fiboki.research.structure import structure_hash
from fiboki.strategy.compiler import compile_strategy
from fiboki.strategy.dsl import LOCKS_DECLARED_FEATURE, StrategyDocument

ROOT = Path(__file__).resolve().parents[2]
SEEDS = ROOT / "research" / "strategies"

#: Computed before the change, on the tree the change was made against.
#:
#: ``exit_policy_fp`` RE-PINNED DELIBERATELY for engine_v3_realism: the exit
#: policy fingerprint carries ``key_version = ENGINE_VERSION``
#: (``backtest/exits.py``), so bumping the engine generation moves it and
#: nothing else. The document pins (content hashes, JSON, structure,
#: complexity) did NOT move.
PINS: dict[str, dict[str, object]] = {
    "donchian_breakout_atr": {
        "template_content_hash": "8483154d0f1754b8841296b67e4b6152b8f095153b839e65b46f4d8c071d9031",
        "bound_content_hash": "66d6a844b96bf0c5b4fe164e537bced361290f6e093a7a128e694bf0ab3b52b9",
        "template_json_sha": "3d5a95494c645f14b0e16423eb42a1676894d63a1c36e984f102fa9ccc6d1e58",
        "bound_json_sha": "ff58290b2d7acfa14ea4c758b54468fbe812b52af4a88203348cfdc280327dc0",
        "structure_hash": "ea8d0371a473de2a15fb13b01a5f2f9a7be681a923ca1fde7b39e00fa9dba1fe",
        "complexity": 8.0,
        "exit_policy_fp": "3c73579bf2cefcbf265494b5b02ef48947a72008b4e680e43aa84e73331d1378",
    },
    "fib_golden_pocket_pullback": {
        "template_content_hash": "fd2a53ed94907ab418b6dd6ecc67a8a0fd49b20c378ca7236860838bb8e84df8",
        "bound_content_hash": "158d914a08b3c80b5d840683738dbe7c3ef0d74790c2d8fd61023e3bd95cd5b5",
        "template_json_sha": "6183e89e033f2ac502c9a6ac0697cd814ae916d5a8c93ae858cf3c03afe98108",
        "bound_json_sha": "03df806ee32e3a1aabea4f5f4d345145e82fd1a21558d917cbe79c9678d3c85f",
        "structure_hash": "4b480bd11a0ac3651f2dd16d832d26e3c8691e9262f551676ebcdb6bdafc439e",
        "complexity": 14.0,
        "exit_policy_fp": "5e45a155b8627de608b588ea24a50cadd3be1c46815f292bbaef6821b451464a",
    },
    "ichimoku_kumo_trend": {
        "template_content_hash": "75caa48f3c2043bc3e2ca07d3665dddf9cefb7644d5a2a7a69d6bf4759123248",
        "bound_content_hash": "22fd6a9510e43a079a8e71de7c2d8362e5d3b0e3e8fe80a96aab638fcdf319a5",
        "template_json_sha": "ff75ff6c66112875847713ae6d504c1d7322173a12772b9b90155f71f4aae8dc",
        "bound_json_sha": "f26e0192b58091458a64a4a8c3d2fea80fd3b4e6dc63eaea131d4aaafc4548d3",
        "structure_hash": "6f37891f231f781b6566b878a2fe9bb485ede65a220093beae4210e57763f3ea",
        "complexity": 13.5,
        "exit_policy_fp": "bd83e4f229fa4a1bdb7d80a901579fdcc1f4dba48881ef507b6d145744a6edad",
    },
    "macd_ema_trend_hybrid": {
        "template_content_hash": "78083597c61621ad885e5942de6986a133c72ee2bb0a37b3e1af9dee9e84c2d2",
        "bound_content_hash": "bed09990a35466acff9350b5f7c8bea3ff5b1733e458ec174672e924d9414765",
        "template_json_sha": "8fb9866910e4b345582c6ed76aba9be4a8f68a010d9434835ccc6cede59e8ab1",
        "bound_json_sha": "adcdf497d13bf4ca8c9a92a4e0d9a58ee4698a300f7e08b0ec8c209e719652cd",
        "structure_hash": "4985a06a17cdf3259439502bca3b49cf21551f5c44c0fc1240400956a097068d",
        "complexity": 14.0,
        "exit_policy_fp": "f1fcb2b2488ca47cfb496314f3dcd8b19b9b3dc9b6bd320cfe149554626e5f1e",
    },
    "rsi_band_mean_reversion": {
        "template_content_hash": "0a1f5a26c24d96c04586fe589899d4a786d45a0e9a9dd5289552c0bbfb1b6c20",
        "bound_content_hash": "d421e3fd8c126614f0178e8a0e834e780be52eba764744b114715ed2734b9ec8",
        "template_json_sha": "590555d3f66ba707f982ff26be58bc7f89e6a5f1b6e9feda950f54d37b5ff236",
        "bound_json_sha": "58d2afbad28401f5fdf6930b2fd45ee290da65c3ce3d628dc228987f72a9102c",
        "structure_hash": "4147815be090df81830350397980cd7489781c6f1bb89563de5397399cb02baf",
        "complexity": 13.0,
        "exit_policy_fp": "0bd4278af10011dec178b5a2dd44dc4354f345f5e2d2d5983f898ed1897dedb9",
    },
}

def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _load(strategy_id: str) -> StrategyDocument:
    return StrategyDocument.from_json((SEEDS / f"{strategy_id}.json").read_text())


@pytest.mark.parametrize("strategy_id", sorted(PINS))
def test_document_keys_are_unchanged(strategy_id: str) -> None:
    template = _load(strategy_id)
    bound = template.bind_defaults()
    pin = PINS[strategy_id]
    assert template.locks is None and bound.locks is None
    assert "locks" not in template.model_dump(mode="json")
    assert "locks" not in json.loads(bound.to_json())
    assert template.content_hash() == pin["template_content_hash"]
    assert bound.content_hash() == pin["bound_content_hash"]
    assert _sha(template.to_json()) == pin["template_json_sha"]
    assert _sha(bound.to_json()) == pin["bound_json_sha"]
    assert structure_hash(bound) == pin["structure_hash"]
    assert bound.complexity_score == pin["complexity"]
    fp = exit_policy_from_document(bound).fingerprint()
    assert "locks" not in fp
    assert _sha(json.dumps(fp, sort_keys=True)) == pin["exit_policy_fp"]


# --------------------------------------------------------------------------
# The engine half
# --------------------------------------------------------------------------


class _Runner:
    """Minimal document runner: compile once, ask for a signal on each bar."""

    def __init__(self, document: StrategyDocument, symbol: str, frame: pd.DataFrame) -> None:
        self.compiled = compile_strategy(document)
        self.symbol = symbol
        self.prepared = self.compiled.prepare(frame)
        self.features_seen: set[str] = set()

    def on_bar(self, ctx):
        j = ctx._cursor.get(self.symbol, -1)
        if j < 0:
            return ()
        signal = self.compiled.generate_signal(self.prepared, j, self.symbol, "H4")
        if signal is None:
            return ()
        self.features_seen.update(signal.features)
        return (signal,)


@pytest.fixture(scope="module")
def bars() -> pd.DataFrame:
    return synthetic_ohlcv(3200, with_volume=False) * 1000.0


def _run(strategy_id: str, bars: pd.DataFrame):
    doc = _load(strategy_id).bind_defaults()
    symbol = "XAUUSD" if "XAUUSD" in doc.universe else doc.universe[0]
    runner = _Runner(doc, symbol, bars)
    policy = exit_policy_from_document(doc)
    series = (
        {symbol: runner.prepared[list(policy.needs_series)]} if policy.needs_series else None
    )
    config = BacktestConfig(
        initial_balance=100_000.0,
        account_ccy=get_instrument(symbol).quote,
        max_concurrent=doc.position_management.max_concurrent_positions,
        max_per_instrument=doc.position_management.max_concurrent_positions,
        strategy_id="pin",
        provenance=Provenance.BACKTEST,
    )
    result = run_backtest(
        data={symbol: bars[["open", "high", "low", "close"]]},
        config=config,
        strategy=runner,
        sizer=FixedFractionalSizer(risk_fraction=0.01),
        fx=IdentityFxSource(),
        exit_policy=policy,
        exit_series=series,
    )
    return result, runner


#: Engine ledger pins, same provenance: ``(ledger_sha256, leg_ledger_sha256,
#: n_trades, rejections, signals_seen)``. Computed with THIS file's runner on
#: the pre-change tree and again on the changed one; the two were identical.
#:
#: The two hashes were RE-PINNED DELIBERATELY for engine_v3_realism
#: (backtest/version.py ENGINE_V3_REASONS): ``FixedFractionalSizer`` now
#: defaults to ``fixed_fractional_v2`` (spread + expected slippage in the risk
#: per unit, so sizes are smaller) and financing is charged on business-day
#: rollovers with the FX Wednesday triple. The trade COUNTS, the rejection
#: counts and ``signals_seen`` are unchanged for all five documents, which is
#: what shows the move is sizing and financing and not entries or exits. Re-run
#: under ``fixed_fractional_v1`` the hashes still differ from the old pins
#: (financing), so both changes contribute.
_LEDGER: dict[str, tuple[str, str, int, dict[str, int], int]] = {
    "donchian_breakout_atr": (
        "09e5ab35987d343e3884ba06734bd5d1795ccc8f60af10b2c82ac79f8147153a",
        "cceb31e71cdaa6d1193a046d5fb39c01af4f8f4cd4996d98c1c2b7f79946020d",
        106,
        {"cooldown": 2, "max_concurrent": 284},
        392,
    ),
    "fib_golden_pocket_pullback": (
        "4325aabf1a443c414a54ce05db32c5f6f2e763d0ec73caec34d95bbc53529c53",
        "17cd1ed9d47879700d119057b8a1e6bea55798cd163b8cabd96f603d32acb482",
        32,
        {"cooldown": 4, "max_concurrent": 24},
        60,
    ),
    "ichimoku_kumo_trend": (
        "93243358e5127fb8c106763771805dcfa608f9f68fcc1f9a719d22e3f91e2b20",
        "a0230387f492ef4c72f479ce9fad0a73774d08e0494f4224a9f6377e8b34f858",
        12,
        {},
        12,
    ),
    "macd_ema_trend_hybrid": (
        "5181b77b32a1a7fd93bd117fe57fe5d9d82e06ae259c6e2f6e4056904780997e",
        "9b6d2e318d28e48f94bc71f88a3575d4522fe99a1dfb04f426e086e1189ca2d7",
        5,
        {},
        5,
    ),
    "rsi_band_mean_reversion": (
        "4aa1b1e58118993e215c58d5d44a6fa66067e12e9f85374dcd90e8160bf3f128",
        "f58db72f08c2ee1ccc2c19bd77961f13975b5a15ee90ec36919122455e0b1116",
        16,
        {},
        16,
    ),
}


@pytest.mark.parametrize("strategy_id", sorted(PINS))
def test_the_engine_ledger_is_unchanged(strategy_id: str, bars: pd.DataFrame) -> None:
    result, runner = _run(strategy_id, bars)
    ledger, legs, n_trades, rejections, signals_seen = _LEDGER[strategy_id]
    assert result.ledger_sha256() == ledger
    assert result.leg_ledger_sha256() == legs
    assert len(result.trades) == n_trades
    assert result.rejections == rejections
    assert result.signals_seen == signals_seen
    assert result.lock_blocks == []
    assert "instrument_lock" not in result.rejections
    assert LOCKS_DECLARED_FEATURE not in runner.features_seen
