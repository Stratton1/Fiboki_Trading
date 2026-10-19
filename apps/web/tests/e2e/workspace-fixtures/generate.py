"""Write the workspace e2e fixtures from the REAL backend.

Run from the repository root:

    PYTHONPATH=src .venv/bin/python apps/web/tests/e2e/workspace-fixtures/generate.py

It builds the API in-process against a throwaway state directory, seeds it
(recorded quotes, headlines, alert rules and a notification), signs in and
writes each route's answer as JSON beside this file. A spec therefore drives
the screens with payloads the API can actually send; nothing here is
hand-shaped. Times are pinned (``NOW``) so the files are reproducible.
"""
from __future__ import annotations

import hashlib
import json
import os
import sys
import tempfile
from datetime import UTC, datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[5]
sys.path.insert(0, str(ROOT / "src"))
OUT = Path(__file__).resolve().parent

from fastapi.testclient import TestClient  # noqa: E402

ORIGIN = "https://workstation.fiboki.test"
NOW = datetime(2026, 10, 19, 9, 0, tzinfo=UTC)


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def write(name: str, payload: object) -> None:
    (OUT / f"{name}.json").write_text(json.dumps(payload, indent=1, sort_keys=True) + "\n", encoding="utf-8")
    print("wrote", name)


def seed_quotes(state: Path) -> None:
    from fiboki.data.recorder import MarketState, QuoteRecord, QuoteRecorder

    rows = []
    walk = {"EURUSD": (1.0850, 0.00004), "GBPUSD": (1.2700, -0.00003), "USDJPY": (149.80, 0.004),
            "USDCHF": (0.8900, -0.00002), "AUDUSD": (0.6600, 0.00001), "XAUUSD": (2650.0, 0.35)}
    for symbol, (start, drift) in walk.items():
        for i in range(0, 24 * 60 + 20, 20):  # a quote every 20 minutes for a day, the last 20 s ago
            t = min(NOW - timedelta(seconds=20), NOW - timedelta(hours=24) + timedelta(minutes=i, seconds=30))
            if symbol == "USDCHF" and t > NOW - timedelta(minutes=15):
                continue  # stale: the session is open but nothing recent
            wiggle = ((i // 20) % 7 - 3) * drift * 0.5
            mid = start + drift * (i // 20) + wiggle
            spread = mid * (0.00002 if symbol != "XAUUSD" else 0.00012)
            if symbol == "GBPUSD" and i >= 24 * 60 - 40:
                spread *= 10  # a wide spread at the end
            rows.append(QuoteRecord(timestamp=t, instrument=symbol, bid=round(mid - spread / 2, 6),
                                    ask=round(mid + spread / 2, 6), provider="oanda_practice_pricing",
                                    market_state=MarketState.OPEN))
    rows.sort(key=lambda r: (r.timestamp, r.instrument))
    with QuoteRecorder(state / "quotes") as rec:
        for r in rows:
            rec.record(r)
    stamp = (NOW - timedelta(seconds=10)).timestamp()
    for seg in (state / "quotes").glob("seg-*"):
        os.utime(seg, (stamp, stamp))


def seed_news(state: Path) -> None:
    from fiboki.data.news.store import HeadlineStore, NewItem, NewsSource, default_store_path

    items = [
        (NewsSource.ECB_RSS, "ECB keeps the deposit rate unchanged", "https://www.ecb.europa.eu/press/1", 300),
        (NewsSource.FED_RSS, "Federal Reserve issues FOMC statement", "https://www.federalreserve.gov/1", 120),
        (NewsSource.BOE_RSS, "Bank of England Monetary Policy Summary", "https://www.bankofengland.co.uk/1", 75),
        (NewsSource.BOJ_RSS, "Minutes of the Monetary Policy Meeting", "https://www.boj.or.jp/1", 40),
        (NewsSource.FINNHUB, "Dollar steadies ahead of US data", "https://finnhub.example/1", 12),
        (NewsSource.SNB_RSS, "SNB quarterly bulletin", "https://www.snb.ch/1", 60 * 30),
    ]
    with HeadlineStore(default_store_path(state)) as store:
        for src, title, url, minutes in items:
            store.record([NewItem(source=src, feed_key=f"{src.value}:main", url=url, title=title, summary=None,
                                  source_item_id=None, vendor_published_at=NOW - timedelta(minutes=minutes + 7),
                                  raw={})], NOW - timedelta(minutes=minutes))
        store.log_poll(NOW - timedelta(seconds=90), NOW - timedelta(seconds=80), {"fed_rss": "ok"})


def seed_alerts(state: Path) -> None:
    from fiboki.core.paths import resolve_paths
    from fiboki.data.recorder import MarketState, QuoteRecord
    from fiboki.obs.price_alerts import PriceAlertStore, RuleSpec

    store = PriceAlertStore(resolve_paths({"FIBOKI_STATE_DIR": str(state)}).price_alerts_db)
    t0 = NOW - timedelta(hours=2)
    store.create("joe", RuleSpec("EURUSD", "cross", 1.0870, mode="rearm", note="range top"), actor="joe", now=t0)
    store.create("joe", RuleSpec("GBPUSD", "below", 1.2690), actor="joe", now=t0)
    store.create("joe", RuleSpec("USDJPY", "above", 152.0, note="intervention zone"), actor="joe", now=t0)

    def q(minutes: int, sym: str, mid: float) -> QuoteRecord:
        return QuoteRecord(timestamp=t0 + timedelta(minutes=minutes), instrument=sym, bid=mid - 0.0001,
                           ask=mid + 0.0001, provider="oanda_practice_pricing", market_state=MarketState.OPEN)

    store.evaluate([q(10, "EURUSD", 1.0860), q(30, "EURUSD", 1.0875), q(50, "GBPUSD", 1.2685),
                    q(70, "EURUSD", 1.0865)], now=NOW - timedelta(minutes=40))
    newest = store.notifications("joe")[-1].seq  # the oldest one: mark it read
    store.mark_read("joe", newest, now=NOW - timedelta(minutes=30))
    store.evaluate([], now=NOW - timedelta(seconds=20))


def pin_clocks() -> None:
    """The routers read the API's clock; pin it so the fixtures are reproducible."""
    import fiboki.api.routers.monitors as monitors

    monitors._now = lambda at=None: NOW  # type: ignore[assignment]
    import fiboki.api.routers.price_alerts as price_alerts

    price_alerts._now = lambda: NOW  # type: ignore[assignment]


def dump(client: TestClient) -> None:
    write("panels", client.get("/api/workspace/panels").json())
    write("presets", client.get("/api/workspace/presets").json())
    write("format_rules_watchlist", client.get("/api/workspace/format-rules/watchlist").json())
    write("watchlists", client.get("/api/markets/watchlists").json())
    write("watchlist_majors_24h", client.get("/api/markets/watchlists/Majors/quotes?window=24h").json())
    write("quotes_fx", client.get("/api/markets/quotes?symbols=EURUSD,GBPUSD,USDJPY,USDCHF,AUDUSD,XAUUSD&window=24h").json())
    write("column_sets_watchlist", client.get("/api/workspace/column-sets/watchlist").json())
    write("news_24h", client.get("/api/markets/news?hours=24").json())
    write("news_eurusd", client.get("/api/markets/news?instrument=EURUSD&hours=24").json())
    week = client.get("/api/markets/calendar?days=7").json()
    write("calendar_7d", week)
    write("calendar_eurusd_high", client.get("/api/markets/calendar?days=14&impact=high&instrument=EURUSD").json())
    first = week["data"]["events"][0]["event_id"]
    write("calendar_event", client.get(f"/api/markets/calendar/events/{first}?instrument=EURUSD").json())
    write("pulse_24h", client.get("/api/markets/pulse?window=24h").json())
    write("format_rules_pulse", client.get("/api/workspace/format-rules/pulse").json())
    write("alert_rules", client.get("/api/alerts/rules").json())
    write("notifications", client.get("/api/alerts/notifications?limit=200").json())
    write("notifications_30", client.get("/api/alerts/notifications?limit=30").json())
    for extra in EXTRA_DUMPS:
        extra(client)


#: Later screens register their dumps here.
EXTRA_DUMPS: list = []


def main() -> None:
    from fiboki.api.app import create_app
    from fiboki.api.settings import load_settings

    with tempfile.TemporaryDirectory() as tmp:
        state = Path(tmp) / "state"
        os.environ["FIBOKI_OPERATORS"] = f"joe:admin:{_sha('pw')}"
        settings = load_settings({
            "FIBOKI_STATE_DIR": str(state),
            "FIBOKI_ALLOWED_ORIGINS": ORIGIN,
            "FIBOKI_COOKIE_SECURE": "false",
            "FIBOKI_SESSION_SECRET": "fixture-secret",
        })
        seed_quotes(state)
        seed_news(state)
        seed_alerts(state)
        pin_clocks()
        app = create_app(settings, configure_logs=False)
        with TestClient(app, base_url=ORIGIN) as client:
            client.post("/api/auth/login", json={"username": "joe", "password": "pw"}, headers={"Origin": ORIGIN})
            dump(client)


if __name__ == "__main__":
    main()
