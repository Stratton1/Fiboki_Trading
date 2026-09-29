"""GET /api/stream: the SSE contract the workstation's live store is built on.

The stream is read through the WHOLE application (both HTTP middlewares, the
auth dependency, sse-starlette) with a minimal ASGI harness, because
Starlette's TestClient buffers a response to completion and an event stream
never completes. The harness reads the first N events and then sends
``http.disconnect``, exactly as a browser closing the tab does.

The hub is given a fake clock whose ``sleep`` advances time instantly, so the
5 s heartbeat cadence is asserted exactly and the suite does not wait on it.
"""
from __future__ import annotations

import asyncio
import json
import subprocess
import sys
from datetime import UTC, datetime, timedelta
from itertools import pairwise
from typing import Any

import pandas as pd

from fiboki.api.provenance import Figure
from fiboki.api.routers import stream as stream_mod
from fiboki.api.routers.stream import MarkCoalescer, StreamHub
from fiboki.core.enums import Provenance
from fiboki.risk.killswitch import KillSwitchMode
from tests.api.conftest import ORIGIN

T0 = datetime(2026, 9, 29, 12, 0, 0, tzinfo=UTC)


class FakeClock:
    """``sleep`` advances time and yields; nothing waits on the wall clock."""

    def __init__(self) -> None:
        self.t = 1_000.0

    def now(self) -> datetime:
        return T0 + timedelta(seconds=self.t - 1_000.0)

    def monotonic(self) -> float:
        return self.t

    async def sleep(self, seconds: float) -> None:
        self.t += seconds
        await asyncio.sleep(0)


def _parse(block: str) -> dict[str, Any] | None:
    out: dict[str, Any] = {}
    comments = []
    for line in block.split("\n"):
        if not line:
            continue
        if line.startswith(":"):
            comments.append(line[1:].strip())
            continue
        name, _, value = line.partition(":")
        out[name] = value[1:] if value.startswith(" ") else value
    if "data" in out:
        out["data"] = json.loads(out["data"])
    if comments and not out:
        return {"comment": comments[0]}
    return out


class SSEConnection:
    """One streaming GET through the full ASGI app."""

    def __init__(self, app: Any, *, query: str = "", headers: dict[str, str]) -> None:
        self.app = app
        self.query = query
        self.headers = headers
        self.status: int | None = None
        self.response_headers: dict[str, str] = {}
        self._chunks: asyncio.Queue[bytes | None] = asyncio.Queue()
        self._disconnect = asyncio.Event()
        self._buffer = ""
        self._task: asyncio.Task[None] | None = None
        self.ended = False

    async def __aenter__(self) -> SSEConnection:
        async def receive() -> dict[str, Any]:
            await self._disconnect.wait()
            return {"type": "http.disconnect"}

        async def send(message: dict[str, Any]) -> None:
            if message["type"] == "http.response.start":
                self.status = message["status"]
                self.response_headers = {
                    k.decode().lower(): v.decode() for k, v in message.get("headers", [])
                }
            elif message["type"] == "http.response.body":
                await self._chunks.put(message.get("body", b""))
                if not message.get("more_body", False):
                    await self._chunks.put(None)

        scope = {
            "type": "http",
            "asgi": {"version": "3.0"},
            "http_version": "1.1",
            "method": "GET",
            "scheme": "https",
            "path": "/api/stream",
            "raw_path": b"/api/stream",
            "query_string": self.query.encode(),
            "root_path": "",
            "headers": [(b"host", b"workstation.fiboki.test")]
            + [(k.lower().encode(), v.encode()) for k, v in self.headers.items()],
            "client": ("127.0.0.1", 50000),
            "server": ("workstation.fiboki.test", 443),
        }
        self._task = asyncio.create_task(self.app(scope, receive, send))
        return self

    async def next_event(self, *, timeout: float = 20.0) -> dict[str, Any] | None:
        """The next non-comment event, or ``None`` when the stream ended."""
        while True:
            while "\n\n" in self._buffer:
                block, self._buffer = self._buffer.split("\n\n", 1)
                parsed = _parse(block)
                if parsed and "event" in parsed:
                    return parsed
            if self.ended:
                return None
            chunk = await asyncio.wait_for(self._chunks.get(), timeout)
            if chunk is None:
                self.ended = True
                continue
            self._buffer += chunk.decode().replace("\r\n", "\n")

    async def take(self, n: int) -> list[dict[str, Any]]:
        out = []
        for _ in range(n):
            event = await self.next_event()
            if event is None:
                break
            out.append(event)
        return out

    async def until(self, predicate, *, limit: int = 400) -> list[dict[str, Any]]:
        seen = []
        for _ in range(limit):
            event = await self.next_event()
            if event is None:
                break
            seen.append(event)
            if predicate(event):
                return seen
        raise AssertionError(f"condition not met in {len(seen)} events: {seen[-3:]}")

    async def __aexit__(self, *exc: object) -> None:
        self._disconnect.set()
        if self._task is not None:
            await asyncio.wait_for(self._task, 10)


