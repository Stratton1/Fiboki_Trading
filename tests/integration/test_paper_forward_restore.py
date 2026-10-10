"""Paper forward across a process restart: the restored run IS the uninterrupted run.

The defect (2026-10-02): the book, the venue position manager and the paper
venue lived in memory, so every restart abandoned the open paper positions and
the forward record stopped describing what an account would show. The fix is
a crash-safe state snapshot written after every completed cycle
(``fiboki.entrypoints.paper_state``).

What these tests prove, on the WHOLE composition (``compose_runtime``) driven by
a fake clock and a candle/pricing transport that serves one synthetic EURUSD H1
series by the clock (nothing leaves the process):

* PARITY. N bars uninterrupted versus k bars, a restart, then N-k bars: the
  trade ledger, every venue fill, every refused submission and every per-bar
  balance/equity/peak row are byte-identical. The feed's window is made SHORT
  (it slides from the first bar) so the test also proves the restored feed
  rebuilds the exact indicator window: the trend filter is a 100-bar EMA,
  whose value depends on where the window starts.
* A restart while bars close (the process was down for several bars): the
  missed bars are applied to the book in order, so a stop hit during the gap
  closes the trade on the bar it was hit, at the price the fill model gives,
  exactly as in the uninterrupted run.
* A restart while the venue has not confirmed a close: the pending close is
  restored, start-up reconciliation is clean, and the next bar re-sends it.
* Refusals: a corrupted snapshot (CRC), a changed wiring, and a snapshot older
  than the ledgers each fall back to the journalled abandonment, with the
  reason, and alert.
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
from fiboki.core.contracts import RiskDecision
from fiboki.entrypoints import paper_state
from fiboki.entrypoints.paper_forward import PaperForwardRuntime, compose_runtime, load_wiring
from fiboki.obs.alerts import AlertDispatcher, MemoryChannel
from fiboki.strategy.dsl import StrategyDocument
from fiboki.workers.base import WorkerStore, reset_log_once

REPO = Path(__file__).resolve().parents[2]
SEED = REPO / "research" / "strategies" / "donchian_breakout_atr.json"
ACCOUNT = "101-004-0000000-001"
ENV = {"FIBOKI_OANDA_PRACTICE_TOKEN": "practice-token-not-real",
       "FIBOKI_OANDA_PRACTICE_ACCOUNT_ID": ACCOUNT}
H1 = pd.Timedelta(hours=1)
#: The first forward bar boundary: Monday 2026-10-19 01:00 UTC (the 00:00 bar
#: has just closed). The run stays inside one trading week.
FIRST_POLL = pd.Timestamp("2026-10-19T01:00:00Z")
WARMUP = 400
HISTORY = 420
N_POLLS = 110


# ------------------------------------------------------------------ fixtures


class FakeClock:
    def __init__(self, start: pd.Timestamp) -> None:
        self.now = start

    def __call__(self) -> pd.Timestamp:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.now = self.now + pd.Timedelta(seconds=float(seconds))

    def mono(self) -> float:
        return self.now.value / 1e9


def _frame(seed: int = 12) -> pd.DataFrame:
    """Hourly EURUSD-like mids with trending stretches, ending after the forward week."""
    start = FIRST_POLL - (WARMUP + 400) * H1
    n = WARMUP + 400 + N_POLLS + 10
    rng = np.random.default_rng(seed)
    drift = np.repeat(rng.choice([-1.0, 1.0], size=n // 40 + 1), 40)[:n] * 0.0006
    returns = drift + rng.normal(0.0, 0.0024, n)
    close = 1.10 * np.exp(np.cumsum(returns))
    open_ = np.concatenate([[1.10], close[:-1]])
    wick = np.abs(rng.normal(0.0, 0.0016, n)) * close
    high = np.maximum(open_, close) + wick
    low = np.minimum(open_, close) - wick
    index = pd.date_range(start, periods=n, freq="h", tz="UTC")
    frame = pd.DataFrame({"open": open_, "high": high, "low": low, "close": close}, index=index)
    return frame.round(5)


class MarketTransport:
    """Serves the frame AS OF the clock: complete candles plus the forming one."""

    def __init__(self, frame: pd.DataFrame, clock: FakeClock) -> None:
        self.frame = frame
        self.clock = clock
        self.calls: list[str] = []

    def request(self, method: str, url: str, *, headers: dict[str, str],
                body: Any = None, timeout: float = 10.0) -> HttpResponse:
        parts = urlsplit(url)
        self.calls.append(parts.path)
        now = self.clock()
        complete = self.frame.loc[self.frame.index + H1 <= now]
        if parts.path.endswith("/candles"):
            count = int(parse_qs(parts.query).get("count", ["500"])[0])
            rows = []
            for ts, r in complete.iloc[-count:].iterrows():
                rows.append(self._candle(ts, r, True))
            forming = self.frame.loc[self.frame.index == now.floor("h")]
            for ts, r in forming.iterrows():
                rows.append(self._candle(ts, r, False))
            return HttpResponse(200, {"instrument": "EUR_USD", "granularity": "H1",
                                      "candles": rows})
        if parts.path.endswith("/pricing"):
            mid = float(complete["close"].iloc[-1])
            half = 1.2 * 0.0001 / 2
            stamp = now.strftime("%Y-%m-%dT%H:%M:%S.000000000Z")
            return HttpResponse(200, {"time": now.isoformat(), "prices": [{
                "type": "PRICE", "instrument": "EUR_USD", "time": stamp, "tradeable": True,
                "status": "tradeable",
                "bids": [{"price": f"{mid - half:.5f}", "liquidity": 1000000}],
                "asks": [{"price": f"{mid + half:.5f}", "liquidity": 1000000}],
            }]})
        raise AssertionError(f"unexpected request {method} {url}")

    @staticmethod
    def _candle(ts: pd.Timestamp, r: pd.Series, complete: bool) -> dict[str, Any]:
        return {
            "complete": complete,
            "volume": 100,
            "time": ts.strftime("%Y-%m-%dT%H:%M:%S.000000000Z"),
            "mid": {k[0]: f"{float(r[k]):.5f}" for k in ("open", "high", "low", "close")},
        }


def _document() -> StrategyDocument:
    body = json.loads(SEED.read_text(encoding="utf-8"))
    body["strategy_id"] = "donchian_breakout_atr_h1_restore_test"
    body["timeframes"] = ["H1"]
    return StrategyDocument.from_json(json.dumps(body))


def _wiring(tmp: Path, document: StrategyDocument, *, version: str = "restore_test") -> Path:
    body = json.loads(
        (REPO / "src" / "fiboki" / "entrypoints" / "wiring" / "paper_forward_v1.json").read_text()
    )
    body["version"] = version
    body["strategies"] = [{
        "strategy_id": document.strategy_id,
        "document": "doc.json",
        "content_hash": document.bind_defaults().content_hash(),
    }]
    body["instruments"] = ["EURUSD"]
    body["timeframe"] = "H1"
    body["book"] = {"max_concurrent": 1}
    body["ledger_dir"] = "paper_forward/restore_test"
    body["feed"]["warmup_bars"] = WARMUP
    body["feed"]["history_bars"] = HISTORY
    path = tmp / f"{version}.json"
    path.write_text(json.dumps(body, indent=2))
    return path


class Account:
    """One paper account (one state directory) that processes come and go on."""

    def __init__(self, tmp: Path, frame: pd.DataFrame, *, wiring: Path | None = None) -> None:
        reset_log_once()
        self.tmp = tmp
        tmp.mkdir(parents=True, exist_ok=True)
        self.document = _document()
        self.strategies = tmp / "strategies"
        self.strategies.mkdir(exist_ok=True)
        (self.strategies / "doc.json").write_text(self.document.to_json())
        self.wiring = wiring or _wiring(tmp, self.document)
        self.clock = FakeClock(FIRST_POLL + pd.Timedelta(seconds=2))
        self.transport = MarketTransport(frame, self.clock)
        self.settings = load_settings({"FIBOKI_STATE_DIR": str(tmp / "var")})
        self.channel = MemoryChannel()
        self.processes = 0
        self.runtime: PaperForwardRuntime | None = None
        self.store: WorkerStore | None = None
        #: bar start -> signals emitted on it (the bars where a decision was made)
        self.signal_bars: list[pd.Timestamp] = []

    # -- a process -------------------------------------------------------

    def start(self, *, wiring: Path | None = None) -> PaperForwardRuntime:
        """Compose a NEW process on this account's state and run its first cycle."""
        if self.store is not None:
            self.store.close()
        self.processes += 1
        self.store = WorkerStore.sqlite_at(self.tmp / f"state{self.processes}.db")
        self.runtime = compose_runtime(
            self.settings,
            wiring or self.wiring,
            store=self.store,
            environ=ENV,
            transport=self.transport,
            dispatcher=AlertDispatcher([self.channel]),
            strategies_dir=self.strategies,
            clock=self.clock,
            sleeper=self.clock.sleep,
            monotonic=self.clock.mono,
            worker_id=f"paper@test:{self.processes}",
            max_cycles=1,
        )
        before = self.runtime.signals.signals_emitted
        code = self.runtime.worker.run(install_signals=False)
        assert code == 0, self.runtime.worker.heartbeat.last_error
        self._note_signals(before)
        return self.runtime

    def next_bar(self) -> None:
        assert self.runtime is not None
        self.clock.now = self.clock.now.floor("h") + H1 + pd.Timedelta(seconds=3)
        before = self.runtime.signals.signals_emitted
        self.runtime.worker.run_cycle()
        self._note_signals(before)

    def _note_signals(self, before: int) -> None:
        assert self.runtime is not None
        if self.runtime.signals.signals_emitted > before:
            self.signal_bars.append(self.clock.now.floor("h") - H1)

    def close(self) -> None:
        if self.store is not None:
            self.store.close()
            self.store = None

    # -- what was recorded ----------------------------------------------

    @property
    def ledger(self) -> Path:
        return Path(self.settings.state_dir) / "paper_forward" / "restore_test"

    def events(self, kind: str) -> list[dict[str, Any]]:
        from fiboki.core.durable import read_payloads

        rows = [json.loads(p) for p in read_payloads(self.ledger / "journal.jsonl")]
        return [r for r in rows if r["kind"] == kind]

    def trades(self) -> list[dict[str, Any]]:
        from fiboki.core.durable import read_payloads

        return [json.loads(p) for p in read_payloads(self.ledger / "trades.jsonl")]


