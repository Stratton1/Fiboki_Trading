"""The deterministic half of the event channel: store, policy, source, shadow report.

Nothing here consults a model. The annotations are hand-written drafts, which
is exactly what the policy sees in production: data in a quarantined table.
"""
from __future__ import annotations

import ast
import json
import sqlite3
from dataclasses import replace
from datetime import timedelta
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pandas as pd
import pytest

from fiboki.core.contracts import EventAnnotation
from fiboki.marketstate.calendar import EconomicEvent, ImpactLevel, InMemoryEconomicCalendar
from fiboki.marketstate.events import (
    ANNOTATION_POLICY_VERSION,
    DEFAULT_EVENT_VETO_POLICY,
    AnnotationStore,
    AnnotationStoreError,
    EventVetoPolicy,
    EventVetoSource,
    VolSpikeFilter,
    instrument_buckets,
    shadow_report,
)
from tests.event_fixtures import AT, annotation_store, draft

SRC = Path(__file__).resolve().parents[2] / "src" / "fiboki"
PREREG = Path(__file__).resolve().parents[2] / "research" / "preregistration" / "event_veto_v1.json"


def _one(tmp_path: Path, **overrides) -> EventAnnotation:
    store = annotation_store(tmp_path / "a.sqlite", (draft(**overrides),))
    (ann,) = store.annotations()
    store.close()
    return ann


def _veto(ann: EventAnnotation, instrument: str, at: pd.Timestamp = AT, policy=None):
    return (policy or DEFAULT_EVENT_VETO_POLICY).veto_among((ann,), instrument, at)


# ----------------------------------------------------------- the mapping


@pytest.mark.parametrize(
    ("symbol", "expected"),
    [
        ("EURUSD", {"EUR", "USD"}),
        ("USDJPY", {"USD", "JPY"}),
        ("EURGBP", {"EUR", "GBP"}),
        ("AUDNZD", {"AUD", "NZD"}),
        ("XAUUSD", {"XAU", "USD"}),
        ("XAGUSD", {"XAG", "USD"}),
        ("WTIUSD", {"OIL", "USD"}),
        ("BCOUSD", {"OIL", "USD"}),
        ("US500", {"INDEX_US", "USD"}),
        ("UK100", {"INDEX_UK", "GBP"}),
        ("DE40", {"INDEX_EU", "EUR"}),
        ("JP225", {"INDEX_JP", "JPY"}),
        ("AU200", {"AUD"}),
        ("HK50", set()),
    ],
)
def test_instrument_bucket_mapping_golden(symbol: str, expected: set[str]) -> None:
    assert instrument_buckets(symbol) == frozenset(expected)


def test_an_unknown_instrument_is_refused_not_guessed() -> None:
    with pytest.raises(KeyError):
        instrument_buckets("NOTREAL")


# ------------------------------------------------------ golden veto cases


def test_a_severe_unscheduled_usd_event_vetoes_usd_exposure_only(tmp_path) -> None:
    ann = _one(tmp_path)
    for sym in ("EURUSD", "USDJPY", "XAUUSD", "US500", "WTIUSD"):
        reason = _veto(ann, sym)
        assert reason is not None, sym
        assert reason.buckets == ("USD",)
        assert reason.code == f"geopolitical:USD:sev3:{ann.annotation_id}"
        assert reason.policy_version == DEFAULT_EVENT_VETO_POLICY.version
    for sym in ("EURGBP", "AUDNZD", "HK50", "DE40"):
        assert _veto(ann, sym) is None, sym


@pytest.mark.parametrize(
    ("overrides", "vetoes"),
    [
        ({"severity": 1}, False),
        ({"severity": 2}, True),  # the threshold is inclusive
        ({"confidence": 0.59}, False),
        ({"confidence": 0.6}, True),
        ({"scheduled": True}, False),
        ({"event_type": "none"}, False),
        ({"currencies": ()}, False),
        ({"currencies": ("INDEX_US",)}, False),  # EURUSD is not an index
    ],
)
def test_threshold_golden_cases_on_eurusd(tmp_path, overrides, vetoes) -> None:
    assert (_veto(_one(tmp_path, **overrides), "EURUSD") is not None) is vetoes


def test_require_unscheduled_can_be_switched_off(tmp_path) -> None:
    ann = _one(tmp_path, scheduled=True)
    assert _veto(ann, "EURUSD") is None
    lenient = EventVetoPolicy(require_unscheduled=False)
    assert _veto(ann, "EURUSD", policy=lenient) is not None


