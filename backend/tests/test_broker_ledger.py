"""Wave 1 — broker trade ledger / IG transaction import tests.

Uses a fake IG client so the importer is fully exercised offline. Verifies
PnL parsing, direction from size sign, cash/no-reference skipping, and
idempotent re-import (no duplicates). Temp SQLite.
"""

import pytest

from fibokei.db.database import get_engine, get_session_factory, init_db
from fibokei.db.repository import list_broker_trades
from fibokei.execution.broker_ledger import (
    import_ig_transactions,
    parse_ig_pnl,
)


class FakeIGClient:
    def __init__(self, transactions):
        self._txns = transactions

    def get_transactions(self, from_date, to_date=None, page_size=500):
        return self._txns


SAMPLE = [
    {
        "reference": "SBQLDCAC", "dealId": "DIAAAAXSBQD3EAQ",
        "instrumentName": "Spot Gold", "transactionType": "DEAL",
        "profitAndLoss": "£554.00", "size": "-10", "currency": "GBP",
        "openLevel": "4217.0", "closeLevel": "4188.51",
        "dateUtc": "2026-06-18T23:49:09", "cashTransaction": False,
    },
    {  # cash transaction — must be skipped
        "reference": "CASH1", "transactionType": "DEPO",
        "profitAndLoss": "£0.00", "cashTransaction": True,
    },
    {  # no reference — must be skipped
        "instrumentName": "EUR/USD", "transactionType": "DEAL",
        "profitAndLoss": "-£12.00", "size": "5",
    },
    {
        "reference": "REF2", "instrumentName": "GBP/USD", "transactionType": "DEAL",
        "profitAndLoss": "-£8.50", "size": "3", "currency": "GBP",
        "dateUtc": "2026-06-18T15:30:34", "cashTransaction": False,
    },
]


@pytest.fixture()
def session(tmp_path):
    engine = get_engine(f"sqlite:///{tmp_path}/ledger.db")
    init_db(engine)
    factory = get_session_factory(engine)
    with factory() as s:
        yield s


def test_parse_ig_pnl_variants():
    assert parse_ig_pnl("£554.00") == 554.0
    assert parse_ig_pnl("GBP554.00") == 554.0
    assert parse_ig_pnl("-£10.50") == -10.5
    assert parse_ig_pnl("(£10.50)") == -10.5
    assert parse_ig_pnl("1,234.50") == 1234.5
    assert parse_ig_pnl(42) == 42.0
    assert parse_ig_pnl("") is None
    assert parse_ig_pnl(None) is None


def test_import_creates_and_parses(session):
    client = FakeIGClient(SAMPLE)
    counts = import_ig_transactions(session, client, "2026-06-01", source="ig_demo")
    assert counts["imported"] == 2  # SBQLDCAC + REF2
    assert counts["skipped"] == 2   # cash + no-reference

    rows = list_broker_trades(session, source="ig_demo")
    gold = next(r for r in rows if r.reference == "SBQLDCAC")
    assert gold.pnl == 554.0
    assert gold.direction == "SELL"   # size -10
    assert gold.size == 10.0
    assert gold.deal_id == "DIAAAAXSBQD3EAQ"
    assert gold.instrument_name == "Spot Gold"
    assert gold.close_level == 4188.51


def test_reimport_is_idempotent(session):
    client = FakeIGClient(SAMPLE)
    import_ig_transactions(session, client, "2026-06-01", source="ig_demo")
    counts2 = import_ig_transactions(session, client, "2026-06-01", source="ig_demo")
    assert counts2["imported"] == 0
    assert counts2["updated"] == 2
    rows = list_broker_trades(session, source="ig_demo")
    refs = [r.reference for r in rows]
    assert refs.count("SBQLDCAC") == 1  # no duplicate
    assert len(rows) == 2