#: Random per process or per object by construction, so excluded: uuids, the
#: process id and wall-clock stamps of when a line was written.
_VOLATILE = {
    "trade_id", "position_id", "plan_id", "client_ref", "process", "at", "written_at", "caught_up",
}


def _clean(row: Any) -> Any:
    if isinstance(row, dict):
        return {k: _clean(v) for k, v in sorted(row.items()) if k not in _VOLATILE}
    if isinstance(row, list):
        return [_clean(v) for v in row]
    return row


def record(account: Account) -> dict[str, str]:
    """The decision record of a run, as canonical text."""
    def dump(rows: list[Any]) -> str:
        return "\n".join(json.dumps(_clean(r), sort_keys=True) for r in rows)

    return {
        "trades": dump(account.trades()),
        "fills": dump(account.events("venue_fill")),
        "refused": dump(
            account.events("submission_not_accepted") + account.events("entry_refused_by_book")
        ),
        "bars": dump(account.events("bar")),
        "opened": dump(account.events("position_opened")),
    }


def run_uninterrupted(tmp: Path, frame: pd.DataFrame, polls: int = N_POLLS) -> Account:
    acct = Account(tmp, frame)
    acct.start()
    for _ in range(polls - 1):
        acct.next_bar()
    acct.close()
    return acct