def _cookie_header(client) -> dict[str, str]:
    return {"cookie": "; ".join(f"{k}={v}" for k, v in client.cookies.items())}


def _hub(app, **kwargs) -> StreamHub:
    hub = StreamHub(app, clock=kwargs.pop("clock", FakeClock()), seq_base=0, **kwargs)
    app.state.stream_hub = hub
    return hub


def _arm(app, mode=KillSwitchMode.PAUSE):
    app.state.platform.kill_switch.activate(
        mode,
        operator="tom",
        reason="another operator armed it",
        at=pd.Timestamp("2026-09-29T12:00:00Z"),
        positions_open=0,
    )


# ------------------------------------------------------------------ auth


def test_stream_refuses_a_request_without_a_session(client):
    response = client.get("/api/stream")
    assert response.status_code == 401
    assert response.json()["code"] == "not_authenticated"


def test_stream_refuses_a_foreign_origin_even_with_a_session(admin_client):
    response = admin_client.get("/api/stream", headers={"Origin": "https://evil.example"})
    assert response.status_code == 403
    assert response.json()["code"] == "origin_not_allowed"


def test_stream_refuses_an_unknown_topic(admin_client):
    response = admin_client.get("/api/stream?topics=mode,orders")
    assert response.status_code == 400
    assert response.json()["code"] == "unknown_stream_topic"


def test_a_viewer_may_read_the_stream(client):
    from tests.api.conftest import VIEWER_PW, login

    assert login(client, "guest", VIEWER_PW).status_code == 200
    _hub(client.app)

    async def run():
        async with SSEConnection(
            client.app, query="topics=mode", headers=_cookie_header(client)
        ) as conn:
            events = await conn.take(2)
            return conn.status, events

    status, events = asyncio.run(run())
    assert status == 200
    assert [e["event"] for e in events] == ["mode.snapshot", "heartbeat.heartbeat"]


# ------------------------------------------------------- snapshot, delta


def test_snapshot_then_delta_with_the_envelope_from_the_plan(admin_client):
    app = admin_client.app
    _hub(app)

    async def run():
        async with SSEConnection(
            app,
            query="topics=mode,killswitch",
            headers={**_cookie_header(admin_client), "Origin": ORIGIN},
        ) as conn:
            first = await conn.take(3)
            _arm(app)  # an append to the journal, as another process would
            rest = await conn.until(lambda e: e["event"] == "killswitch.delta")
            return conn, first, rest

    conn, first, rest = asyncio.run(run())
    assert conn.status == 200
    assert conn.response_headers["content-type"].startswith("text/event-stream")
    assert conn.response_headers["cache-control"] == "no-cache"
    assert conn.response_headers["x-accel-buffering"] == "no"

    assert [e["event"] for e in first] == [
        "mode.snapshot",
        "killswitch.snapshot",
        "heartbeat.heartbeat",
    ]
    snap = first[1]
    envelope = snap["data"]
    assert set(envelope) == {"topic", "seq", "kind", "as_of", "source", "data"}
    assert snap["id"] == f"killswitch:{envelope['seq']}"
    assert envelope["kind"] == "snapshot"
    assert envelope["source"]["kind"] == "live"
    entity = envelope["data"]["entities"]["kill_switch"]
    assert entity["active"] is False
    # Numbers inside an event are Figures with provenance.
    assert entity["open_positions"]["provenance"] in {p.value for p in Provenance}
    assert envelope["data"]["count"]["unit"] == "count"

    delta = rest[-1]
    body = delta["data"]
    assert body["kind"] == "delta"
    assert body["seq"] == envelope["seq"] + 1
    assert body["data"]["id"] == "kill_switch"
    assert body["data"]["entity"]["active"] is True
    assert body["data"]["entity"]["mode"] == "pause"
    # The mode topic reflects the same journal replay.
    mode_delta = [e for e in rest if e["event"] == "mode.delta"]
    assert mode_delta and mode_delta[-1]["data"]["data"]["entity"]["kill_switch_active"] is True


