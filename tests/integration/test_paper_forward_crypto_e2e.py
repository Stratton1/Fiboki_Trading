"""Paper forward on SPOT CRYPTO, end to end, with the network faked (no request leaves).

The whole composition from ``compose_runtime`` runs on a wiring in the
committed crypto format (``venue.market_data = coinbase_public``): the
Coinbase candle provider behind the polling feed, the public-ticker spread
source, the risk gateway with its durable kill switch, the execution service,
the paper venue and the position manager over the shared book. The one
substitution is the transport, which answers the two public endpoints from a
synthetic BTC-GBP series.

What it pins:

* the feed polls on a SATURDAY: the crypto calendar is 24/7, so no bar is
  "market closed" and the gateway's ``market_state`` passes;
* a LONG breakout is sized once, passes every gateway check (including
  ``direction_permitted`` and ``abnormal_spread`` against the live quoted
  spread in bps of mid) and fills at the paper venue;
* a SHORT breakout is refused by the gateway as ``short_not_permitted`` and
  nothing is dealt: long-only lives in the risk layer, not the strategy;
* the commission the book charges is the profile's 60 bps per leg;
* the crypto session's journal is its own (``paper_forward_crypto`` ledger dir).
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlsplit

import numpy as np
import pandas as pd
import pytest

from fiboki.api.settings import load_settings
from fiboki.broker.oanda import HttpResponse
from fiboki.core.enums import Direction
from fiboki.obs.alerts import AlertDispatcher, MemoryChannel
from fiboki.research.universes import retarget
from fiboki.strategy.compiler import compile_strategy
from fiboki.strategy.dsl import StrategyDocument
from fiboki.workers.base import WorkerStore, reset_log_once

REPO = Path(__file__).resolve().parents[2]
SEED = REPO / "research" / "strategies" / "donchian_breakout_atr.json"
CRYPTO_WIRING = REPO / "src" / "fiboki" / "entrypoints" / "wiring" / "paper_forward_crypto_v1.json"
H1 = pd.Timedelta(hours=1)
#: A Saturday. FX is shut; crypto is not.
SATURDAY = pd.Timestamp("2026-10-24T09:00:00Z")


class FakeClock:
    def __init__(self, start: pd.Timestamp) -> None:
        self.now = start

    def __call__(self) -> pd.Timestamp:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.now = self.now + pd.Timedelta(seconds=float(seconds))

    def mono(self) -> float:
        return self.now.value / 1e9


class FakeCoinbase:
    """Answers ``/products/BTC-GBP/candles`` and ``/products/BTC-GBP/ticker`` from a frame.

    A candle is served only once its hour has CLOSED on the fake clock (the
    venue does publish the forming candle; the provider must drop it, and the
    rest of this suite checks that it does).
    """

    def __init__(self, frame: pd.DataFrame, clock: FakeClock, spread_bps: float = 1.0) -> None:
        self.frame = frame
        self.clock = clock
        self.spread_bps = spread_bps
        self.calls: list[str] = []

    def request(self, method: str, url: str, *, headers: dict[str, str],
                body: Any = None, timeout: float = 10.0) -> HttpResponse:
        parts = urlsplit(url)
        assert parts.hostname == "api.exchange.coinbase.com", url
        assert "Authorization" not in headers, "public endpoints carry no credential"
        self.calls.append(parts.path)
        if parts.path.endswith("/candles"):
            q = parse_qs(parts.query)
            assert q["granularity"] == ["3600"]
            start, end = pd.Timestamp(q["start"][0]), pd.Timestamp(q["end"][0])
            now = self.clock()
            rows = [
                [int(ts.timestamp()), float(r["low"]), float(r["high"]), float(r["open"]),
                 float(r["close"]), 1.5]
                for ts, r in self.frame.iterrows()
                if start <= ts <= end and ts <= now  # the forming candle IS served
            ]
            rows.reverse()  # newest first, as Coinbase sends
            return HttpResponse(200, {"_body": rows})
        if parts.path.endswith("/ticker"):
            now = self.clock()
            last = float(self.frame.loc[self.frame.index <= now - H1, "close"].iloc[-1])
            half = self.spread_bps * 1e-4 * last / 2
            return HttpResponse(200, {"bid": f"{last - half:.2f}", "ask": f"{last + half:.2f}",
                                      "price": f"{last:.2f}", "time": now.isoformat()})
        raise AssertionError(f"unexpected request {url}")


def _document() -> StrategyDocument:
    """The crypto paper document (the retargeted seed), permitted on H1 for a 3-bar test."""
    seed = StrategyDocument.from_json(SEED.read_text(encoding="utf-8"))
    body = json.loads(retarget(seed, "crypto_core_v1").to_json())
    body["strategy_id"] = "donchian_ccore1_h1_forward_test"
    body["timeframes"] = ["H1"]
    body["universe"] = ["BTCGBP"]
    return StrategyDocument.from_json(json.dumps(body))


def _frame(n: int = 460, seed: int = 11) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    close = 60_000.0 * np.exp(np.cumsum(rng.normal(0.0, 0.006, n)))
    open_ = np.concatenate([[60_000.0], close[:-1]])
    wick = np.abs(rng.normal(0.0, 0.003, n)) * close
    high = np.maximum(open_, close) + wick
    low = np.minimum(open_, close) - wick
    index = pd.date_range("2026-01-01", periods=n, freq="h", tz="UTC")
    return pd.DataFrame({"open": open_, "high": high, "low": low, "close": close},
                        index=index).round(2)


def _first_signal(document: StrategyDocument, frame: pd.DataFrame, direction: Direction) -> int:
    compiled = compile_strategy(document.bind_defaults())
    prepared = compiled.prepare(frame)
    for i in range(compiled.warmup_period + 20, len(frame) - 3):
        sig = compiled.generate_signal(prepared, i, instrument="BTCGBP", timeframe="H1")
        if sig is not None:
            if sig.direction is direction:
                return i
            continue
    raise AssertionError(f"no {direction.value} signal in the synthetic series")


def _wiring(tmp: Path, document: StrategyDocument) -> Path:
    body = json.loads(CRYPTO_WIRING.read_text())
    body["version"] = "paper_forward_crypto_test"
    body["strategies"] = [{
        "strategy_id": document.strategy_id,
        "document": "doc.json",
        "content_hash": document.bind_defaults().content_hash(),
    }]
    body["instruments"] = ["BTCGBP"]
    body["timeframe"] = "H1"
    body["book"] = {"max_concurrent": 1}
    body["feed"]["warmup_bars"] = 400
    body["feed"]["history_bars"] = 1000
    path = tmp / "paper_forward_crypto_test.json"
    path.write_text(json.dumps(body, indent=2))
    return path


class Rig:
    def __init__(self, tmp: Path, direction: Direction) -> None:
        from fiboki.entrypoints.paper_forward import compose_runtime

        reset_log_once()
        self.document = _document()
        strategies = tmp / "strategies"
        strategies.mkdir()
        (strategies / "doc.json").write_text(self.document.to_json())
        frame = _frame()
        s = _first_signal(self.document, frame, direction)
        frame.index = frame.index + (SATURDAY - frame.index[s])
        self.frame, self.s = frame, s
        # The first poll is at the signal bar's OPEN: its newest closed bar is s-1.
        self.clock = FakeClock(SATURDAY + pd.Timedelta(seconds=25))
        self.transport = FakeCoinbase(frame, self.clock)
        self.settings = load_settings({"FIBOKI_STATE_DIR": str(tmp / "var")})
        self.channel = MemoryChannel()
        self.store = WorkerStore.sqlite_at(tmp / "state.db")
        self.runtime = compose_runtime(
            self.settings, _wiring(tmp, self.document), store=self.store,
            environ={},  # no credential exists for public data, and none is needed
            transport=self.transport, dispatcher=AlertDispatcher([self.channel]),
            strategies_dir=strategies, clock=self.clock, sleeper=self.clock.sleep,
            monotonic=self.clock.mono, worker_id="paper-crypto@test:1", max_cycles=1,
        )
        self.worker = self.runtime.worker

    def start(self) -> None:
        assert self.worker.run(install_signals=False) == 0, self.worker.heartbeat.last_error

    def next_bar(self) -> Any:
        self.clock.now = self.clock.now.floor("h") + H1 + pd.Timedelta(seconds=25)
        return self.worker.run_cycle()

    def events(self, kind: str) -> list[dict[str, Any]]:
        return self.runtime.journal.events(kind)


@pytest.fixture
def long_rig(tmp_path: Path):
    rig = Rig(tmp_path, Direction.LONG)
    yield rig
    rig.store.close()


@pytest.fixture
def short_rig(tmp_path: Path):
    rig = Rig(tmp_path, Direction.SHORT)
    yield rig
    rig.store.close()


def test_a_saturday_long_breakout_fills_on_paper_with_spot_costs(long_rig: Rig) -> None:
    rig = long_rig
    assert rig.clock().dayofweek == 5, "the test runs on a Saturday"
    rig.start()
    runtime = rig.runtime
    assert runtime.wiring.market_data == "coinbase_public"
    assert "paper_forward_crypto" in str(runtime.journal.ledger_dir)
    result = rig.next_bar()  # the signal bar closes
    assert result.jobs == 1, result.detail
    (attempt,) = runtime.recorder.attempts
    assert attempt.allowed, attempt.decision.reasons
    for check in ("market_state", "direction_permitted", "abnormal_spread", "data_freshness"):
        assert check in attempt.decision.checks_run
    (fill,) = rig.events("venue_fill")
    assert fill["mid"] == pytest.approx(float(rig.frame["close"].iloc[rig.s]))
    rig.next_bar()  # the book enters at the next open
    (managed,) = runtime.manager.open_positions
    assert managed.position.direction is Direction.LONG
    # COINBASE_UK_SPOT charges 60 bps of notional on the entry leg, in GBP, on the
    # DEALT price (mid + half spread + 5 bps slippage), so within 0.1% of 60 bps of mid.
    pos = managed.position
    notional = pos.entry_price * pos.size
    charged = managed.entry_costs_account["commission"]
    assert 0.006 * notional <= charged <= 0.006 * notional * 1.001
    assert runtime.manager.book.costs.commission == pytest.approx(charged)
    # Every request went to the public host, with no credential (FakeCoinbase asserts).
    assert {p.rsplit("/", 1)[-1] for p in rig.transport.calls} == {"candles", "ticker"}
    pricing = rig.events("pricing")
    assert pricing and pricing[-1]["sample"]["quotes"]["BTCGBP"]["spread"] > 0


def test_a_short_breakout_is_refused_by_the_gateway_not_the_strategy(short_rig: Rig) -> None:
    rig = short_rig
    rig.start()
    rig.next_bar()
    runtime = rig.runtime
    attempts = runtime.recorder.attempts
    assert attempts, "the strategy DID signal short; the risk layer must say no"
    (attempt,) = attempts
    assert not attempt.allowed
    assert any(r.startswith("short_not_permitted:BTCGBP") for r in attempt.decision.reasons)
    assert rig.events("venue_fill") == []
    rig.next_bar()
    assert runtime.manager.open_positions == []


def test_the_crypto_journal_is_separate_from_the_fx_session(tmp_path: Path) -> None:
    from fiboki.entrypoints.paper_forward import DEFAULT_WIRING, load_wiring

    fx, crypto = load_wiring(DEFAULT_WIRING), load_wiring(CRYPTO_WIRING)
    assert fx.ledger_dir != crypto.ledger_dir
    assert fx.version != crypto.version
    assert fx.worker["lease_name"] != crypto.worker["lease_name"]
