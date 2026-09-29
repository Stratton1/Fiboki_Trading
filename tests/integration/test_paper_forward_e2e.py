"""Paper forward, end to end, on RECORDED OANDA practice responses (audit F, P1-15).

The whole composition from ``fiboki.entrypoints.paper_forward.compose_runtime``
runs here -- the committed-format wiring file, a content-hashed strategy
document, the polling feed, the pricing spread source, the risk gateway with a
durable kill-switch journal, the execution service with fsynced intents, the
instant-fill paper venue and the venue position manager over the shared
position book -- with exactly one substitution: the network. Every candle and
every quote comes from a :class:`~fiboki.broker.oanda.RecordedTransport`
fixture in the documented v20 shape, so nothing leaves the process.

Three H1 bars, delivered one poll at a time on a fake clock:

1. 09:00 poll: the warm-up history arrives, the newest bar (08:00) is evaluated,
   no signal.
2. 10:00 poll: the 09:00 bar CLOSES with a Donchian breakout. The signal is
   sized once, passes all twenty gateway checks (including ``abnormal_spread``
   against the LIVE quoted spread), is written PENDING before dispatch and is
   dealt by the paper venue at the bar's close.
3. 11:00 poll: the book models the entry at the 10:00 bar's OPEN -- the
   backtester's fill -- and the position is open in both the book and the
   venue, and journalled.

The kill switch is honoured MID-RUN, from its durable journal, exactly as
``fiboki killswitch pause`` in another terminal would set it.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import pytest

from fiboki.api.settings import load_settings
from fiboki.broker.oanda import HttpResponse, RecordedTransport
from fiboki.obs.alerts import AlertDispatcher, MemoryChannel
from fiboki.risk.killswitch import FileKillSwitchJournal, KillSwitch, KillSwitchMode
from fiboki.strategy.compiler import compile_strategy
from fiboki.strategy.dsl import StrategyDocument
from fiboki.workers.base import WorkerStore, reset_log_once

REPO = Path(__file__).resolve().parents[2]
SEED = REPO / "research" / "strategies" / "donchian_breakout_atr.json"
ACCOUNT = "101-004-0000000-001"
CANDLES = "GET /v3/instruments/EUR_USD/candles"
PRICING = f"GET /v3/accounts/{ACCOUNT}/pricing"
H1 = pd.Timedelta(hours=1)
#: The signal bar STARTS here (a Wednesday, no high-impact EUR/USD release near).
SIGNAL_BAR = pd.Timestamp("2026-10-21T09:00:00Z")
ENV = {"FIBOKI_OANDA_PRACTICE_TOKEN": "practice-token-not-real",
       "FIBOKI_OANDA_PRACTICE_ACCOUNT_ID": ACCOUNT}


class FakeClock:
    def __init__(self, start: pd.Timestamp) -> None:
        self.now = start

    def __call__(self) -> pd.Timestamp:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.now = self.now + pd.Timedelta(seconds=float(seconds))

    def mono(self) -> float:
        return self.now.value / 1e9


# --------------------------------------------------------------- fixtures


def _h1_document() -> StrategyDocument:
    """The Donchian seed, permitted on H1 so three H1 bars can drive it."""
    body = json.loads(SEED.read_text(encoding="utf-8"))
    body["strategy_id"] = "donchian_breakout_atr_h1_forward_test"
    body["timeframes"] = ["H1"]
    return StrategyDocument.from_json(json.dumps(body))


def _frame(n: int = 460, seed: int = 7) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    returns = rng.normal(0.0002, 0.0022, n)
    close = 1.10 * np.exp(np.cumsum(returns))
    open_ = np.concatenate([[1.10], close[:-1]])
    spread = np.abs(rng.normal(0.0, 0.0015, n)) * close
    high = np.maximum(open_, close) + spread
    low = np.minimum(open_, close) - spread
    index = pd.date_range("2026-01-01", periods=n, freq="h", tz="UTC")
    # Five decimals, as v20 quotes EURUSD: the recorded strings ARE the data.
    frame = pd.DataFrame({"open": open_, "high": high, "low": low, "close": close}, index=index)
    return frame.round(5)


def _first_signal(document: StrategyDocument, frame: pd.DataFrame) -> int:
    compiled = compile_strategy(document.bind_defaults())
    prepared = compiled.prepare(frame)
    start = compiled.warmup_period + 20
    for i in range(start, len(frame) - 2):
        if compiled.generate_signal(prepared, i, instrument="EURUSD", timeframe="H1"):
            return i
    raise AssertionError("the synthetic series produced no signal; change the seed")


def _candles(frame: pd.DataFrame, lo: int, hi: int) -> HttpResponse:
    """Candles ``lo..hi-1`` complete, plus the forming candle ``hi`` (complete: false)."""
    rows = []
    for i in range(lo, min(hi + 1, len(frame))):
        r = frame.iloc[i]
        rows.append(
            {
                "complete": i < hi,
                "volume": 100,
                "time": frame.index[i].strftime("%Y-%m-%dT%H:%M:%S.000000000Z"),
                "mid": {k[0]: f"{float(r[k]):.5f}" for k in ("open", "high", "low", "close")},
            }
        )
    return HttpResponse(200, {"instrument": "EUR_USD", "granularity": "H1", "candles": rows})


def _pricing(at: pd.Timestamp, mid: float, spread_pips: float) -> HttpResponse:
    half = spread_pips * 0.0001 / 2
    return HttpResponse(
        200,
        {
            "time": at.isoformat(),
            "prices": [
                {
                    "type": "PRICE",
                    "instrument": "EUR_USD",
                    "time": at.strftime("%Y-%m-%dT%H:%M:%S.000000000Z"),
                    "tradeable": True,
                    "status": "tradeable",
                    "bids": [{"price": f"{mid - half:.5f}", "liquidity": 1000000}],
                    "asks": [{"price": f"{mid + half:.5f}", "liquidity": 1000000}],
                }
            ],
        },
    )


def _wiring(tmp: Path, document: StrategyDocument) -> Path:
    body = json.loads(
        (REPO / "src" / "fiboki" / "entrypoints" / "wiring" / "paper_forward_v1.json").read_text()
    )
    body["version"] = "paper_forward_test"
    body["strategies"] = [
        {
            "strategy_id": document.strategy_id,
            "document": "doc.json",
            "content_hash": document.bind_defaults().content_hash(),
        }
    ]
    body["instruments"] = ["EURUSD"]
    body["timeframe"] = "H1"
    body["book"] = {"max_concurrent": 1}
    body["feed"]["warmup_bars"] = 400
    body["feed"]["history_bars"] = 1000
    path = tmp / "paper_forward_test.json"
    path.write_text(json.dumps(body, indent=2))
    return path


class Rig:
    """Everything one test needs: the composed runtime and its recorded venue."""

    def __init__(self, tmp: Path, *, spread_pips: tuple[float, float, float] = (1.2, 1.2, 1.2)):
        reset_log_once()
        self.tmp = tmp
        self.document = _h1_document()
        strategies = tmp / "strategies"
        strategies.mkdir()
        (strategies / "doc.json").write_text(self.document.to_json())
        self.frame = _frame()
        s = _first_signal(self.document, self.frame)
        # Re-stamp so the signal bar starts at SIGNAL_BAR.
        self.frame.index = self.frame.index + (SIGNAL_BAR - self.frame.index[s])
        self.s = s
        f = self.frame
        polls = [SIGNAL_BAR + k * H1 for k in range(3)]  # 09:00, 10:00, 11:00
        candles = [_candles(f, 0, s), _candles(f, s - 5, s + 1), _candles(f, s - 4, s + 2)]
        pricing = [
            _pricing(at + pd.Timedelta(seconds=4), float(f["close"].iloc[s - 1 + k]), spread_pips[k])
            for k, at in enumerate(polls)
        ]
        self.transport = RecordedTransport({CANDLES: candles, PRICING: pricing})
        self.clock = FakeClock(SIGNAL_BAR + pd.Timedelta(seconds=2))
        self.settings = load_settings({"FIBOKI_STATE_DIR": str(tmp / "var")})
        self.killswitch_path = Path(self.settings.killswitch_path)
        self.channel = MemoryChannel()
        self.store = WorkerStore.sqlite_at(tmp / "state.db")
        from fiboki.entrypoints.paper_forward import compose_runtime

        self.runtime = compose_runtime(
            self.settings,
            _wiring(tmp, self.document),
            store=self.store,
            environ=ENV,
            transport=self.transport,
            dispatcher=AlertDispatcher([self.channel]),
            strategies_dir=strategies,
            clock=self.clock,
            sleeper=self.clock.sleep,
            monotonic=self.clock.mono,
            worker_id="paper@test:1",
            max_cycles=1,
        )
        self.worker = self.runtime.worker

    def start(self) -> None:
        """A full process start: lease, fail-closed startup reconcile, the 09:00 poll."""
        assert self.worker.run(install_signals=False) == 0, self.worker.heartbeat.last_error

    def next_bar(self) -> Any:
        self.clock.now = self.clock.now.floor("h") + H1 + pd.Timedelta(seconds=3)
        return self.worker.run_cycle()

    def pause(self) -> None:
        KillSwitch(FileKillSwitchJournal(self.killswitch_path)).activate(
            KillSwitchMode.PAUSE, operator="joe", reason="mid-run test"
        )

    def flatten(self) -> None:
        KillSwitch(FileKillSwitchJournal(self.killswitch_path)).activate(
            KillSwitchMode.FLATTEN, operator="joe", reason="mid-run flatten test"
        )

    def events(self, kind: str) -> list[dict[str, Any]]:
        return self.runtime.journal.events(kind)

    def close(self) -> None:
        self.store.close()


@pytest.fixture
def rig(tmp_path: Path):
    r = Rig(tmp_path)
    yield r
    r.close()


# ------------------------------------------------------------------ tests


def test_three_bars_drive_one_signal_to_a_filled_paper_position(rig: Rig) -> None:
    rig.start()
    runtime = rig.runtime
    (start,) = rig.events("session_start")
    assert start["wiring_sha256"] == runtime.wiring.sha256
    assert rig.worker.reconciliations >= 1, "startup reconciliation ran before trading"
    assert runtime.signals.signals_emitted == 0

    # -- 10:00: the 09:00 bar closes with a breakout ---------------------------
    result = rig.next_bar()
    assert result.jobs == 1, result.detail
    assert runtime.signals.signals_emitted == 1
    (attempt,) = runtime.recorder.attempts
    assert attempt.allowed, attempt.decision.reasons
    assert attempt.extra["wiring_sha256"] == runtime.wiring.sha256
    assert "abnormal_spread" in attempt.decision.checks_run
    (fill,) = rig.events("venue_fill")
    signal_close = float(rig.frame["close"].iloc[rig.s])
    assert fill["mid"] == pytest.approx(signal_close), "dealt at the signal bar's close"
    assert fill["price"] != fill["mid"], "plus the profile's half spread"
    intents = (runtime.journal.intents_path).read_text().splitlines()
    assert json.loads(intents[0])["state"] == "pending", "PENDING written before dispatch"
    assert json.loads(intents[-1])["state"] == "filled"
    assert not runtime.manager.open_positions, "the BOOK has not filled yet (next open)"

    # -- mid-run: an operator pauses from another process ----------------------
    rig.pause()

    # -- 11:00: the book models the entry at the 10:00 bar's open --------------
    result = rig.next_bar()
    assert result.detail == "kill switch active", "no new risk is evaluated while paused"
    (managed,) = runtime.manager.open_positions
    assert managed.position.venue_ref == fill["broker_ref"]
    (opened,) = rig.events("position_opened")
    assert opened["broker_ref"] == fill["broker_ref"]
    assert opened["bar_time"] == (SIGNAL_BAR + H1).isoformat()
    assert len(runtime.venue.positions()) == 1

    # The journal row the API reads: one continuous paper account.
    summary = json.loads((runtime.journal.session_dir / "summary.json").read_text())
    assert summary["provenance"] == "paper"
    assert summary["open_positions"] == 1
    assert summary["wiring_sha256"] == runtime.wiring.sha256
    positions_csv = (runtime.journal.session_dir / "positions.csv").read_text()
    assert "EURUSD" in positions_csv
    # Every attempt row on disk carries the wiring hash.
    rows = [json.loads(line) for line in runtime.journal.attempts_path.read_text().splitlines()]
    assert rows and all(r["extra"]["wiring_sha256"] == runtime.wiring.sha256 for r in rows)
    # No false reconciliation alarm for the adopted-but-not-yet-modelled window.
    assert not [a for a in rig.channel.sent if a.event.value == "reconciliation_divergence"]


def test_a_pause_before_the_signal_bar_opens_nothing(rig: Rig) -> None:
    rig.start()
    rig.pause()
    result = rig.next_bar()
    assert result.detail == "kill switch active"
    assert rig.runtime.recorder.attempts == []
    assert rig.events("venue_fill") == []
    assert rig.runtime.venue.positions() == ()
    assert not rig.runtime.journal.intents_path.exists() or not \
        rig.runtime.journal.intents_path.read_text().strip()


def test_a_flatten_mid_run_closes_the_position_through_the_execution_service(rig: Rig) -> None:
    rig.start()
    rig.next_bar()  # signal, venue fill
    rig.next_bar()  # book entry
    assert len(rig.runtime.manager.open_positions) == 1
    rig.flatten()
    rig.clock.sleep(5)
    rig.worker.run_cycle()  # flattens inside wait_until_due, before the next boundary
    assert rig.runtime.manager.open_positions == []
    assert rig.runtime.venue.positions() == ()
    (closed,) = rig.events("trade_closed")
    assert closed["exit_reason"] == "risk_halt"
    ledger = rig.runtime.journal.trades_path.read_text().splitlines()
    assert len(ledger) == 1 and json.loads(ledger[0])["wiring_sha256"] == rig.runtime.wiring.sha256
    exits = [a for a in rig.runtime.recorder.attempts if a.extra.get("exit")]
    assert exits and all(a.allowed for a in exits), "FLATTEN permits closes"
    trades_csv = (rig.runtime.journal.session_dir / "trades.csv").read_text()
    assert "risk_halt" in trades_csv


def test_a_wide_live_spread_blocks_the_signal_as_abnormal(tmp_path: Path) -> None:
    """P2-4: the check compares the QUOTED spread now, not the profile with itself."""
    rig = Rig(tmp_path, spread_pips=(1.2, 9.0, 1.2))  # 10x the 0.9-pip typical
    try:
        rig.start()
        result = rig.next_bar()
        assert result.jobs == 0
        (attempt,) = rig.runtime.recorder.attempts
        assert not attempt.allowed
        assert any(r.startswith("abnormal_spread:") for r in attempt.decision.reasons)
        assert rig.events("venue_fill") == []
    finally:
        rig.close()


def test_a_restart_carries_the_balance_and_records_abandoned_positions(rig: Rig) -> None:
    rig.start()
    rig.next_bar()
    rig.next_bar()
    assert len(rig.runtime.manager.open_positions) == 1
    rig.store.close()
    from fiboki.entrypoints.paper_forward import compose_runtime

    store = WorkerStore.sqlite_at(rig.tmp / "state2.db")
    try:
        again = compose_runtime(
            rig.settings,
            rig.runtime.wiring,
            store=store,
            environ=ENV,
            transport=rig.transport,
            strategies_dir=rig.tmp / "strategies",
            clock=rig.clock,
            sleeper=rig.clock.sleep,
            monotonic=rig.clock.mono,
            worker_id="paper@test:2",
        )
        assert again.restarts == 1
        assert len(again.abandoned_positions) == 1
        assert again.journal.events("positions_abandoned_at_restart")
        assert again.carried_balance == pytest.approx(rig.runtime.manager.book.balance)
    finally:
        store.close()
        rig.store = WorkerStore.sqlite_at(rig.tmp / "state.db")


def test_compose_refuses_without_credentials(tmp_path: Path) -> None:
    from fiboki.broker.http_transport import MissingCredential
    from fiboki.entrypoints.paper_forward import DEFAULT_WIRING, compose

    settings = load_settings({"FIBOKI_STATE_DIR": str(tmp_path / "var")})
    with WorkerStore.sqlite_at(tmp_path / "s.db") as store, pytest.raises(
        MissingCredential, match="FIBOKI_OANDA_PRACTICE_TOKEN"
    ):
        compose(settings, DEFAULT_WIRING, store=store, environ={}, transport=RecordedTransport({}))