def test_positions_snapshot_is_labelled_exactly_as_rest_labels_it(admin_client):
    app = admin_client.app
    _hub(app)
    rest_body = admin_client.get("/api/trading/positions").json()

    async def run():
        async with SSEConnection(
            app, query="topics=positions,fleet,marks", headers=_cookie_header(admin_client)
        ) as conn:
            return await conn.take(3)

    by_topic = {e["event"].split(".")[0]: e for e in asyncio.run(run())}
    positions, fleet, marks = by_topic["positions"], by_topic["fleet"], by_topic["marks"]
    assert positions["event"] == "positions.snapshot"
    # No journal here: REST serves the seed fixture and says "seed"; so must the stream.
    assert positions["data"]["source"]["kind"] == rest_body["source"]["kind"] == "seed"
    entities = positions["data"]["data"]["entities"]
    assert set(entities) == {row["position_id"] for row in rest_body["items"]}
    assert fleet["data"]["source"]["kind"] == "absent"
    assert fleet["data"]["data"]["entities"] == {}
    assert marks["data"]["source"]["kind"] == "absent"


def test_a_closed_position_is_a_tombstone(tmp_path, api_env, monkeypatch):
    import shutil
    from pathlib import Path

    from fastapi.testclient import TestClient

    from fiboki.api.app import create_app
    from fiboki.api.settings import load_settings
    from tests.api.conftest import ADMIN_PW, login

    fixture = Path(__file__).resolve().parents[1] / "fixtures" / "paper_journal"
    root = tmp_path / "paper"
    shutil.copytree(fixture, root)
    env = {**api_env, "FIBOKI_PAPER_ROOT": str(root)}
    app = create_app(load_settings(env), configure_logs=False)
    with TestClient(app, base_url=ORIGIN) as client:
        assert login(client, "joe", ADMIN_PW).status_code == 200
        _hub(app)

        async def run():
            async with SSEConnection(
                app, query="topics=positions", headers=_cookie_header(client)
            ) as conn:
                snap = (await conn.take(1))[0]
                (root / "donchian_breakout_atr" / "positions.csv").write_text("")
                tomb = await conn.until(lambda e: e["event"] == "positions.tombstone")
                return snap, tomb[-1]

        snap, tomb = asyncio.run(run())
    ids = list(snap["data"]["data"]["entities"])
    assert ids == ["pos_517861ad2f634142"]
    assert snap["data"]["source"]["kind"] == "live"
    assert tomb["data"]["data"] == {"id": "pos_517861ad2f634142"}
    assert tomb["data"]["seq"] == snap["data"]["seq"] + 1


# ---------------------------------------------------------------- replay


def test_last_event_id_replays_the_gap_instead_of_resnapshotting(admin_client):
    app = admin_client.app
    _hub(app)
    headers = _cookie_header(admin_client)

    async def run():
        async with SSEConnection(app, query="topics=killswitch", headers=headers) as keeper:
            await keeper.take(2)  # keeps the hub sampling while A is away
            async with SSEConnection(app, query="topics=killswitch", headers=headers) as a:
                got = await a.take(2)
            last_id = got[-1]["id"]
            _arm(app)
            seen = await keeper.until(lambda e: e["event"] == "killswitch.delta")
            async with SSEConnection(
                app,
                query="topics=killswitch",
                headers={**headers, "Last-Event-ID": last_id},
            ) as again:
                resumed = await again.take(2)
            return seen[-1], resumed

    published, resumed = asyncio.run(run())
    kinds = [e["event"] for e in resumed]
    assert "killswitch.snapshot" not in kinds, kinds
    assert resumed[0]["event"] == "killswitch.delta"
    assert resumed[0]["id"] == published["id"]
    assert resumed[0]["data"]["data"]["entity"]["active"] is True


def test_an_unknown_last_event_id_gets_a_fresh_snapshot(admin_client):
    app = admin_client.app
    _hub(app)

    async def run():
        async with SSEConnection(
            app,
            query="topics=killswitch",
            headers={**_cookie_header(admin_client), "Last-Event-ID": "killswitch:999999"},
        ) as conn:
            return await conn.take(1)

    (event,) = asyncio.run(run())
    assert event["event"] == "killswitch.snapshot"


