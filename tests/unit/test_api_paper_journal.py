"""The API serves the persisted PAPER journal, and never a PAPER chip on fixture data.

Fixture: ``tests/fixtures/paper_journal`` is ``summary.json``, ``trades.csv`` and
``positions.csv`` copied verbatim from ``research/reports/paper_sessions`` (the
two XAUUSD H4 replays ``scripts/run_paper_session.py`` produced). Telemetry and
exit legs are left out because the reader does not read them.

Expected values are the ones ``research/reports/paper_sessions/README.md``
reports for those runs: 5 and 278 closed trades, one position open at the end,
closing balances 97,323.29 and 85,295.26 on 100,000 USD accounts.
"""
from __future__ import annotations

import json
import shutil
from datetime import UTC, datetime
from pathlib import Path

import pytest

from fiboki.api.paper_journal import PaperJournalReader
from fiboki.api.platform import Platform
from fiboki.api.seed import SEED_PROVENANCES, generate
from fiboki.api.settings import load_settings
from fiboki.core.enums import Direction, ExitReason, Provenance

FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "paper_journal"
REAL_SESSIONS = Path(__file__).resolve().parents[2] / "research" / "reports" / "paper_sessions"

EXECUTED = {
    Provenance.PAPER,
    Provenance.SHADOW,
    Provenance.BROKER_DEMO,
    Provenance.BROKER_LIVE,
}


@pytest.fixture
def journal_copy(tmp_path) -> Path:
    root = tmp_path / "paper"
    shutil.copytree(FIXTURE, root)
    return root


def _platform(tmp_path, paper_root: Path | None, monkeypatch) -> Platform:
    monkeypatch.delenv("FIBOKI_PAPER_ROOT", raising=False)
    settings = load_settings({"FIBOKI_STATE_DIR": str(tmp_path / "state")})
    return Platform(settings, paper_root=paper_root)


# ------------------------------------------------------------------ reader


def test_reader_parses_both_real_sessions():
    journal = PaperJournalReader(FIXTURE).load()
    assert journal is not None
    assert journal.errors == ()
    by_id = {s.session_id: s for s in journal.sessions}
    assert set(by_id) == {"donchian_breakout_atr", "fib_golden_pocket_pullback"}

    donchian = by_id["donchian_breakout_atr"]
    fib = by_id["fib_golden_pocket_pullback"]
    assert (len(donchian.trades), len(donchian.positions)) == (5, 1)
    assert (len(fib.trades), len(fib.positions)) == (278, 0)
    for session in (donchian, fib):
        assert session.provenance is Provenance.PAPER
        assert session.account_ccy == "USD"
        assert session.initial_balance == 100_000.0
        assert session.as_of == datetime(2025, 12, 31, 21, 0, tzinfo=UTC)
        # Closing balance minus the opening one equals the closed-trade P&L,
        # so the reader raised no reconciliation warning.
        assert session.warnings == ()
        assert all(t.provenance is Provenance.PAPER for t in session.trades)
        assert all(t.as_of == session.as_of for t in session.trades)
    assert round(donchian.balance, 2) == 97_323.29
    assert round(fib.balance, 2) == 85_295.26


def test_reader_decodes_nanosecond_timestamps_and_enums():
    journal = PaperJournalReader(FIXTURE).load()
    assert journal is not None
    first = next(
        t for t in journal.trades if t.trade_id == "trd_e20fdf284ff84cc8"
    )
    # 1242925200000000000 ns = 2009-05-21 17:00:00 UTC.
    assert first.entry_time == datetime(2009, 5, 21, 17, 0, tzinfo=UTC)
    assert first.direction is Direction.LONG
    assert first.exit_reason is ExitReason.STOP_LOSS
    assert first.size_unit == "units"
    # Costs are the sum of the persisted components, not a separate claim.
    assert first.costs == pytest.approx(sum(first.cost_breakdown.values()))
    # No R multiple is recorded, so none is invented.
    assert first.r_multiple is None


def test_open_position_comes_from_the_venue_book_record():
    journal = PaperJournalReader(FIXTURE).load()
    assert journal is not None
    (position,) = journal.positions
    assert position.position_id == "pos_517861ad2f634142"
    assert position.direction is Direction.LONG
    assert position.size == 52.7
    assert position.entry_price == 934.28
    assert position.mark_price is None, "no mark was persisted; unknown, not entry"
    assert position.take_profit is None
    # Equity minus balance from the session summary, attributable because the
    # session holds exactly one open position.
    assert position.unrealised_pnl == pytest.approx(136_875.69, abs=0.01)


def test_account_sums_independent_sessions_and_says_so():
    journal = PaperJournalReader(FIXTURE).load()
    assert journal is not None
    account = journal.account()
    assert account.sessions == 2
    assert account.account_ccy == "USD"
    assert account.initial_balance == 200_000.0
    assert account.balance == pytest.approx(182_618.54, abs=0.02)
    assert account.realised_pnl == pytest.approx(account.balance - 200_000.0, abs=0.02)
    assert any("independent paper sessions" in w for w in account.warnings)


def test_mixed_currency_sessions_are_not_summed(journal_copy):
    summary = journal_copy / "fib_golden_pocket_pullback" / "summary.json"
    data = json.loads(summary.read_text())
    data["account_ccy"] = "GBP"
    summary.write_text(json.dumps(data))
    trades = journal_copy / "fib_golden_pocket_pullback" / "trades.csv"
    trades.write_text(trades.read_text().replace(",USD,", ",GBP,"))
    account = PaperJournalReader(journal_copy).load().account()
    assert account.account_ccy is None
    assert account.balance is None and account.equity is None
    assert any("different currencies" in w for w in account.warnings)


