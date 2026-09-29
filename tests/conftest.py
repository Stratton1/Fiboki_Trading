"""Shared pytest fixtures."""
from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest

from fiboki.core.contracts import Trade
from fiboki.core.enums import Direction, ExitReason, Provenance
from fiboki.data.schema import BarDatasetMetadata

# Data-platform helpers live in tests/data_fixtures.py as plain functions; they
# are re-exported here so they can also be used as fixtures.
from tests.data_fixtures import make_bars, make_metadata

_EPOCH = pd.Timestamp("2023-01-02 00:00", tz="UTC")


@pytest.fixture(autouse=True)
def _isolated_state_dir(tmp_path_factory: pytest.TempPathFactory, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Point ``FIBOKI_STATE_DIR`` at a fresh temporary directory for every test.

    Without this a test that resolves the state directory from the environment
    falls back to the relative ``./var`` and would read (or append to) the
    operator's real ``var/killswitch.jsonl`` and other ledgers. Tests that
    exercise the "unset" path call ``monkeypatch.delenv("FIBOKI_STATE_DIR")``
    themselves; that still works because this fixture uses ``monkeypatch``.
    """
    state = tmp_path_factory.mktemp("fiboki_state")
    monkeypatch.setenv("FIBOKI_STATE_DIR", str(state))
    return state


@pytest.fixture
def trade_factory():
    """Build a closed :class:`Trade` with a deterministic entry/exit window.

    ``trade_factory(net_pnl, index=i, bars_held=n)`` places the i-th trade i
    hours after a fixed epoch and holds it for ``bars_held`` hourly bars, so a
    sequence of them produces overlapping label spans.
    """

    def _make(
        net_pnl: float,
        *,
        index: int = 0,
        bars_held: int = 6,
        instrument: str = "EURUSD",
        direction: Direction = Direction.LONG,
        size: float = 10_000.0,
        entry_price: float = 1.1000,
        bar_minutes: int = 60,
        strategy_id: str = "test",
        exit_reason: ExitReason = ExitReason.TAKE_PROFIT,
        account_ccy: str = "GBP",
    ) -> Trade:
        entry_time = _EPOCH + pd.Timedelta(minutes=bar_minutes * index)
        exit_time = entry_time + pd.Timedelta(minutes=bar_minutes * bars_held)
        exit_price = entry_price + direction.sign * net_pnl / size
        return Trade(
            instrument=instrument,
            direction=direction,
            size=size,
            entry_price=entry_price,
            exit_price=exit_price,
            entry_time=entry_time,
            exit_time=exit_time,
            exit_reason=exit_reason,
            gross_pnl=net_pnl,
            spread_cost=0.0,
            commission=0.0,
            slippage_cost=0.0,
            financing_cost=0.0,
            net_pnl=net_pnl,
            account_ccy=account_ccy,
            strategy_id=strategy_id,
            bars_held=bars_held,
            provenance=Provenance.BACKTEST,
        )

    return _make


@pytest.fixture
def clean_bars() -> pd.DataFrame:
    return make_bars()


@pytest.fixture
def bar_metadata():
    def _make(frame: pd.DataFrame, **kwargs: object) -> BarDatasetMetadata:
        return make_metadata(frame, **kwargs)

    return _make


@pytest.fixture
def store(tmp_path: Path):
    from fiboki.data.store import DataStore

    s = DataStore.initialise(tmp_path / "datastore")
    yield s
    s.close()