def test_an_aged_out_cut_gets_a_snapshot_for_that_topic_only(api_env, monkeypatch):
    """Hub level: the ring for one topic is too short to cover the gap."""
    monkeypatch.setattr(stream_mod, "RING_SIZE", 2)
    from fiboki.api.app import create_app
    from fiboki.api.settings import load_settings

    app = create_app(load_settings(api_env), configure_logs=False)
    hub = _hub(app)
    source = {"kind": "live", "detail": "test", "as_of": None}

    async def run():
        first, _ = await hub.subscribe(["mode", "killswitch"])
        hub.unsubscribe(first)
        cut = hub._publish("mode", "delta", {"id": "mode", "entity": {}}, as_of=T0, source=source)
        for n in range(3):  # three killswitch events push the ring past the cut
            hub._publish(
                "killswitch", "delta", {"id": "k", "entity": {"n": n}}, as_of=T0, source=source
            )
        hub._publish("mode", "delta", {"id": "mode", "entity": {"x": 1}}, as_of=T0, source=source)
        sub, initial = await hub.subscribe(["mode", "killswitch"], cut.id)
        hub.unsubscribe(sub)
        return initial

    initial = asyncio.run(run())
    kinds = [(e.topic, e.kind) for e in initial]
    assert ("mode", "delta") in kinds and ("mode", "snapshot") not in kinds
    assert ("killswitch", "snapshot") in kinds and ("killswitch", "delta") not in kinds


def test_sequence_numbers_start_from_a_per_process_base(api_env):
    from fiboki.api.app import create_app
    from fiboki.api.settings import load_settings

    app = create_app(load_settings(api_env), configure_logs=False)
    hub = StreamHub(app)
    assert hub.seq_base > 1_700_000_000_000  # epoch ms, so a restart cannot collide
    assert all(t.seq == hub.seq_base for t in hub.topics.values())


# ------------------------------------------------------------- heartbeat


def test_heartbeat_every_five_seconds_with_worker_age_mode_and_topics(admin_client):
    app = admin_client.app
    clock = FakeClock()
    _hub(app, clock=clock)

    async def run():
        async with SSEConnection(
            app, query="topics=mode,health", headers=_cookie_header(admin_client)
        ) as conn:
            events = []
            while len([e for e in events if e["event"] == "heartbeat.heartbeat"]) < 4:
                events.append(await conn.next_event())
            return events

    events = asyncio.run(run())
    beats = [e["data"] for e in events if e["event"] == "heartbeat.heartbeat"]
    times = [datetime.fromisoformat(b["data"]["server_time"].replace("Z", "+00:00")) for b in beats]
    gaps = [(b - a).total_seconds() for a, b in pairwise(times)]
    assert gaps == [5.0, 5.0, 5.0]
    body = beats[-1]["data"]
    age = body["worker_heartbeat_age_s"]
    # No worker has ever beaten here: the age is missing, never 0.
    assert age["value"] is None and age["unit"] == "s" and age["provenance"] == "paper"
    assert body["worker_state"] == "absent"
    assert body["mode"] == "paper"
    assert body["kill_switch"] == {"active": False, "mode": None}
    assert set(body["topics"]) == set(stream_mod.TOPICS)
    assert body["topics"]["health"]["seq"] == 0
    assert body["topics"]["health"]["as_of"] is not None
    assert body["topics"]["marks"]["source_kind"] == "absent"


def test_health_is_re_evaluated_on_the_timer_not_per_subscriber(admin_client, monkeypatch):
    app = admin_client.app
    clock = FakeClock()
    _hub(app, clock=clock)
    calls: list[float] = []
    real = stream_mod.build_health

    def counting(platform, settings):
        calls.append(clock.t)
        return real(platform, settings)

    monkeypatch.setattr(stream_mod, "build_health", counting)

    async def run():
        headers = _cookie_header(admin_client)
        async with SSEConnection(app, query="topics=health", headers=headers) as a:
            await a.take(1)
            async with SSEConnection(app, query="topics=health", headers=headers) as b:
                await b.take(1)
            beats = 0
            while beats < 7:  # 30 s of fake time
                event = await a.next_event()
                beats += event["event"] == "heartbeat.heartbeat"

    asyncio.run(run())
    # One read at first subscribe (the second subscriber reuses it), then one per
    # 15 s of fake time: never one per request.
    assert 2 <= len(calls) <= 4, calls
    assert all(b - a >= 15.0 for a, b in pairwise(calls))