def test_the_veto_is_point_in_time_on_available_at(tmp_path) -> None:
    """An annotation written after the decision instant does not exist yet."""
    ann = _one(
        tmp_path,
        observed_at=(AT - pd.Timedelta(minutes=30)).to_pydatetime(),
        available_at=(AT + pd.Timedelta(seconds=1)).to_pydatetime(),
    )
    assert _veto(ann, "EURUSD", AT) is None
    assert _veto(ann, "EURUSD", AT + pd.Timedelta(seconds=1)) is not None


def test_the_window_is_half_open_after_observed_at(tmp_path) -> None:
    observed = AT - pd.Timedelta(hours=2)
    ann = _one(
        tmp_path,
        observed_at=observed.to_pydatetime(),
        available_at=observed.to_pydatetime(),
    )
    assert _veto(ann, "EURUSD", observed) is not None
    assert _veto(ann, "EURUSD", observed + pd.Timedelta(hours=2) - pd.Timedelta(seconds=1))
    assert _veto(ann, "EURUSD", observed + pd.Timedelta(hours=2)) is None
    assert _veto(ann, "EURUSD", observed - pd.Timedelta(seconds=1)) is None


def test_the_most_severe_then_most_confident_annotation_is_named(tmp_path) -> None:
    store = annotation_store(
        tmp_path / "a.sqlite",
        (
            draft(severity=2, confidence=0.99, source_ids=("h1",)),
            draft(severity=3, confidence=0.7, source_ids=("h2",)),
            draft(severity=3, confidence=0.8, source_ids=("h3",)),
        ),
    )
    anns = store.annotations()
    reason = DEFAULT_EVENT_VETO_POLICY.veto_among(anns, "EURUSD", AT)
    chosen = next(a for a in anns if a.annotation_id == reason.annotation_id)
    assert chosen.source_ids == ("h3",)
    # Deterministic: the same inputs in any order give the same answer.
    again = DEFAULT_EVENT_VETO_POLICY.veto_among(tuple(reversed(anns)), "EURUSD", AT)
    assert again == reason


def test_policy_defaults_are_the_pre_registered_ones_and_disabled() -> None:
    p = DEFAULT_EVENT_VETO_POLICY
    assert p.enabled is False
    assert (p.min_severity, p.min_confidence, p.require_unscheduled) == (2, 0.6, True)
    assert p.veto_window == timedelta(hours=2)
    with pytest.raises(ValueError):
        EventVetoPolicy(min_severity=0)
    with pytest.raises(ValueError):
        EventVetoPolicy(min_confidence=1.5)
    with pytest.raises(ValueError):
        EventVetoPolicy(veto_window=timedelta(0))


# ---------------------------------------------------------- the contract


def test_the_contract_refuses_what_the_vocabulary_does_not_contain(tmp_path) -> None:
    ann = _one(tmp_path)
    with pytest.raises(ValueError, match="event_type"):
        replace(ann, event_type="rumour")
    with pytest.raises(ValueError, match="currencies"):
        replace(ann, currencies=("BTC",))
    with pytest.raises(ValueError, match="severity"):
        replace(ann, severity=4)
    with pytest.raises(ValueError, match="confidence"):
        replace(ann, confidence=float("nan"))
    with pytest.raises(ValueError, match="precedes"):
        replace(ann, available_at=ann.observed_at - pd.Timedelta(seconds=1))
    with pytest.raises(ValueError, match="model"):
        replace(ann, model_digest="")


def test_the_contract_carries_no_free_text_price_stop_or_size() -> None:
    fields = set(EventAnnotation.__dataclass_fields__)
    for forbidden in ("rationale", "title", "text", "price", "stop", "size", "summary"):
        assert not any(forbidden in f for f in fields), forbidden


# ------------------------------------------------------------- the store


def test_the_store_is_append_only_in_the_database_itself(tmp_path) -> None:
    path = tmp_path / "a.sqlite"
    annotation_store(path, (draft(),), scans=((AT, "ok"),)).close()
    conn = sqlite3.connect(path)
    for sql in (
        "UPDATE event_annotation SET severity = 0",
        "DELETE FROM event_annotation",
        "UPDATE scan_log SET outcome = 'ok'",
        "DELETE FROM scan_log",
        "INSERT OR REPLACE INTO event_annotation SELECT * FROM event_annotation",
    ):
        with pytest.raises(sqlite3.DatabaseError, match="append-only"):
            conn.execute(sql)
    assert conn.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
    conn.close()


def test_the_rationale_is_quarantined_in_the_store_not_the_contract(tmp_path) -> None:
    store = annotation_store(tmp_path / "a.sqlite", (draft(rationale="Fed surprise cut"),))
    (ann,) = store.annotations()
    assert store.rationale(ann.annotation_id) == "Fed surprise cut"
    assert ann.policy_version == ANNOTATION_POLICY_VERSION
    with pytest.raises(AnnotationStoreError, match="300"):
        store.append((draft(rationale="x" * 301),), batch_digest="b", recorded_by="t")