@pytest.fixture(scope="module")
def frame() -> pd.DataFrame:
    return _frame()


@pytest.fixture(scope="module")
def baseline(tmp_path_factory: pytest.TempPathFactory, frame: pd.DataFrame) -> Account:
    return run_uninterrupted(tmp_path_factory.mktemp("uninterrupted"), frame)


def _open_at(baseline: Account, *, min_gap: int = 2) -> list[tuple[pd.Timestamp, dict]]:
    """(bar after which a position is open, the trade) for every closed trade."""
    out = []
    for trade in baseline.trades():
        entry = pd.Timestamp(trade["entry_time"])
        exit_ = pd.Timestamp(trade["exit_time"])
        if (exit_ - entry) >= min_gap * H1:
            out.append((entry, trade))
    return out


# --------------------------------------------------------------------- tests


def test_the_scenario_trades_and_is_itself_deterministic(
    tmp_path: Path, frame: pd.DataFrame, baseline: Account
) -> None:
    trades = baseline.trades()
    assert len(trades) >= 2, "the synthetic series must trade for parity to mean anything"
    assert baseline.events("venue_fill"), "orders reached the paper venue"
    again = run_uninterrupted(tmp_path / "again", frame)
    assert record(again) == record(baseline)


@pytest.mark.parametrize("which", [0, -1])
def test_restart_with_an_open_position_is_byte_identical_to_never_restarting(
    tmp_path: Path, frame: pd.DataFrame, baseline: Account, which: int
) -> None:
    """k bars, process dies, a new process starts mid-bar, N-k bars: same record."""
    entry, trade = _open_at(baseline)[which]
    # Restart after the bar FOLLOWING the entry bar has been processed, so the
    # position is open in the book (and its stop may already have trailed).
    k = int((entry - (FIRST_POLL - H1)) / H1) + 2
    acct = Account(tmp_path, frame)
    acct.start()
    for _ in range(k - 1):
        acct.next_bar()
    first = acct.runtime
    assert first is not None and first.manager.open_positions, "a position is open"
    book_before = first.manager.export_state()
    # -- the process dies; a new one starts half an hour into the next bar ----
    acct.clock.now = acct.clock.now + pd.Timedelta(minutes=30)
    second = acct.start()
    assert second is not first
    assert second.restored_positions and not second.abandoned_positions
    (restored,) = acct.events("positions_restored_at_restart")
    assert restored["positions"][0]["broker_ref"] == book_before["book"]["open"][0]["ref"]
    assert not acct.events("positions_abandoned_at_restart")
    assert second.worker.last_position_divergences == ()
    assert second.manager.export_state() == book_before, "restored exactly, nothing re-run"
    for _ in range(N_POLLS - k):
        acct.next_bar()
    acct.close()
    assert record(acct) == record(baseline)
    summary = json.loads((second.journal.session_dir / "summary.json").read_text())
    assert summary["restarts"] == 1
    assert summary["abandoned_at_restart"] == 0
    assert summary["restored_at_restart"] >= 1
    assert summary["snapshot_at_start"] == "restore"


