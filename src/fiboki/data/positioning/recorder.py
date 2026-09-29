"""The positioning recorder: poll every configured client, append snapshots.

``PositioningRecorder.poll_once(now)`` is the unit of work, with the same
contract as the headline recorder: ``now`` is passed in and becomes
``observed_at`` for every new row; a client that fails is recorded and the
rest carry on; nothing is retried inside a poll; each client is called at
most once per its ``min_interval_s`` (hourly by default for both OANDA books
and the Myfxbook outlook).
"""
from __future__ import annotations

import os
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from fiboki.data.positioning.myfxbook import (
    ENV_MYFXBOOK_EMAIL,
    ENV_MYFXBOOK_PASSWORD,
    MyfxbookOutlookClient,
)
from fiboki.data.positioning.oanda_books import ENV_OANDA_BOOKS_TOKEN, OandaBooksClient
from fiboki.data.positioning.store import PositioningSnapshot, PositioningStore

__all__ = ["PositioningPoll", "PositioningRecorder", "positioning_clients_from_env"]


def positioning_clients_from_env(
    http_client: Any, *, env: Mapping[str, str] | None = None,
) -> tuple[list[Any], dict[str, str]]:
    """Configured positioning clients, plus a reason for each one that is off."""
    environ = os.environ if env is None else env
    clients: list[Any] = []
    off: dict[str, str] = {}
    if (environ.get(ENV_OANDA_BOOKS_TOKEN) or "").strip():
        clients.append(OandaBooksClient.from_env(http_client, env=environ))
    else:
        off["oanda_books"] = f"{ENV_OANDA_BOOKS_TOKEN} not set"
    if (environ.get(ENV_MYFXBOOK_EMAIL) or "").strip() and environ.get(ENV_MYFXBOOK_PASSWORD):
        clients.append(MyfxbookOutlookClient.from_env(http_client, env=environ))
    else:
        off["myfxbook_outlook"] = f"{ENV_MYFXBOOK_EMAIL} and {ENV_MYFXBOOK_PASSWORD} not both set"
    return clients, off


@dataclass(slots=True)
class PositioningPoll:
    started_at: datetime
    per_client: dict[str, dict[str, Any]] = field(default_factory=dict)

    @property
    def errors(self) -> dict[str, str]:
        return {k: v["error"] for k, v in self.per_client.items() if v.get("error")}

    def to_dict(self) -> dict[str, Any]:
        return {"started_at": self.started_at.isoformat(), "errors": self.errors,
                "per_client": self.per_client}


class PositioningRecorder:
    """Owns a store and clients (``key``, ``min_interval_s``, ``fetch() -> [snapshot]``)."""

    def __init__(self, store: PositioningStore, clients: Iterable[Any], *,
                 disabled: dict[str, str] | None = None) -> None:
        self.store = store
        self.clients = list(clients)
        keys = [c.key for c in self.clients]
        if len(keys) != len(set(keys)):
            raise ValueError(f"duplicate client keys: {sorted(keys)}")
        self.disabled = dict(disabled or {})
        self._last_attempt: dict[str, datetime] = {}

    def poll_once(self, now: datetime) -> PositioningPoll:
        if now.tzinfo is None:
            raise ValueError("poll_once needs an aware UTC 'now'")
        result = PositioningPoll(started_at=now)
        batch: list[tuple[str, list[PositioningSnapshot]]] = []
        for c in self.clients:
            last = self._last_attempt.get(c.key)
            interval = float(getattr(c, "min_interval_s", 0.0) or 0.0)
            if last is not None and (now - last).total_seconds() < interval:
                result.per_client[c.key] = {"skipped": f"min interval {interval:.0f}s"}
                continue
            self._last_attempt[c.key] = now
            try:
                snaps = c.fetch()
            except Exception as exc:  # recorded, never raised
                result.per_client[c.key] = {"error": f"{type(exc).__name__}: {exc}", "fetched": 0}
                continue
            entry: dict[str, Any] = {"fetched": len(snaps)}
            extra = getattr(c, "last_report", None)
            if extra:
                entry.update(dict(extra))
            result.per_client[c.key] = entry
            batch.append((c.key, snaps))
        for key, snaps in batch:
            result.per_client[key].update(self.store.record(snaps, now))
        for name, why in self.disabled.items():
            result.per_client[name] = {"disabled": why}
        self.store.log_poll(now, result.to_dict())
        return result
