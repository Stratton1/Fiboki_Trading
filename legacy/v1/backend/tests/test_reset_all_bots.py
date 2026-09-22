"""Test bulk bot reset clears positions and returns all bots to monitoring."""

from fibokei.db.database import get_engine, get_session_factory, init_db
from fibokei.db.repository import get_paper_bot, reset_all_bots, save_paper_bot


def test_reset_all_bots_clears_positions(tmp_path):
    engine = get_engine(f"sqlite:///{tmp_path}/reset.db")
    init_db(engine)
    factory = get_session_factory(engine)
    with factory() as s:
        save_paper_bot(s, {
            "bot_id": "b1", "strategy_id": "bot06_nwave", "instrument": "DE40",
            "timeframe": "M15", "risk_pct": 1.0, "source_type": "manual",
            "state": "position_open",
        })
        b = get_paper_bot(s, "b1")
        b.position_json = {"trade_id": "x", "direction": "LONG"}
        b.bars_seen = 1310
        s.commit()

        n = reset_all_bots(s)
        assert n == 1
        b2 = get_paper_bot(s, "b1")
        assert b2.state == "monitoring"
        assert b2.position_json is None
        assert b2.last_evaluated_bar is None
        assert b2.bars_seen == 0