def test_restart_between_signal_and_book_entry_keeps_the_pending_entry(
    tmp_path: Path, frame: pd.DataFrame, baseline: Account
) -> None:
    """The venue filled at the signal bar's close; the book fills at the next open.
    A restart in between must carry the pending entry, not orphan the venue fill."""
    fill_bar = baseline.signal_bars[0]
    k = int((fill_bar - (FIRST_POLL - H1)) / H1) + 1
    acct = Account(tmp_path, frame)
    acct.start()
    for _ in range(k - 1):
        acct.next_bar()
    first = acct.runtime
    assert first is not None and first.manager.book.pending, "venue filled, book pending"
    assert first.venue.positions()
    acct.clock.now = acct.clock.now + pd.Timedelta(minutes=10)
    second = acct.start()
    assert [p["state"] for p in second.restored_positions] == ["entry_pending_in_book"]
    assert second.worker.last_position_divergences == ()
    for _ in range(N_POLLS - k):
        acct.next_bar()
    acct.close()
    assert record(acct) == record(baseline)


def test_a_stop_hit_while_the_process_was_down_is_realised_on_the_bar_it_was_hit(
    tmp_path: Path, frame: pd.DataFrame, baseline: Account
) -> None:
    # A trade whose exit falls inside a window with no signal, so the run that
    # was down (and therefore evaluated nothing during the gap) and the run
    # that was up make the same decisions.
    signals = set(baseline.signal_bars)
    chosen = None
    for entry, trade in _open_at(baseline, min_gap=3):
        exit_bar = pd.Timestamp(trade["exit_time"])
        down_from = exit_bar - H1  # last bar processed before dying: one before the exit
        gap = [down_from + i * H1 for i in range(1, 4)]  # exit bar + two more missed
        if entry < down_from and not signals & set(gap) and trade["exit_reason"] in (
            "stop_loss", "trailing_stop", "breakeven"
        ):
            chosen = (down_from, trade)
            break
    assert chosen is not None, "the scenario has a stopped-out trade to miss"
    down_from, trade = chosen
    k = int((down_from - (FIRST_POLL - H1)) / H1) + 1
    acct = Account(tmp_path, frame)
    acct.start()
    for _ in range(k - 1):
        acct.next_bar()
    assert acct.runtime is not None and acct.runtime.manager.open_positions
    # Down for three bars: the exit bar and the two after it close meanwhile.
    acct.clock.now = acct.clock.now.floor("h") + 4 * H1 + pd.Timedelta(seconds=3)
    second = acct.start()
    (caught,) = acct.events("bars_caught_up")
    assert caught["count"] == 3
    assert pd.Timestamp(caught["first"]) == down_from + H1
    assert not second.manager.open_positions, "closed on the bar the stop was hit"
    mine = [t for t in acct.trades() if t["entry_time"] == trade["entry_time"]]
    assert len(mine) == 1
    assert _clean(mine[0]) == _clean(trade), "same exit bar, price, reason and costs"
    assert second.venue.positions() == (), "the close reached the venue too"
    for _ in range(N_POLLS - k - 4):
        acct.next_bar()
    acct.close()
    assert record(acct) == record(baseline)