def test_a_revoked_session_ends_the_stream_at_the_next_heartbeat(admin_client):
    app = admin_client.app
    _hub(app)

    async def run():
        async with SSEConnection(
            app, query="topics=mode", headers=_cookie_header(admin_client)
        ) as conn:
            await conn.take(2)
            app.state.sessions.revoke_user("joe")
            tail = []
            while (event := await conn.next_event()) is not None:
                tail.append(event)
            return tail

    tail = asyncio.run(run())
    assert all(e["event"] != "heartbeat.heartbeat" for e in tail)


# ----------------------------------------------------------- topology rule


def test_the_stream_never_starts_a_worker(admin_client, monkeypatch):
    import multiprocessing

    from fiboki.workers import base as worker_base

    started: list[str] = []

    def refuse(name):
        def _refused(*args, **kwargs):
            started.append(name)
            raise AssertionError(f"the stream started a worker via {name}")

        return _refused

    monkeypatch.setattr(worker_base.Worker, "__init__", refuse("Worker.__init__"))
    monkeypatch.setattr(worker_base.Worker, "run", refuse("Worker.run"))
    monkeypatch.setattr(worker_base.WorkerStore, "__init__", refuse("WorkerStore.__init__"))
    monkeypatch.setattr(worker_base.WorkerLease, "__init__", refuse("WorkerLease.__init__"))
    monkeypatch.setattr(subprocess.Popen, "__init__", refuse("subprocess.Popen"))
    monkeypatch.setattr(multiprocessing.Process, "start", refuse("Process.start"))
    before = {m for m in sys.modules if m.startswith("fiboki.workers")}

    app = admin_client.app
    _hub(app)

    async def run():
        async with SSEConnection(app, query="", headers=_cookie_header(admin_client)) as conn:
            events = await conn.take(len(stream_mod.TOPICS) + 3)
        return events

    events = asyncio.run(run())
    assert started == []
    assert len(events) == len(stream_mod.TOPICS) + 3
    new = {m for m in sys.modules if m.startswith("fiboki.workers")} - before
    assert new <= {"fiboki.workers", "fiboki.workers.base"}, new
    # The hub stops sampling once nobody is subscribed.
    assert app.state.stream_hub.subscriber_count == 0


# -------------------------------------------------------------- coalescing


def test_marks_coalesce_to_four_hertz_per_instrument_and_keep_the_latest():
    coalescer = MarkCoalescer()
    published: list[tuple[float, float]] = []
    for i in range(20):  # 20 Hz for one second
        now = i * 0.05
        ready = coalescer.offer("EURUSD", {"p": float(i)}, now)
        if ready is not None:
            published.append((now, ready["p"]))
        for _, payload in coalescer.due(now):
            published.append((now, payload["p"]))
    for _, payload in coalescer.due(1.25):
        published.append((1.25, payload["p"]))
    times = [t for t, _ in published]
    assert all(b - a >= 0.25 - 1e-9 for a, b in pairwise(times))
    assert len(published) <= 6
    assert published[-1][1] == 19.0, "the last price must never be dropped"


def test_marks_are_independent_per_instrument():
    coalescer = MarkCoalescer()
    assert coalescer.offer("EURUSD", {"p": 1}, 0.0) is not None
    assert coalescer.offer("GBPUSD", {"p": 2}, 0.01) is not None
    assert coalescer.offer("EURUSD", {"p": 3}, 0.02) is None


def test_publish_mark_reaches_subscribers_through_the_coalescer(api_env):
    from fiboki.api.app import create_app
    from fiboki.api.settings import load_settings

    app = create_app(load_settings(api_env), configure_logs=False)
    clock = FakeClock()
    hub = _hub(app, clock=clock)

    async def run():
        sub, initial = await hub.subscribe(["marks"])
        for i in range(5):
            hub.publish_mark("EURUSD", Figure(value=1.1 + i / 1000, provenance=Provenance.PAPER))
            clock.t += 0.05
        hub.unsubscribe(sub)
        return initial, [e for e in sub.buffer if e.topic == "marks"]

    initial, marks = asyncio.run(run())
    assert initial[0].source["kind"] == "absent"
    assert len(marks) == 1
    assert marks[0].data["entity"]["price"]["provenance"] == "paper"
    assert hub.topics["marks"].source["kind"] == "live"