def test_a_read_only_store_never_creates_a_file(tmp_path) -> None:
    path = tmp_path / "missing" / "a.sqlite"
    with pytest.raises(AnnotationStoreError, match="does not exist"):
        AnnotationStore(path, read_only=True)
    assert not path.exists()
    annotation_store(path).close()
    ro = AnnotationStore(path, read_only=True)
    with pytest.raises(AnnotationStoreError, match="read-only"):
        ro.append((draft(),), batch_digest="b", recorded_by="t")


# ------------------------------------------------ the source: fail-open


class _Alerts:
    def __init__(self) -> None:
        self.sent: list[tuple[str, dict]] = []

    def __call__(self, message: str, context) -> None:
        self.sent.append((message, dict(context)))


def test_a_missing_store_is_no_veto_plus_one_alert(tmp_path) -> None:
    alerts = _Alerts()
    source = EventVetoSource(tmp_path / "nope.sqlite", alert=alerts)
    first = source.assess("EURUSD", AT)
    assert (first.available, first.veto, first.detail) == (False, None, "store_missing")
    assert source.veto_for("EURUSD", AT) is None
    assert len(alerts.sent) == 1, "one alert per transition, not one per decision"
    assert "fail-open" in alerts.sent[0][0]
    assert not (tmp_path / "nope.sqlite").exists()


def test_an_unreadable_store_is_no_veto_plus_an_alert(tmp_path) -> None:
    path = tmp_path / "a.sqlite"
    path.write_bytes(b"this is not a database" * 100)
    alerts = _Alerts()
    verdict = EventVetoSource(path, alert=alerts).assess("EURUSD", AT)
    assert not verdict.available and verdict.veto is None
    assert verdict.detail.startswith("store_unreadable")
    assert len(alerts.sent) == 1


def test_a_stale_pipeline_alerts_but_annotations_on_file_still_count(tmp_path) -> None:
    path = tmp_path / "a.sqlite"
    annotation_store(path, (draft(),), scans=((AT - pd.Timedelta(hours=3), "ok"),)).close()
    alerts = _Alerts()
    verdict = EventVetoSource(path, alert=alerts).assess("EURUSD", AT)
    assert not verdict.available and verdict.detail.startswith("stale:")
    assert verdict.veto is not None
    assert len(alerts.sent) == 1


def test_only_a_successful_scan_counts_as_a_heartbeat(tmp_path) -> None:
    path = tmp_path / "a.sqlite"
    annotation_store(path, scans=((AT - pd.Timedelta(minutes=5), "error"),)).close()
    verdict = EventVetoSource(path, alert=_Alerts()).assess("EURUSD", AT)
    assert verdict.detail == "no_successful_scan_on_file"


def test_a_fresh_store_is_available_and_the_alert_state_resets(tmp_path) -> None:
    path = tmp_path / "a.sqlite"
    store = annotation_store(path, (draft(),), scans=((AT - pd.Timedelta(hours=3), "ok"),))
    alerts = _Alerts()
    source = EventVetoSource(path, alert=alerts)
    assert not source.assess("EURUSD", AT).available
    store.log_scan(
        as_of=(AT - pd.Timedelta(minutes=10)).to_pydatetime(),
        since=(AT - pd.Timedelta(minutes=25)).to_pydatetime(),
        finished_at=(AT - pd.Timedelta(minutes=9)).to_pydatetime(),
        outcome="ok", n_headlines=1, n_batches=1, n_annotations=1, truncated=False,
        workflow_id="wf",
    )
    fresh = source.assess("EURUSD", AT)
    assert fresh.available and fresh.veto is not None and fresh.detail == ""
    # A later relapse alerts again, because the state reset on recovery.
    assert not source.assess("EURUSD", AT + pd.Timedelta(hours=5)).available
    assert len(alerts.sent) == 2


def test_a_scan_finished_after_the_decision_instant_is_not_a_heartbeat(tmp_path) -> None:
    path = tmp_path / "a.sqlite"
    annotation_store(path, scans=((AT, "ok"),)).close()  # finishes at AT + 30s
    assert EventVetoSource(path).assess("EURUSD", AT).detail == "no_successful_scan_on_file"


def test_an_alert_sink_that_raises_does_not_change_the_verdict(tmp_path) -> None:
    def broken(message, context):
        raise RuntimeError("channel down")

    verdict = EventVetoSource(tmp_path / "x.sqlite", alert=broken).assess("EURUSD", AT)
    assert verdict.veto is None and not verdict.available