def test_a_restart_during_a_pending_close_restores_it_and_re_sends_it(
    tmp_path: Path, frame: pd.DataFrame, baseline: Account
) -> None:
    entry, trade = _open_at(baseline)[0]
    exit_bar = pd.Timestamp(trade["exit_time"])
    k = int((exit_bar - (FIRST_POLL - H1)) / H1) + 1
    acct = Account(tmp_path, frame)
    acct.start()
    for _ in range(k - 2):
        acct.next_bar()
    runtime = acct.runtime
    assert runtime is not None
    # The gateway refuses the close the book makes on the exit bar, as it did
    # on 2026-10-06 (risk_gateway_blocked_exit on a stale quote).
    real_exit = runtime.gateway.evaluate_exit

    def refuse(_ctx: Any) -> RiskDecision:
        return RiskDecision(False, ("stale_price:test",))

    runtime.gateway.evaluate_exit = refuse  # type: ignore[method-assign]
    acct.next_bar()
    runtime.gateway.evaluate_exit = real_exit  # type: ignore[method-assign]
    assert not runtime.manager.open_positions, "the book closed it"
    assert runtime.manager.pending_closes, "the venue did not confirm"
    ref = next(iter(runtime.manager.pending_closes))
    # -- restart before the next bar ------------------------------------------
    acct.clock.now = acct.clock.now + pd.Timedelta(minutes=20)
    second = acct.start()
    assert ref in second.manager.pending_closes, "the pending close was restored"
    assert [p["state"] for p in second.restored_positions] == ["close_pending_at_venue"]
    assert second.worker.last_position_divergences == (), "start-up reconciliation clean"
    assert [p.venue_ref for p in second.venue.positions()] == [ref]
    acct.next_bar()
    assert not second.manager.pending_closes, "re-sent on the next bar and confirmed"
    assert second.venue.positions() == ()
    for _ in range(N_POLLS - k - 1):
        acct.next_bar()
    acct.close()
    assert record(acct) == record(baseline), "the book's record never depended on the venue"