def test_session_without_a_stated_provenance_is_refused_not_assumed_paper(journal_copy):
    summary = journal_copy / "donchian_breakout_atr" / "summary.json"
    data = json.loads(summary.read_text())
    del data["provenance"]
    summary.write_text(json.dumps(data))
    journal = PaperJournalReader(journal_copy).load()
    assert [s.session_id for s in journal.sessions] == ["fib_golden_pocket_pullback"]
    assert journal.errors[0][0] == "donchian_breakout_atr"
    assert "refusing to assume PAPER" in journal.errors[0][1]


def test_row_provenance_disagreeing_with_its_session_is_refused(journal_copy):
    trades = journal_copy / "donchian_breakout_atr" / "trades.csv"
    text = trades.read_text()
    trades.write_text(text.replace(",paper,", ",broker_live,", 1))
    journal = PaperJournalReader(journal_copy).load()
    assert "donchian_breakout_atr" in {name for name, _ in journal.errors}
    assert "broker_live" in dict(journal.errors)["donchian_breakout_atr"]


def test_no_journal_is_none_not_an_empty_journal(tmp_path):
    assert PaperJournalReader(tmp_path / "missing").load() is None
    (tmp_path / "empty").mkdir()
    (tmp_path / "empty" / "README.md").write_text("not a session")
    assert PaperJournalReader(tmp_path / "empty").load() is None


# -------------------------------------------------------------- the seed


def test_seed_rows_never_carry_an_executed_provenance():
    assert not (SEED_PROVENANCES & EXECUTED)
    trades, positions = generate(["a", "b", "c"], ["EURUSD", "GBPUSD"])
    assert trades and positions
    assert {t.provenance for t in trades} <= SEED_PROVENANCES
    assert {p.provenance for p in positions} <= SEED_PROVENANCES
    # Still a MIXED table, which is what the UI work needs to exercise.
    assert len({t.provenance for t in trades}) > 1


# ------------------------------------------------------------- platform


def test_platform_with_journal_serves_paper_rows(tmp_path, monkeypatch):
    platform = _platform(tmp_path, FIXTURE, monkeypatch)
    assert platform.data_source == "live"
    trades, total = platform.trades(limit=10_000)
    assert total == 283
    assert {t.provenance for t in trades} == {Provenance.PAPER}
    positions = platform.positions()
    assert len(positions) == 1
    assert positions[0].provenance is Provenance.PAPER
    record = next(s for s in platform.data_sources() if s.name == "trade_and_position_records")
    assert record.kind == "live" and record.healthy
    assert "283 closed trade(s), 1 open position(s)" in record.detail


def test_platform_without_journal_falls_back_to_seed_and_never_labels_it_paper(
    tmp_path, monkeypatch
):
    platform = _platform(tmp_path, None, monkeypatch)
    assert platform.paper_root == tmp_path / "state" / "paper"
    assert platform.data_source == "seed"
    trades, _ = platform.trades(limit=10_000)
    assert trades
    assert not ({t.provenance for t in trades} & EXECUTED)
    assert not ({p.provenance for p in platform.positions()} & EXECUTED)
    assert platform.account() is None
    record = next(s for s in platform.data_sources() if s.name == "trade_and_position_records")
    assert record.kind == "seed" and not record.healthy


def test_unreadable_journal_is_absent_not_seed(tmp_path, monkeypatch):
    root = tmp_path / "paper" / "broken"
    root.mkdir(parents=True)
    (root / "summary.json").write_text("{not json")
    platform = _platform(tmp_path, tmp_path / "paper", monkeypatch)
    assert platform.data_source == "absent"
    assert platform.trades()[1] == 0
    assert platform.positions() == []
    assert any("could not be read" in w for w in platform.journal_warnings())


def test_paper_root_is_read_from_settings(tmp_path):
    """``FIBOKI_PAPER_ROOT`` is parsed by ``load_settings`` like every other
    variable; the platform never reaches into ``os.environ`` itself."""
    settings = load_settings(
        {"FIBOKI_STATE_DIR": str(tmp_path / "state"), "FIBOKI_PAPER_ROOT": str(FIXTURE)}
    )
    assert settings.paper_root == FIXTURE
    platform = Platform(settings)
    assert platform.paper_root == FIXTURE
    assert platform.data_source == "live"


def test_journal_is_reloaded_when_a_session_appears(tmp_path, monkeypatch):
    root = tmp_path / "paper"
    root.mkdir()
    platform = _platform(tmp_path, root, monkeypatch)
    assert platform.data_source == "seed"
    shutil.copytree(FIXTURE / "donchian_breakout_atr", root / "donchian_breakout_atr")
    assert platform.data_source == "live"
    assert platform.trades()[1] == 5


@pytest.mark.skipif(not REAL_SESSIONS.is_dir(), reason="research/reports/paper_sessions absent")
def test_the_committed_report_directory_parses_identically():
    """The fixture is a verbatim copy; the source it was copied from still parses."""
    live = PaperJournalReader(REAL_SESSIONS).load()
    fixture = PaperJournalReader(FIXTURE).load()
    assert live is not None and fixture is not None
    assert live.errors == ()
    assert len(live.trades) == len(fixture.trades) == 283
    assert len(live.positions) == len(fixture.positions) == 1