def test_programming_errors_are_not_swallowed_as_fail_open(tmp_path) -> None:
    source = EventVetoSource(tmp_path / "x.sqlite")
    with pytest.raises(ValueError, match="naive"):
        source.assess("EURUSD", pd.Timestamp("2024-06-03 12:00"))
    with pytest.raises(KeyError):
        source.assess("NOTREAL", AT)


# ------------------------------------------------------ shadow arithmetic


def _trade(tid: str, instrument: str, entry: pd.Timestamp, pnl: float, mae: float):
    return SimpleNamespace(
        trade_id=tid, instrument=instrument, entry_time=entry, net_pnl=pnl,
        max_adverse_excursion=mae,
    )


def test_shadow_report_arithmetic_on_synthetic_trades(tmp_path) -> None:
    """Hand-checked.

    Annotation: USD, sev 3, observed AT-30m, available AT-25m, window to AT+90m.
      t1 EURUSD @AT       -120, MAE -200  -> vetoed (USD, inside window)
      t2 USDJPY @AT+60m    -80, MAE -100  -> vetoed
      t3 EURGBP @AT        +50, MAE  -40  -> kept (no USD)
      t4 EURUSD @AT+3h    +150, MAE  -60  -> kept (window closed at AT+90m)
      t5 EURUSD @AT-28m    +10, MAE  -20  -> kept (annotation not yet available)
    event_veto blocked: n=2, total -200, E=-100, mean|MAE|=150
    event_veto kept:    n=3, total  210, E=  70, mean|MAE|=40
    kept - blocked = 170.
    """
    store = annotation_store(tmp_path / "a.sqlite", (draft(),))
    trades = [
        _trade("t1", "EURUSD", AT, -120.0, -200.0),
        _trade("t2", "USDJPY", AT + pd.Timedelta(minutes=60), -80.0, -100.0),
        _trade("t3", "EURGBP", AT, 50.0, -40.0),
        _trade("t4", "EURUSD", AT + pd.Timedelta(hours=3), 150.0, -60.0),
        _trade("t5", "EURUSD", AT - pd.Timedelta(minutes=28), 10.0, -20.0),
    ]
    report = shadow_report(store.annotations(), trades, min_affected_trades=2)
    ev = report.channel("event_veto")
    assert [r.trade_id for r in report.rows if r.event_veto] == ["t1", "t2"]
    assert (ev.blocked.n, ev.blocked.total_net_pnl) == (2, -200.0)
    assert ev.blocked.net_expectancy == pytest.approx(-100.0)
    assert ev.blocked.mean_adverse_excursion == pytest.approx(150.0)
    assert (ev.kept.n, ev.kept.total_net_pnl) == (3, 210.0)
    assert ev.kept.net_expectancy == pytest.approx(70.0)
    assert ev.kept.mean_adverse_excursion == pytest.approx(40.0)
    assert ev.expectancy_kept_minus_blocked == pytest.approx(170.0)
    assert ev.not_evaluated == 0
    assert report.sufficient
    # No bars and no calendar: both baselines are NOT_EVALUATED, never "kept".
    for name in ("vol_spike_filter", "calendar_only"):
        c = report.channel(name)
        assert (c.blocked.n, c.kept.n, c.not_evaluated) == (0, 0, 5)
        assert c.blocked.net_expectancy is None and c.expectancy_kept_minus_blocked is None
    nv = report.channel("no_veto")
    assert (nv.blocked.n, nv.kept.n, nv.kept.total_net_pnl) == (0, 5, 10.0)
    assert report.policy["event_veto_enabled"] is False


def test_shadow_report_is_insufficient_below_the_floor(tmp_path) -> None:
    store = annotation_store(tmp_path / "a.sqlite", (draft(),))
    report = shadow_report(store.annotations(), [_trade("t1", "EURUSD", AT, -1.0, -1.0)])
    assert report.channel("event_veto").blocked.n == 1
    assert not report.sufficient


def _bars(n: int, spike_last: int, end: pd.Timestamp) -> pd.DataFrame:
    idx = pd.date_range(end=end, periods=n, freq="1h", tz="UTC", name="timestamp")
    ranges = np.full(n, 0.0010)
    ranges[-spike_last:] = 0.0050
    close = np.full(n, 1.1000)
    return pd.DataFrame(
        {"open": close, "high": close + ranges / 2, "low": close - ranges / 2, "close": close},
        index=idx,
    )