# ---------------------------------------------------------------- refusals


def _to_open_position(acct: Account, baseline: Account) -> int:
    entry, _trade = _open_at(baseline)[0]
    k = int((entry - (FIRST_POLL - H1)) / H1) + 2
    acct.start()
    for _ in range(k - 1):
        acct.next_bar()
    assert acct.runtime is not None and acct.runtime.manager.open_positions
    acct.clock.now = acct.clock.now + pd.Timedelta(minutes=30)
    return k


def _assert_abandoned(acct: Account, status: str) -> dict[str, Any]:
    runtime = acct.runtime
    assert runtime is not None
    assert len(runtime.abandoned_positions) == 1 and not runtime.restored_positions
    (refused,) = acct.events("state_snapshot_refused")
    assert refused["status"] == status
    assert refused["kept_as"] and (acct.ledger / refused["kept_as"]).exists()
    (abandoned,) = acct.events("positions_abandoned_at_restart")
    assert abandoned["snapshot"] == status and abandoned["reason"]
    assert not runtime.manager.open_positions and runtime.venue.positions() == ()
    alerts = [a for a in acct.channel.sent if a.event.value == "reconciliation_divergence"]
    assert any("could not restore" in a.message for a in alerts)
    assert any("abandoned" in a.message for a in alerts)
    summary = json.loads((runtime.journal.session_dir / "summary.json").read_text())
    assert summary["abandoned_at_restart"] == 1 and summary["snapshot_at_start"] == status
    return refused


def test_a_corrupted_snapshot_is_refused_alerted_and_falls_back_to_abandonment(
    tmp_path: Path, frame: pd.DataFrame, baseline: Account
) -> None:
    acct = Account(tmp_path, frame)
    _to_open_position(acct, baseline)
    path = acct.ledger / paper_state.SNAPSHOT_FILE
    text = path.read_text()
    path.write_text(text.replace('"balance":', '"balance": ', 1))  # bytes change, CRC does not
    acct.start()
    refused = _assert_abandoned(acct, "corrupt")
    assert "crc32" in refused["reason"]
    critical = [a for a in acct.channel.sent if "could not restore" in a.message]
    assert critical and critical[0].severity.value == "critical"
    acct.close()


def test_a_changed_wiring_is_not_restored_into(
    tmp_path: Path, frame: pd.DataFrame, baseline: Account
) -> None:
    acct = Account(tmp_path, frame)
    _to_open_position(acct, baseline)
    body = json.loads(acct.wiring.read_text())
    body["sizing"]["risk_fraction"] = 0.004  # a reviewed change: a new sha
    changed = acct.tmp / "changed.json"
    changed.write_text(json.dumps(body, indent=2))
    assert load_wiring(changed).sha256 != load_wiring(acct.wiring).sha256
    acct.start(wiring=changed)
    refused = _assert_abandoned(acct, "mismatch")
    assert "wiring_sha256" in refused["reason"]
    acct.close()


def test_a_snapshot_older_than_the_ledgers_is_refused(
    tmp_path: Path, frame: pd.DataFrame, baseline: Account
) -> None:
    """The process died part-way through a cycle: after a ledger write, before the
    snapshot. Restoring would forget what the ledger recorded."""
    acct = Account(tmp_path, frame)
    _to_open_position(acct, baseline)
    from fiboki.core.durable import read_record

    # The snapshot as it stood BEFORE the dead process's last ledger write: a
    # valid record (its CRC verifies) whose watermark the ledger has outgrown.
    path = acct.ledger / paper_state.SNAPSHOT_FILE
    body = json.loads(read_record(path))
    body["watermark"]["trades_bytes"] -= 1 if body["watermark"]["trades_bytes"] else 0
    body["watermark"]["intents_bytes"] -= 1
    paper_state.write(path, body)
    acct.start()
    refused = _assert_abandoned(acct, "stale")
    assert "ledgers grew" in refused["reason"]
    acct.close()


