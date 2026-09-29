"""STREAM: one multiplexed Server-Sent Events endpoint for the workstation.

``GET /api/stream?topics=mode,killswitch,health,fleet,positions,marks,incidents,risk``

Envelope (docs/v2/FRONTEND_OVERHAUL_PLAN.md §5, report E §6.2)::

    id: <topic>:<seq>
    event: <topic>.<kind>          kind = snapshot | delta | tombstone | heartbeat
    data: {"topic", "seq", "kind", "as_of", "source": SourceNote, "data": {...}}

* **Snapshot on subscribe**, per topic: ``data = {"entities": {id: entity},
  "count": Figure}``. It carries the topic's CURRENT seq, so its id equals the
  id of the last delta already folded into it: dedupe on ``(event, id)``, not
  on ``id`` alone, and ignore any event whose seq is <= the applied seq.
* **Deltas** are keyed by entity id: ``delta`` = ``{"id", "entity"}`` (a full
  upsert, so applying one twice is harmless); ``tombstone`` = ``{"id"}``.
* **Entities are the REST shapes.** A position is exactly a
  ``PositionRowView`` from ``/api/trading/positions``, the health entity is the
  ``HealthReport`` of ``/api/health``, and so on, so every number inside is the
  same :class:`~fiboki.api.provenance.Figure` the REST route returns, with the
  same named exceptions ``tests/api/test_provenance_contract.py`` allows.
* **Per-topic sequence numbers** start at a per-process base (epoch
  milliseconds at hub creation), so a restarted server can never collide with
  an id from its predecessor: a client sees a gap and resyncs.
* **Ring buffer** of the last :data:`RING_SIZE` events per topic.
  ``Last-Event-ID`` identifies the last event the client applied; because one
  hub publishes every topic in one order, that event is a cut across ALL
  topics, and every event after it is replayed. A topic whose needed events
  have aged out of its ring gets a fresh snapshot instead. The client never
  has to guess.
* **Heartbeat** (``heartbeat.heartbeat``) every :data:`HEARTBEAT_SECONDS`:
  server time, ``worker_heartbeat_age_s`` (a Figure, from the platform's
  ``worker_heartbeat`` TABLE reader, never a file mtime), the execution mode,
  the kill-switch state and every topic's ``seq``/``as_of``, so a connected
  stream over a dead worker is visibly stale. Heartbeats are not replayed.
* ``X-Accel-Buffering: no``, ``Cache-Control: no-cache`` and a comment
  keep-alive every :data:`KEEPALIVE_SECONDS` (sse-starlette's ping).

Sources: mode and kill switch from the settings and a fresh replay of the
kill-switch journal; health from :func:`build_health` on its own timer (never
per request); positions, fleet and risk from the paper-journal reader through
the same functions the REST routes use, so a missing source is ``absent``
exactly as it is over REST; incidents from the incident read model; marks are
pushed by a price feed through :meth:`StreamHub.publish_mark`, coalesced to at
most 4 Hz per instrument. No price feed is composed into the API process yet
(ARCHITECTURE.md §12), so ``marks`` reports ``absent`` until one is.

Topology rule (ARCHITECTURE.md §6): **the stream never starts a worker.** The
hub is one asyncio task inside the API process that READS the stores workers
write; it holds no lease, spawns no process and imports no worker class. It
runs only while at least one client is subscribed.
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
from collections import deque
from collections.abc import AsyncIterator, Callable, Iterable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Annotated, Any, Protocol

import anyio
from fastapi import APIRouter, Depends, Header, Query, Request, status
from sse_starlette.sse import EventSourceResponse, ServerSentEvent

from fiboki.api.errors import ApiError
from fiboki.api.health import build_health
from fiboki.api.provenance import Figure, figure
from fiboki.api.security import Principal, Role, current_principal, origin_of, read_principal

log = logging.getLogger("fiboki.api.stream")

router = APIRouter(prefix="/api", tags=["stream"])

__all__ = [
    "HEARTBEAT_SECONDS",
    "KEEPALIVE_SECONDS",
    "MARK_MIN_INTERVAL_SECONDS",
    "RING_SIZE",
    "SAMPLE_INTERVAL_SECONDS",
    "TOPICS",
    "MarkCoalescer",
    "StreamEvent",
    "StreamHub",
    "hub_for",
    "router",
]

#: Every subscribable topic, in publication order.
TOPICS: tuple[str, ...] = (
    "mode",
    "killswitch",
    "health",
    "fleet",
    "positions",
    "marks",
    "incidents",
    "risk",
)
HEARTBEAT_TOPIC = "heartbeat"
RING_SIZE = 500
HEARTBEAT_SECONDS = 5.0
KEEPALIVE_SECONDS = 15
#: 4 Hz per instrument, at most.
MARK_MIN_INTERVAL_SECONDS = 0.25
#: How often each pulled topic is re-read. Health is re-evaluated on this
#: timer, never per request; risk is re-read every 5 s and published on change
#: (the heartbeat carries its seq/as_of every 5 s either way).
SAMPLE_INTERVAL_SECONDS: dict[str, float] = {
    "mode": 1.0,
    "killswitch": 1.0,
    "health": 15.0,
    "fleet": 5.0,
    "positions": 5.0,
    "incidents": 5.0,
    "risk": 5.0,
}
#: Pushed rather than pulled.
_PUSH_TOPICS = frozenset({"marks"})
TICK_SECONDS = 0.25
RETRY_MS = 3000
#: A subscriber this far behind is disconnected; it resumes with Last-Event-ID.
SUBSCRIBER_BUFFER = 2000
#: Incidents published per snapshot/delta pass, newest first.
MAX_INCIDENT_ENTITIES = 200


def _iso(stamp: datetime | None) -> str | None:
    if stamp is None:
        return None
    return stamp.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _fingerprint(entity: Any) -> str:
    return json.dumps(entity, sort_keys=True, separators=(",", ":"), default=str)


def _dump(model: Any) -> dict[str, Any]:
    return model.model_dump(mode="json")


# ------------------------------------------------------------------ clock


class Clock(Protocol):
    def now(self) -> datetime: ...

    def monotonic(self) -> float: ...

    async def sleep(self, seconds: float) -> None: ...


class SystemClock:
    def now(self) -> datetime:
        return datetime.now(tz=UTC)

    def monotonic(self) -> float:
        return time.monotonic()

    async def sleep(self, seconds: float) -> None:
        await asyncio.sleep(seconds)


# ----------------------------------------------------------------- events


@dataclass(frozen=True, slots=True)
class StreamEvent:
    topic: str
    seq: int
    kind: str
    as_of: datetime | None
    source: dict[str, Any]
    data: dict[str, Any]
    #: Position in the hub's single publication order. -1 for a per-connection
    #: snapshot, which is never in a ring.
    index: int = -1

    @property
    def id(self) -> str:
        return f"{self.topic}:{self.seq}"

    @property
    def event(self) -> str:
        return f"{self.topic}.{self.kind}"

    def payload(self) -> dict[str, Any]:
        return {
            "topic": self.topic,
            "seq": self.seq,
            "kind": self.kind,
            "as_of": _iso(self.as_of),
            "source": self.source,
            "data": self.data,
        }

    def to_sse(self) -> ServerSentEvent:
        return ServerSentEvent(
            data=json.dumps(self.payload(), separators=(",", ":"), default=str),
            event=self.event,
            id=self.id,
        )


@dataclass
class TopicSample:
    entities: dict[str, dict[str, Any]]
    source: dict[str, Any]
    #: Change-detection key per entity; defaults to the entity's canonical JSON.
    identity: dict[str, str] | None = None


@dataclass
class TopicState:
    name: str
    seq: int
    ring: deque[StreamEvent] = field(default_factory=lambda: deque(maxlen=RING_SIZE))
    #: Publication index of the newest event pushed OUT of the ring; -1 = none.
    evicted_upto: int = -1
    entities: dict[str, dict[str, Any]] = field(default_factory=dict)
    identity: dict[str, str] = field(default_factory=dict)
    source: dict[str, Any] = field(default_factory=dict)
    #: When this topic was last read successfully (server clock).
    as_of: datetime | None = None
    sampled: bool = False
    error: str | None = None
    next_due: float = 0.0


@dataclass(eq=False)
class Subscription:
    topics: frozenset[str]
    buffer: deque[StreamEvent] = field(default_factory=deque)
    wake: asyncio.Event = field(default_factory=asyncio.Event)
    overflowed: bool = False

    def push(self, event: StreamEvent) -> None:
        if len(self.buffer) >= SUBSCRIBER_BUFFER:
            self.overflowed = True
        else:
            self.buffer.append(event)
        self.wake.set()


# ------------------------------------------------------------- coalescing


class MarkCoalescer:
    """At most one published mark per instrument per ``min_interval`` seconds.

    The latest value always wins: a mark offered inside the window is held and
    published when the window closes, so the last price is never dropped, only
    the intermediate ones. Pattern after Fincept's ``coalesce_within_ms``
    (described, not copied).
    """

    def __init__(self, min_interval: float = MARK_MIN_INTERVAL_SECONDS) -> None:
        self.min_interval = min_interval
        self._last: dict[str, float] = {}
        self._pending: dict[str, dict[str, Any]] = {}

    def offer(self, instrument: str, payload: dict[str, Any], now: float) -> dict[str, Any] | None:
        last = self._last.get(instrument)
        if last is None or now - last >= self.min_interval:
            self._last[instrument] = now
            self._pending.pop(instrument, None)
            return payload
        self._pending[instrument] = payload
        return None

    def due(self, now: float) -> list[tuple[str, dict[str, Any]]]:
        out: list[tuple[str, dict[str, Any]]] = []
        for instrument in sorted(self._pending):
            if now - self._last.get(instrument, float("-inf")) >= self.min_interval:
                out.append((instrument, self._pending.pop(instrument)))
                self._last[instrument] = now
        return out


# --------------------------------------------------------------- samplers


def _note(kind: str, detail: str, as_of: datetime | None) -> dict[str, Any]:
    return {"kind": kind, "detail": detail, "as_of": _iso(as_of)}


def _count(value: int, provenance: Any, as_of: datetime | None = None) -> dict[str, Any]:
    return _dump(Figure(value=float(value), provenance=provenance, unit="count", as_of=as_of))


def _positions_provenance(platform: Any, settings: Any) -> Any:
    labels = {p.provenance for p in platform.positions()}
    return next(iter(labels)) if len(labels) == 1 else settings.provenance_for_execution()


def sample_mode(platform: Any, settings: Any, state: Any, now: datetime) -> TopicSample:
    from fiboki.api.routers import system as system_router

    banner = _dump(system_router.execution_mode(platform, settings).data)
    ks = platform.kill_switch_replayed()
    banner["kill_switch_active"] = bool(ks.active)
    banner["kill_switch_mode"] = ks.mode.value if ks.mode else None
    banner.pop("as_of", None)
    return TopicSample(
        {"mode": banner},
        _note("live", "Process configuration and a fresh kill-switch journal replay.", now),
    )


def sample_killswitch(platform: Any, settings: Any, state: Any, now: datetime) -> TopicSample:
    ks = platform.kill_switch_replayed()
    since = ks.since.to_pydatetime() if ks.since is not None else None
    entity = {
        "active": bool(ks.active),
        "mode": ks.mode.value if ks.mode else None,
        "operator": ks.operator,
        "reason": ks.reason,
        "since": _iso(since),
        "blocks_new_risk": bool(ks.blocks_new_risk),
        "requires_flatten": bool(ks.requires_flatten),
        "open_positions": _count(
            len(platform.positions()), _positions_provenance(platform, settings)
        ),
    }
    return TopicSample(
        {"kill_switch": entity},
        _note("live", f"Replayed now from {settings.killswitch_path.name}.", now),
    )


def sample_health(platform: Any, settings: Any, state: Any, now: datetime) -> TopicSample:
    report = build_health(platform, settings)
    entity = _dump(report)
    # Ages and latencies move every read; a delta is published when a STATUS
    # changes. The heartbeat carries the live worker age every 5 s.
    identity = _fingerprint(
        {
            "status": report.status,
            "advisory": report.advisory,
            "checks": [(c.name, c.status) for c in report.checks],
        }
    )
    return TopicSample(
        {"health": entity},
        _note("live", "build_health, re-evaluated on the stream's timer.", now),
        identity={"health": identity},
    )


def sample_positions(platform: Any, settings: Any, state: Any, now: datetime) -> TopicSample:
    from fiboki.api.routers import trading as trading_router

    page = trading_router.positions(platform, settings)
    entities = {row.position_id: _dump(row) for row in page.items}
    return TopicSample(entities, _dump(page.source))


def sample_fleet(platform: Any, settings: Any, state: Any, now: datetime) -> TopicSample:
    """One entity per strategy with at least one paper session.

    The seed fixture has no bots, so with no journal the topic is ``absent``,
    never an idle fleet.
    """
    journal = platform.journal
    if journal is None:
        return TopicSample(
            {},
            _note(
                "absent",
                f"No paper journal at {platform.paper_root}; no bot has run, so there "
                "is no fleet to show. This is not an idle fleet.",
                now,
            ),
        )
    if not journal.sessions:
        return TopicSample(
            {},
            _note(
                "absent",
                f"A paper journal exists at {journal.root} but none of its sessions "
                "could be read.",
                now,
            ),
        )
    lifecycle: dict[str, Any] = {}
    try:
        for status_obj in platform.lifecycle.statuses():
            lifecycle[status_obj.strategy_id] = status_obj
    except Exception:  # the fleet is still reportable without the ladder
        lifecycle = {}
    by_strategy: dict[str, list[Any]] = {}
    for session in journal.sessions:
        by_strategy.setdefault(session.strategy_id or "unknown", []).append(session)
    entities: dict[str, dict[str, Any]] = {}
    for strategy_id, sessions in sorted(by_strategy.items()):
        provenances = {s.provenance for s in sessions}
        p = next(iter(provenances)) if len(provenances) == 1 else settings.provenance_for_execution()
        as_of = max((s.as_of for s in sessions if s.as_of is not None), default=None)
        currencies = {s.account_ccy for s in sessions}
        trades = [t for s in sessions for t in s.trades]
        charged = frozenset.intersection(
            *(platform.session_charged_costs(s.session_id) for s in sessions)
        )
        if len(currencies) == 1:
            realised = figure(
                round(sum(t.net_pnl for t in trades), 2),
                p,
                settings=settings,
                unit=next(iter(currencies)),
                as_of=as_of,
                sample_size=len(trades),
                affects="net_pnl",
                charged=charged,
            )
        else:
            realised = Figure.missing(
                p,
                reason="Sessions are in different currencies and no conversion is "
                "applied, so no total is reported.",
            )
        status_obj = lifecycle.get(strategy_id)
        entities[strategy_id] = {
            "strategy_id": strategy_id,
            "provenance": p.value,
            "sessions": sorted(s.session_id for s in sessions),
            "instruments": sorted({s.instrument for s in sessions if s.instrument}),
            "timeframes": sorted({s.timeframe for s in sessions if s.timeframe}),
            "open_positions": _count(sum(len(s.positions) for s in sessions), p, as_of),
            "closed_trades": _count(len(trades), p, as_of),
            "realised_pnl": _dump(realised),
            "lifecycle": status_obj.lifecycle.value if status_obj is not None else None,
            "degraded": bool(status_obj.degraded) if status_obj is not None else None,
            "as_of": _iso(as_of),
        }
    return TopicSample(
        entities,
        _note(
            "live",
            f"{len(journal.sessions)} paper session(s) under {journal.root}; one entity "
            "per strategy. A replay session's state is true at its last replayed bar.",
            journal.as_of,
        ),
    )


def sample_incidents(platform: Any, settings: Any, state: Any, now: datetime) -> TopicSample:
    from fiboki.api.routers.incidents import alert_log_path, load_incidents

    snap = load_incidents(platform, settings, alert_log=alert_log_path(state), now=now)
    entities = {i.id: _dump(i) for i in snap.incidents[:MAX_INCIDENT_ENTITIES]}
    source = _dump(snap.source)
    if snap.caveats:
        source["detail"] += " Caveats: " + "; ".join(c.code for c in snap.caveats) + "."
    return TopicSample(entities, source)


def sample_risk(platform: Any, settings: Any, state: Any, now: datetime) -> TopicSample:
    from fiboki.api.routers import trading as trading_router

    risk = trading_router.risk_state(platform, settings)
    exposure = trading_router.exposure(platform, settings)
    entities: dict[str, dict[str, Any]] = {"state": _dump(risk.data)}
    for row in exposure.items:
        entities[f"exposure:{row.key}"] = _dump(row)
    return TopicSample(entities, _dump(risk.source))


SAMPLERS: dict[str, Callable[[Any, Any, Any, datetime], TopicSample]] = {
    "mode": sample_mode,
    "killswitch": sample_killswitch,
    "health": sample_health,
    "fleet": sample_fleet,
    "positions": sample_positions,
    "incidents": sample_incidents,
    "risk": sample_risk,
}


# -------------------------------------------------------------------- hub


class StreamHub:
    """Per-topic state, ring buffers and fan-out. One per application.

    Every mutation happens on the event loop; blocking reads run in a worker
    THREAD (not a worker process) via ``anyio.to_thread``.
    """

    def __init__(
        self,
        app: Any,
        *,
        clock: Clock | None = None,
        seq_base: int | None = None,
        heartbeat_seconds: float = HEARTBEAT_SECONDS,
        tick_seconds: float = TICK_SECONDS,
    ) -> None:
        self.app = app
        self.clock: Clock = clock or SystemClock()
        base = int(time.time() * 1000) if seq_base is None else int(seq_base)
        self.seq_base = base
        self.heartbeat_seconds = heartbeat_seconds
        self.tick_seconds = tick_seconds
        self.topics: dict[str, TopicState] = {
            name: TopicState(name=name, seq=base) for name in (*TOPICS, HEARTBEAT_TOPIC)
        }
        self._index = 0
        self._subscribers: set[Subscription] = set()
        self._task: asyncio.Task[None] | None = None
        self._next_heartbeat: float | None = None
        self._coalescer = MarkCoalescer()
        self._mark_feed_detail: str | None = None
        marks = self.topics["marks"]
        marks.sampled = True
        marks.source = _note(
            "absent",
            "No price feed is composed into the API process (ARCHITECTURE.md §12); "
            "no mark is streamed. Position marks, where persisted, are on the "
            "positions topic.",
            None,
        )

    # -- accessors ---------------------------------------------------------

    @property
    def platform(self) -> Any:
        return self.app.state.platform

    @property
    def settings(self) -> Any:
        return self.app.state.settings

    @property
    def subscriber_count(self) -> int:
        return len(self._subscribers)

    # -- publication --------------------------------------------------------

    def _publish(
        self,
        topic: str,
        kind: str,
        data: dict[str, Any],
        *,
        as_of: datetime | None,
        source: dict[str, Any],
    ) -> StreamEvent:
        state = self.topics[topic]
        state.seq += 1
        self._index += 1
        event = StreamEvent(topic, state.seq, kind, as_of, source, data, self._index)
        if state.ring.maxlen is not None and len(state.ring) == state.ring.maxlen:
            state.evicted_upto = state.ring[0].index
        state.ring.append(event)
        for sub in list(self._subscribers):
            if topic == HEARTBEAT_TOPIC or topic in sub.topics:
                sub.push(event)
        return event

    def _apply(self, topic: str, sample: TopicSample, now: datetime) -> None:
        state = self.topics[topic]
        identity = sample.identity or {k: _fingerprint(v) for k, v in sample.entities.items()}
        if state.sampled:
            for entity_id in sorted(identity):
                if state.identity.get(entity_id) != identity[entity_id]:
                    self._publish(
                        topic,
                        "delta",
                        {"id": entity_id, "entity": sample.entities[entity_id]},
                        as_of=now,
                        source=sample.source,
                    )
            for entity_id in sorted(set(state.identity) - set(identity)):
                self._publish(
                    topic, "tombstone", {"id": entity_id}, as_of=now, source=sample.source
                )
        state.entities = dict(sample.entities)
        state.identity = identity
        state.source = sample.source
        state.as_of = now
        state.sampled = True
        state.error = None

    def _fail(self, topic: str, exc: BaseException, now: datetime) -> None:
        state = self.topics[topic]
        state.error = f"{type(exc).__name__}: {exc}"
        log.warning("stream_sample_failed", extra={"topic": topic, "error": state.error})
        if not state.sampled:
            state.source = _note(
                "absent", f"The {topic} source could not be read ({type(exc).__name__}).", now
            )
            state.sampled = True

    def publish_mark(
        self, instrument: str, price: Figure, *, detail: str = "price feed"
    ) -> StreamEvent | None:
        """Push one mark. Coalesced to <= 4 Hz per instrument; latest wins."""
        self._mark_feed_detail = detail
        payload = {"instrument": instrument, "price": _dump(price)}
        ready = self._coalescer.offer(instrument, payload, self.clock.monotonic())
        return self._emit_mark(instrument, ready) if ready is not None else None

    def _emit_mark(self, instrument: str, payload: dict[str, Any]) -> StreamEvent | None:
        now = self.clock.now()
        state = self.topics["marks"]
        source = _note("live", self._mark_feed_detail or "price feed", now)
        state.entities[instrument] = payload
        state.identity[instrument] = _fingerprint(payload)
        state.source = source
        state.as_of = now
        return self._publish(
            "marks", "delta", {"id": instrument, "entity": payload}, as_of=now, source=source
        )

    def _heartbeat_data(self, beat: Any, now: datetime) -> dict[str, Any]:
        settings = self.settings
        provenance = settings.provenance_for_execution()
        if beat.age_seconds is None:
            age = Figure.missing(
                provenance, unit="s", reason=f"No worker heartbeat ({beat.reason})."
            )
        else:
            age = Figure(
                value=round(beat.age_seconds, 3),
                provenance=provenance,
                unit="s",
                as_of=beat.newest_beat_at,
                estimated=beat.reason == "mtime_fallback",
            )
        ks = self.topics["killswitch"].entities.get("kill_switch") or {}
        return {
            "server_time": _iso(now),
            "worker_heartbeat_age_s": _dump(age),
            "worker_state": beat.state,
            "worker_reason": beat.reason,
            "worker_stale_after_s": _dump(
                Figure(
                    value=float(settings.worker_heartbeat_stale_seconds),
                    provenance=provenance,
                    unit="s",
                )
            ),
            "mode": settings.execution_mode.value,
            "kill_switch": {"active": ks.get("active"), "mode": ks.get("mode")},
            "topics": {
                name: {
                    "seq": state.seq,
                    "as_of": _iso(state.as_of),
                    "source_kind": state.source.get("kind"),
                    "error": state.error,
                }
                for name, state in self.topics.items()
                if name != HEARTBEAT_TOPIC
            },
        }

    async def _beat(self) -> StreamEvent:
        # Scheduled and stamped BEFORE the read, so a beat requested by a new
        # subscriber and the timer's beat can never both fire, and the cadence
        # is exact rather than "five seconds plus however long the read took".
        now = self.clock.now()
        self._next_heartbeat = self.clock.monotonic() + self.heartbeat_seconds
        beat = await anyio.to_thread.run_sync(self.platform.worker_heartbeat)
        return self._publish(
            HEARTBEAT_TOPIC,
            "heartbeat",
            self._heartbeat_data(beat, now),
            as_of=now,
            source=_note(
                "live",
                f"Stream hub; worker age from the worker_heartbeat table "
                f"({beat.reason}).",
                now,
            ),
        )

    # -- sampling -------------------------------------------------------------

    def _sample_sync(
        self, topics: Iterable[str], now: datetime
    ) -> list[tuple[str, TopicSample | BaseException]]:
        platform, settings, state = self.platform, self.settings, self.app.state
        out: list[tuple[str, TopicSample | BaseException]] = []
        for topic in topics:
            try:
                out.append((topic, SAMPLERS[topic](platform, settings, state, now)))
            except Exception as exc:
                out.append((topic, exc))
        return out

    async def _sample(self, topics: list[str]) -> None:
        if not topics:
            return
        now = self.clock.now()
        results = await anyio.to_thread.run_sync(self._sample_sync, topics, now)
        for topic, result in results:
            if isinstance(result, BaseException):
                self._fail(topic, result, now)
            else:
                self._apply(topic, result, now)

    async def _run(self) -> None:
        try:
            while self._subscribers:
                mono = self.clock.monotonic()
                due = [
                    name
                    for name in TOPICS
                    if name not in _PUSH_TOPICS and self.topics[name].next_due <= mono
                ]
                if due:
                    await self._sample(due)
                    for name in due:
                        self.topics[name].next_due = mono + SAMPLE_INTERVAL_SECONDS[name]
                for instrument, payload in self._coalescer.due(self.clock.monotonic()):
                    self._emit_mark(instrument, payload)
                if self._next_heartbeat is None or self.clock.monotonic() >= self._next_heartbeat:
                    await self._beat()
                await self.clock.sleep(self.tick_seconds)
        except asyncio.CancelledError:  # pragma: no cover - shutdown
            raise
        except Exception:  # pragma: no cover - logged, the next subscriber restarts it
            log.exception("stream_hub_crashed")

    def _ensure_running(self) -> None:
        loop = asyncio.get_running_loop()
        task = self._task
        if task is not None and not task.done() and task.get_loop() is loop:
            return
        self._task = loop.create_task(self._run(), name="fiboki-stream-hub")

    # -- subscription ---------------------------------------------------------

    def _cut_for(self, last_event_id: str | None) -> int | None:
        """Publication index of the event the client last applied, or None."""
        if not last_event_id or ":" not in last_event_id:
            return None
        topic, _, raw_seq = last_event_id.rpartition(":")
        state = self.topics.get(topic)
        if state is None:
            return None
        try:
            seq = int(raw_seq)
        except ValueError:
            return None
        for event in state.ring:
            if event.seq == seq:
                return event.index
        return None

    def _snapshot(self, topic: str) -> StreamEvent:
        state = self.topics[topic]
        provenance = self.settings.provenance_for_execution()
        return StreamEvent(
            topic,
            state.seq,
            "snapshot",
            state.as_of,
            state.source,
            {
                "entities": dict(state.entities),
                "count": _count(len(state.entities), provenance, state.as_of),
            },
        )

    async def subscribe(
        self, topics: Iterable[str], last_event_id: str | None = None
    ) -> tuple[Subscription, list[StreamEvent]]:
        wanted = frozenset(topics)
        unsampled = [t for t in TOPICS if t in wanted and not self.topics[t].sampled]
        await self._sample(unsampled)
        mono = self.clock.monotonic()
        for name in unsampled:
            self.topics[name].next_due = mono + SAMPLE_INTERVAL_SECONDS[name]
        # From here to registration there is no await, so nothing can be
        # published between computing the initial batch and joining the fan-out.
        cut = self._cut_for(last_event_id)
        initial: list[StreamEvent] = []
        for topic in TOPICS:
            if topic not in wanted:
                continue
            state = self.topics[topic]
            if cut is None or state.evicted_upto > cut:
                initial.append(self._snapshot(topic))
            else:
                initial.extend(e for e in state.ring if e.index > cut)
        sub = Subscription(topics=wanted)
        self._subscribers.add(sub)
        self._ensure_running()
        # A fresh heartbeat after the snapshot/replay, so a client knows at once
        # whether the worker behind the data is alive. Published (to everyone),
        # so its id is a valid resume point.
        await self._beat()
        return sub, initial

    def unsubscribe(self, sub: Subscription) -> None:
        self._subscribers.discard(sub)
        sub.wake.set()

    async def events(self, sub: Subscription) -> AsyncIterator[StreamEvent]:
        while True:
            while sub.buffer:
                yield sub.buffer.popleft()
            if sub.overflowed or sub not in self._subscribers:
                return
            sub.wake.clear()
            if sub.buffer:
                continue
            await sub.wake.wait()


def hub_for(app: Any) -> StreamHub:
    hub = getattr(app.state, "stream_hub", None)
    if hub is None:
        hub = StreamHub(app)
        app.state.stream_hub = hub
    return hub


# ------------------------------------------------------------------ route


def stream_principal(
    request: Request, principal: Annotated[Principal, Depends(current_principal)]
) -> Principal:
    """Session cookie, role, and origin, for a GET that browsers send without CSRF.

    ``validate_origin`` passes every safe method, so the stream checks the
    origin itself: an ``Origin`` or ``Referer`` that is present must be on the
    allow-list. Both absent is allowed, because a same-origin ``EventSource``
    through the Next rewrite need not send either; the session cookie is
    ``SameSite=strict`` and httpOnly, so a cross-site page cannot ride it.
    """
    settings = request.app.state.settings
    origin, source = origin_of(request)
    if origin:
        allowed = {a.strip().rstrip("/").lower() for a in settings.allowed_origins if a.strip()}
        if origin not in allowed:
            raise ApiError(
                status.HTTP_403_FORBIDDEN,
                "origin_not_allowed",
                "The requesting origin is not on this deployment's allow-list.",
                context={"origin_header": source},
            )
    if not principal.role.satisfies(Role.VIEWER):  # pragma: no cover - every role does
        raise ApiError(status.HTTP_403_FORBIDDEN, "insufficient_role", "Not permitted.")
    return principal


StreamPrincipal = Annotated[Principal, Depends(stream_principal)]


def parse_topics(raw: str | None) -> list[str]:
    if raw is None or not raw.strip():
        return list(TOPICS)
    wanted = [t.strip().lower() for t in raw.split(",") if t.strip()]
    unknown = sorted(set(wanted) - set(TOPICS))
    if unknown:
        raise ApiError(
            status.HTTP_400_BAD_REQUEST,
            "unknown_stream_topic",
            f"Unknown topic(s) {unknown}. Known topics: {list(TOPICS)}.",
            context={"known": list(TOPICS)},
        )
    return [t for t in TOPICS if t in wanted]


@router.get(
    "/stream",
    response_class=EventSourceResponse,
    responses={
        200: {
            "description": "text/event-stream of topic envelopes; see module docs.",
            "content": {"text/event-stream": {}},
        },
        401: {"description": "No valid session cookie."},
        403: {"description": "Origin/Referer present and not allowed."},
    },
)
async def stream(
    request: Request,
    principal: StreamPrincipal,
    topics: str | None = Query(
        None, description="Comma-separated subset of: " + ",".join(TOPICS)
    ),
    last_event_id: str | None = Header(None, alias="Last-Event-ID"),
) -> EventSourceResponse:
    """The workstation's live feed. Snapshot, then deltas, then heartbeats."""
    wanted = parse_topics(topics)
    hub = hub_for(request.app)
    sub, initial = await hub.subscribe(wanted, last_event_id)
    session_id = principal.session_id

    def still_signed_in() -> bool:
        current = read_principal(request)
        return current is not None and current.session_id == session_id

    async def body() -> AsyncIterator[ServerSentEvent]:
        try:
            yield ServerSentEvent(comment="fiboki stream", retry=RETRY_MS)
            for event in initial:
                yield event.to_sse()
            async for event in hub.events(sub):
                # A revoked or expired session ends the stream at the next beat.
                if event.topic == HEARTBEAT_TOPIC and not still_signed_in():
                    return
                yield event.to_sse()
        finally:
            hub.unsubscribe(sub)

    return EventSourceResponse(
        body(),
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        ping=KEEPALIVE_SECONDS,
    )