def test_the_vol_spike_baseline_reads_only_closed_bars() -> None:
    f = VolSpikeFilter()
    # Last bar stamped AT-1h closes at AT: visible. One spike bar of 5x the
    # usual range: mean TR of the last three = (5+1+1)/3 = 2.33 > 2 x median 1.
    bars = _bars(120, 1, AT - pd.Timedelta(hours=1))
    assert f.flags(bars, AT) is True
    # One hour earlier that bar has not closed, so it is invisible: no spike.
    assert f.flags(bars, AT - pd.Timedelta(hours=1)) is False
    # Nor is a bar that is still forming at AT (stamped AT, closes AT+1h).
    assert f.flags(_bars(121, 1, AT), AT) is False
    assert f.flags(bars.iloc[:0], AT) is None
    assert f.flags(_bars(50, 3, AT), AT + pd.Timedelta(hours=1)) is None  # too little history


def test_the_calendar_only_baseline_uses_the_same_window_after_the_event(tmp_path) -> None:
    event = EconomicEvent(
        event_time=AT - pd.Timedelta(minutes=30), currency="USD", name="NFP",
        impact=ImpactLevel.HIGH, source="test", event_id="nfp",
    )
    cal = InMemoryEconomicCalendar(
        [event], declared_start=AT - pd.Timedelta(days=30), declared_end=AT + pd.Timedelta(days=30)
    )
    trades = [
        _trade("in", "EURUSD", AT, -5.0, -5.0),
        _trade("late", "EURUSD", AT + pd.Timedelta(hours=2), 5.0, -1.0),
        _trade("ccy", "EURGBP", AT, 1.0, -1.0),
        _trade("outside", "EURUSD", AT + pd.Timedelta(days=60), 1.0, -1.0),
    ]
    report = shadow_report((), trades, calendar=cal, min_affected_trades=1)
    by_id = {r.trade_id: r.calendar_only for r in report.rows}
    assert by_id == {"in": True, "late": False, "ccy": False, "outside": None}


# ------------------------------------------------------- pre-registration


def test_the_pre_registration_names_the_policy_the_code_implements() -> None:
    doc = json.loads(PREREG.read_text(encoding="utf-8"))
    p = DEFAULT_EVENT_VETO_POLICY
    assert doc["code"]["policy_version"] == p.version
    assert doc["code"]["annotation_policy_version"] == ANNOTATION_POLICY_VERSION
    c = doc["policy_constants"]
    assert c["enabled"] is p.enabled is False
    assert c["min_severity"] == p.min_severity
    assert c["min_confidence"] == p.min_confidence
    assert c["veto_window_minutes"] * 60 == p.veto_window.total_seconds()
    assert c["require_unscheduled"] == p.require_unscheduled
    assert c["max_annotation_age_minutes"] * 60 == p.max_annotation_age.total_seconds()
    vol = next(b for b in doc["baselines"] if b["name"] == "vol_spike_filter")
    f = VolSpikeFilter()
    assert (vol["version"], vol["short_bars"], vol["long_bars"], vol["multiple"]) == (
        f.version, f.short_bars, f.long_bars, f.multiple
    )
    assert {b["name"] for b in doc["baselines"]} == {"no_veto", "vol_spike_filter", "calendar_only"}
    import inspect

    floor = inspect.signature(shadow_report).parameters["min_affected_trades"].default
    assert doc["minimum_sample"]["min_would_be_vetoed_trades"] == floor
    assert doc["decision_date"].startswith("TO_BE_SET") or pd.Timestamp(doc["decision_date"])
    for key in ("hypothesis", "metrics", "decision_rule", "model_pin"):
        assert doc[key], key


# -------------------------------------------------------------- structure


def _imports(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            found.add(node.module)
        elif isinstance(node, ast.Import):
            found.update(a.name for a in node.names)
    return found


def test_the_event_policy_cannot_reach_sizing_risk_or_execution() -> None:
    """Rank alone would let marketstate import risk; this module must not."""
    for module in _imports(SRC / "marketstate" / "events.py"):
        for banned in ("fiboki.risk", "fiboki.portfolio", "fiboki.broker", "fiboki.agents"):
            assert not module.startswith(banned), module


def test_event_annotation_is_constructed_only_by_the_event_policy_module() -> None:
    """Plan §4: EventAnnotation is consumed only in the event policy."""
    offenders = []
    for path in SRC.rglob("*.py"):
        rel = path.relative_to(SRC).as_posix()
        if rel in ("core/contracts.py", "marketstate/events.py"):
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Name) and node.id == "EventAnnotation":
                offenders.append(rel)
            if isinstance(node, ast.alias) and node.name == "EventAnnotation":
                offenders.append(rel)
    assert not offenders, offenders