def test_a_snapshot_that_cannot_be_written_alerts_and_does_not_stop_trading(
    tmp_path: Path, frame: pd.DataFrame, monkeypatch: pytest.MonkeyPatch
) -> None:
    acct = Account(tmp_path, frame)
    acct.start()
    path = acct.ledger / paper_state.SNAPSHOT_FILE
    before = path.read_bytes()

    def disk_full(*_a: Any, **_k: Any) -> None:
        raise OSError(28, "No space left on device")

    monkeypatch.setattr(paper_state, "write", disk_full)
    acct.next_bar()
    acct.next_bar()
    assert acct.runtime is not None
    assert acct.runtime.worker.heartbeat.last_error in (None, ""), "the cycle still ran"
    assert path.read_bytes() == before, "the last good snapshot is left alone"
    alerts = [a for a in acct.channel.sent if "could not write its state snapshot" in a.message]
    assert alerts and alerts[0].severity.value == "error"
    monkeypatch.undo()
    # The ledgers may have moved past the old snapshot; a restart now refuses it
    # as stale rather than restoring a past that forgets a recorded trade.
    acct.close()


def test_a_snapshot_that_verifies_but_does_not_load_is_refused_not_half_restored(
    tmp_path: Path, frame: pd.DataFrame, baseline: Account
) -> None:
    acct = Account(tmp_path, frame)
    _to_open_position(acct, baseline)
    from fiboki.core.durable import read_record

    path = acct.ledger / paper_state.SNAPSHOT_FILE
    body = json.loads(read_record(path))
    del body["state"]["venue"]  # a shape this code cannot load; the CRC is fine
    paper_state.write(path, body)
    acct.start()
    refused = _assert_abandoned(acct, "corrupt")
    assert "did not load" in refused["reason"]
    acct.close()


def test_a_trade_closed_in_a_cycle_that_failed_before_the_ledger_write_is_not_lost(
    tmp_path: Path, frame: pd.DataFrame, baseline: Account
) -> None:
    """The book closes a trade; a journal write raises before ``record_trades``
    runs; the cycle fails and the snapshot is still written (``finally``). The
    process that does not restart writes the trade on its next bar, because
    ``record_trades`` is idempotent and runs every bar. A restored process
    must too: the snapshot's watermark is taken only after every closed trade
    the book holds is in ``trades.jsonl``. Otherwise the balance carries the
    loss and no ledger does, and the daily and weekly loss limits never see it."""
    entry, trade = _open_at(baseline)[0]
    exit_bar = pd.Timestamp(trade["exit_time"])
    k = int((exit_bar - (FIRST_POLL - H1)) / H1) + 1
    acct = Account(tmp_path, frame)
    acct.start()
    for _ in range(k - 2):
        acct.next_bar()
    runtime = acct.runtime
    assert runtime is not None
    real_event = runtime.journal.event

    def failing_journal(kind: str, **fields: Any) -> dict[str, Any]:
        if kind == "venue_instruction":  # written after the book's close, before the ledger
            raise OSError(5, "Input/output error")
        return real_event(kind, **fields)

    runtime.journal.event = failing_journal  # type: ignore[method-assign]
    with pytest.raises(OSError):
        acct.next_bar()
    runtime.journal.event = real_event  # type: ignore[method-assign]
    assert not runtime.manager.open_positions and len(runtime.manager.book.trades) == 1
    assert len(acct.trades()) == 1, "the snapshot write flushed the closed trade to the ledger"
    # -- restart; the trade is in the ledger exactly once, with the baseline's values
    acct.clock.now = acct.clock.now + pd.Timedelta(minutes=20)
    second = acct.start()
    assert second.snapshot_status["status"] == "restore"
    for _ in range(N_POLLS - k):
        acct.next_bar()
    acct.close()
    mine = [t for t in acct.trades() if t["entry_time"] == trade["entry_time"]]
    assert len(mine) == 1 and _clean(mine[0]) == _clean(trade)
    assert record(acct)["trades"] == record(baseline)["trades"]
    assert second.manager.book.realised == pytest.approx(
        sum(float(t["net_pnl"]) for t in acct.trades())
    ), "the realised P&L and the ledger agree"
