"""``fiboki alerts price-evaluate --once``: evaluates rules over the recorded quote log, idempotently."""
from __future__ import annotations

from datetime import UTC, datetime, timedelta

from typer.testing import CliRunner

from fiboki.cli import app
from fiboki.core.paths import resolve_paths
from fiboki.data.recorder import MarketState, QuoteRecord, QuoteRecorder
from fiboki.obs.price_alerts import PriceAlertStore, RuleSpec


def test_price_evaluate_once_fires_from_the_log_and_not_twice(tmp_path):
    paths = resolve_paths({"FIBOKI_STATE_DIR": str(tmp_path)})
    now = datetime.now(tz=UTC)
    store = PriceAlertStore(paths.price_alerts_db)
    store.create("joe", RuleSpec("EURUSD", "above", 1.1000), actor="joe", now=now - timedelta(minutes=5))
    with QuoteRecorder(paths.quotes_dir) as rec:
        for i, mid in enumerate((1.0990, 1.1010)):
            rec.record(QuoteRecord(timestamp=now - timedelta(minutes=2 - i), instrument="EURUSD", bid=mid,
                                   ask=mid + 0.0001, provider="oanda_practice_pricing", market_state=MarketState.OPEN))
    runner = CliRunner()
    first = runner.invoke(app, ["alerts", "price-evaluate", "--once", "--state-dir", str(tmp_path)])
    assert first.exit_code == 0, first.output
    assert "fired=1" in first.output and "ALERT joe" in first.output
    again = runner.invoke(app, ["alerts", "price-evaluate", "--once", "--state-dir", str(tmp_path)])
    assert "fired=0" in again.output
    assert store.counts("joe")[0] == 1
    assert store.evaluator_state().source == "fiboki alerts price-evaluate"


def test_price_evaluate_needs_a_mode(tmp_path):
    result = CliRunner().invoke(app, ["alerts", "price-evaluate", "--state-dir", str(tmp_path)])
    assert result.exit_code != 0


def test_the_recorder_hook_evaluates_each_poll_and_never_raises(tmp_path, monkeypatch):
    from fiboki.cli import _PollAlerts

    hook = _PollAlerts(tmp_path / "quotes")
    hook.flush()  # an empty poll still beats
    assert hook.store.evaluator_state().source == "fiboki quotes record"

    def boom(*a, **k):
        raise RuntimeError("disk full")

    monkeypatch.setattr(hook.store, "evaluate", boom)
    hook.add(object())
    hook.flush()  # printed, not raised
    assert hook.buffer == []
