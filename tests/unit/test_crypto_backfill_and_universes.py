"""Crypto backfill into a real store, named universes, campaign selection, the crypto wiring.

The backfill runs end to end against a temporary :class:`DataStore` with the
HTTP client faked (a synthetic hourly series served by start/end), so the RAW,
VALIDATED, RESAMPLED and VALIDATED-H4 versions, their lineage and the resume
are all real. The universes are checked against the E-4 filing they must not
disturb.
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from typing import Any

import pandas as pd
import pytest

from fiboki.core.enums import Timeframe
from fiboki.data.backfill import BackfillRefused
from fiboki.data.crypto_backfill import plan_crypto_backfill, run_crypto_backfill
from fiboki.data.providers.coinbase import CoinbaseCandlesProvider
from fiboki.data.schema import DatasetKind
from fiboki.data.store import DatasetNotFound, DataStore
from fiboki.research.universes import (
    UNIVERSES,
    UnknownUniverse,
    assert_single_venue,
    get_universe,
    retarget,
)
from fiboki.strategy.dsl import StrategyDocument

REPO = Path(__file__).resolve().parents[2]
SEEDS = REPO / "research" / "strategies"


class _Http:
    def __init__(self, frame: pd.DataFrame) -> None:
        self.frame = frame
        self.calls = 0

    def get(self, url: str, *, params: dict[str, Any], headers: dict[str, str]) -> Any:
        self.calls += 1
        gran = int(params["granularity"])
        frame = self.frame if gran == 3600 else self.frame.resample("1D").agg(
            {"open": "first", "high": "max", "low": "min", "close": "last"}).dropna()
        start, end = pd.Timestamp(params["start"]), pd.Timestamp(params["end"])
        rows = [[int(t.timestamp()), r.low, r.high, r.open, r.close, 0.5]
                for t, r in frame.iterrows() if start <= t <= end]
        rows.reverse()

        class R:
            def raise_for_status(self) -> None:
                return None

            def json(self) -> Any:
                return rows

        return R()


def _series(start: str, hours: int) -> pd.DataFrame:
    idx = pd.date_range(start, periods=hours, freq="h", tz="UTC")
    base = pd.Series(range(hours), index=idx, dtype=float) * 0.5 + 30_000.0
    frame = pd.DataFrame({"open": base, "high": base + 20, "low": base - 20, "close": base + 5})
    # A thin-book hour with no trades: no candle at all.
    return frame.drop(idx[100]) if hours > 100 else frame


@pytest.fixture
def store(tmp_path: Path) -> DataStore:
    return DataStore.initialise(tmp_path / "data")


def test_the_plan_refuses_a_cfd_and_derives_h4_from_h1(store: DataStore) -> None:
    with pytest.raises(BackfillRefused, match="not a registered Coinbase product: EURUSD"):
        plan_crypto_backfill(store, ["BTCGBP", "EURUSD"], ["H1"])
    jobs = plan_crypto_backfill(store, ["BTCGBP"], ["H4", "D1"],
                                now=pd.Timestamp("2026-10-10T12:30Z"))
    assert [(j.granularity, j.derive_h4, j.action) for j in jobs] == [
        ("H1", True, "fetch"), ("D1", False, "fetch")]
    assert jobs[0].end == pd.Timestamp("2026-10-10T12:00Z"), "only closed bars"


def test_a_backfill_writes_raw_validated_and_a_derived_h4_then_resumes(store: DataStore) -> None:
    listed = "2026-09-15T00:00Z"
    frame = _series(listed, 24 * 20)
    now = pd.Timestamp("2026-10-05T00:10Z")
    http = _Http(frame)
    provider = CoinbaseCandlesProvider(http_client=http, clock=lambda: now)
    jobs = plan_crypto_backfill(store, ["BTCGBP"], ["H1", "H4", "D1"], "2026-01-01", now=now)
    report = run_crypto_backfill(jobs, provider, store)
    assert [o.status for o in report.outcomes] == ["written", "written"], [
        o.reason for o in report.outcomes]
    h1_out = report.outcomes[0]
    assert h1_out.counters["listed_from"] == int(pd.Timestamp(listed).timestamp()), (
        "the listing probe skipped the empty months before the product existed")
    h1, h1v = store.read_latest("BTCGBP", Timeframe.H1)
    assert len(h1) == 24 * 20 - 1 and h1v.source == "coinbase_exchange_public"
    assert set(h1["price_basis"]) == {"last_as_mid"}
    gaps = h1v.integrity_report["gaps"]
    assert [g["missing_bars"] for g in gaps if not g["expected"]] == [1], "reported, not filled"
    h4, h4v = store.read_latest("BTCGBP", Timeframe.H4)
    assert h4v.kind is DatasetKind.VALIDATED
    assert any(step.operation == "resample" for step in h4v.lineage)
    assert len(h4) == 20 * 6
    d1, _ = store.read_latest("BTCGBP", Timeframe.D1)
    assert len(d1) == 20
    assert report.requests == provider.requests > 0

    # Nothing new has closed: the rerun writes nothing.
    again = run_crypto_backfill(
        plan_crypto_backfill(store, ["BTCGBP"], ["H1", "H4", "D1"], "2026-01-01", now=now),
        provider, store,
    )
    assert {o.job.action for o in again.outcomes} == {"current"}
    assert [o.status for o in again.outcomes] == ["skipped", "skipped"]

    # A day later: a resume appends exactly the new bars and re-derives H4.
    more = _series(listed, 24 * 21)
    later = now + pd.Timedelta(days=1)
    provider2 = CoinbaseCandlesProvider(http_client=_Http(more), clock=lambda: later)
    resumed = run_crypto_backfill(
        plan_crypto_backfill(store, ["BTCGBP"], ["H1", "H4"], "2026-01-01", now=later),
        provider2, store,
    )
    (out,) = resumed.outcomes
    assert out.job.action == "resume" and out.status == "written" and out.new_bars == 24
    h1b, _ = store.read_latest("BTCGBP", Timeframe.H1)
    h4b, _ = store.read_latest("BTCGBP", Timeframe.H4)
    assert len(h1b) == len(h1) + 24 and len(h4b) == len(h4) + 6


def test_a_product_with_no_candles_is_refused_not_stored_empty(store: DataStore) -> None:
    now = pd.Timestamp("2026-10-05T00:10Z")
    provider = CoinbaseCandlesProvider(http_client=_Http(_series("2030-01-01", 10)),
                                       clock=lambda: now)
    jobs = plan_crypto_backfill(store, ["SOLGBP"], ["D1"], "2026-09-01", now=now)
    (out,) = run_crypto_backfill(jobs, provider, store).outcomes
    assert out.status == "refused" and "absence" in out.reason
    with pytest.raises(DatasetNotFound):
        store.read_latest("SOLGBP", Timeframe.D1)


# --------------------------------------------------------------- universes


def test_the_universes_are_named_and_crypto_is_separate() -> None:
    assert set(UNIVERSES) == {"k5_e4_v1", "crypto_core_v1", "crypto_majors_v1"}
    majors = get_universe("crypto_majors_v1")
    assert majors.symbols == ("BTCGBP", "ETHGBP", "SOLGBP", "LINKGBP", "ADAGBP", "DOTGBP")
    assert majors.long_only and majors.profile == "COINBASE_UK_SPOT"
    assert get_universe("CRYPTO_CORE_V1").symbols == ("BTCGBP", "ETHGBP")
    with pytest.raises(UnknownUniverse):
        get_universe("crypto_everything")
    with pytest.raises(ValueError, match="mixes"):
        assert_single_venue(["BTCGBP", "EURUSD"])


def test_k5_e4_v1_is_the_e4_filing_and_has_no_crypto() -> None:
    filed = json.loads((REPO / "research" / "preregistration" / "pooled_universe_e4.json")
                       .read_text())["universe"]["series"]
    assert list(get_universe("k5_e4_v1").symbols) == filed
    assert not {s for s in filed if s.endswith("GBP") and s[:3] in {"BTC", "ETH", "SOL"}}


def test_retarget_keeps_the_rules_and_makes_a_new_candidate() -> None:
    seed = StrategyDocument.from_json((SEEDS / "tsmom_dual_horizon.json").read_text())
    crypto = retarget(seed, "crypto_majors_v1")
    assert crypto.strategy_id == "tsmom_dual_horizon__cmaj1"
    assert crypto.universe == get_universe("crypto_majors_v1").symbols
    assert crypto.parent_strategy_ids == ("tsmom_dual_horizon",)
    assert crypto.content_hash() != seed.content_hash(), "universe is semantic"
    a, b = seed.semantic_payload(), crypto.semantic_payload()
    a.pop("universe"), b.pop("universe")
    assert a == b, "nothing but the universe changed"
    assert crypto.direction == seed.direction, "long-only is the risk layer's job"


def test_every_seed_and_k5_document_retargets_within_the_id_limit() -> None:
    docs = [StrategyDocument.from_json(p.read_text()) for p in sorted(SEEDS.glob("*.json"))]
    docs += [StrategyDocument.from_json(p.read_text())
             for p in sorted((REPO / "research" / "generated").glob("*.json"))
             if p.name != "MANIFEST.json"]
    assert len(docs) >= 106
    for doc in docs:
        assert len(retarget(doc, "crypto_majors_v1").strategy_id) <= 64


def test_the_committed_crypto_paper_document_is_the_retargeted_seed() -> None:
    seed = StrategyDocument.from_json((SEEDS / "donchian_breakout_atr.json").read_text())
    committed = StrategyDocument.from_json(
        (SEEDS / "crypto" / "donchian_breakout_atr__ccore1.json").read_text())
    assert committed == retarget(seed, "crypto_core_v1")


# ---------------------------------------------------------- the campaign CLI


def _campaign(*args: str, tmp: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(REPO / "research" / "run_discovery_campaign.py"),
         "--data-root", str(tmp / "nowhere"), "--out", str(tmp / "out"), *args],
        cwd=REPO, capture_output=True, text=True, timeout=300,
        env={"PYTHONPATH": str(REPO / "src") + ":" + str(REPO), "PATH": "/usr/bin:/bin"},
    )


def test_the_campaign_runner_refuses_ambiguous_or_unknown_universes(tmp_path: Path) -> None:
    both = _campaign("--universe", "crypto_majors_v1", "--instruments", "EURUSD", tmp=tmp_path)
    assert both.returncode == 2 and "not both" in both.stderr
    unknown = _campaign("--universe", "crypto_all", tmp=tmp_path)
    assert unknown.returncode == 2 and "unknown universe" in unknown.stderr
    orphan = _campaign("--retarget-to-universe", tmp=tmp_path)
    assert orphan.returncode == 2 and "needs --universe" in orphan.stderr
    assert not (tmp_path / "out").exists(), "refused before any side effect"


def test_the_crypto_campaign_spec_names_a_selectable_universe() -> None:
    spec = json.loads((REPO / "research" / "campaigns" / "crypto_discovery_c1.json").read_text())
    u = get_universe(spec["universe"])
    assert u.venue == "coinbase" and spec["profile"] == u.profile
    assert spec["stress_profile"] == u.stress_profile
    assert spec["status"].startswith("DRAFT")
    assert "--universe crypto_majors_v1" in spec["commands"]["campaign"]
