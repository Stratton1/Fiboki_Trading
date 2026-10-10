"""The paper-forward state snapshot: open paper positions survive a restart.

The defect
----------
The forward runtime kept its position book, its venue position manager and
its paper venue in memory. Every restart (a deploy, a reboot, a crash) lost
them: positions open at the last bar were journalled as
``positions_abandoned_at_restart``, their P&L was never realised, and the
forward record stopped describing what an account would show. On 2026-10-02
that lost an open EURUSD short.

Snapshot, not replay
--------------------
Two designs were possible. A deterministic REPLAY would rebuild the state by
re-running every bar since the session began. It needs every input of every
decision reproduced exactly: the bars (OANDA's history, which the journal does
not store), the live quoted spread the gateway's ``abnormal_spread`` check
read, the wall-clock ages ``data_freshness`` and ``stale_price`` measured, the
kill-switch state at each instant. Those were live readings; a replay would
re-take them under different conditions and could reach different decisions,
which is the opposite of what a restart must do.

A SNAPSHOT records the decided state instead. After every completed worker
cycle (and at start-up and shutdown) the runtime writes ONE CRC-framed record,
atomically (temporary file, ``F_FULLFSYNC``, rename, directory flush;
:func:`fiboki.core.durable.write_record_atomic`), holding:

* the book: balance, realised P&L, cost totals, the sequence counter that keys
  the fill simulator's RNG, every open position with its ladder, trail,
  breakeven and bar-count state, pending entries and reversal closes,
  cooldowns;
* the manager's timeline (bar index, the previous-bar times the staleness test
  reads, the last bar per instrument) and both pictures of every position
  (what the venue holds per broker reference, the dealt entry, the entries the
  venue filled that the book has not yet modelled, the closes the venue has
  not confirmed);
* the paper venue: its positions keyed by broker reference, its reference
  prefix and counter, the client references it has dealt, its marks;
* the feed: the newest bar delivered per instrument and the first bar of the
  window it held, so nothing is delivered twice and every indicator is
  recomputed over the same bars;
* the forward loop's bar index and the account's peak equity.

It is stamped with the wiring's sha256, the strategy content hashes, the
limits version and :data:`SNAPSHOT_SCHEMA`, and with a WATERMARK: the byte
lengths of ``trades.jsonl`` and ``intents.jsonl`` when it was written.

What start-up does with it (:func:`assess`)
-------------------------------------------
``restore``   the record verifies, every stamp matches the wiring being
              started, and the ledgers have not grown since it was written.
              The runtime is loaded from it: nothing is abandoned, and the
              journal says ``positions_restored_at_restart``.
``absent``    no snapshot (a first start, or the first start of this code).
``corrupt``   the CRC or the shape does not verify. CRITICAL alert.
``mismatch``  the wiring, a strategy hash, the limits version or the schema
              differs: the positions were opened under a configuration that
              is no longer the one running, and carrying them into it would
              mix two strategies' records.
``stale``     the ledgers grew after the snapshot was written: the previous
              process died part-way through a cycle, after recording a fill or
              a closed trade the snapshot does not contain. Restoring would
              re-close a recorded trade or forget a dealt one.

Every outcome but ``restore`` and ``absent`` journals ``state_snapshot_refused``
with the reason, alerts, and keeps the refused file beside the ledger
(``state_snapshot.json.refused-<UTC>``) for inspection. The runtime then falls
back to the previous, honest behaviour: positions open at the last journalled
bar are recorded as ``positions_abandoned_at_restart`` and alerted.
"""
from __future__ import annotations

import json
import os
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pandas as pd

from fiboki.core.durable import FrameError, read_payloads, read_record, write_record_atomic

__all__ = [
    "SNAPSHOT_FILE",
    "SNAPSHOT_SCHEMA",
    "SnapshotAssessment",
    "assess",
    "quarantine",
    "stamps",
    "watermark",
    "write",
]

#: The snapshot format this module writes and restores. A different schema is
#: refused, never half-read.
SNAPSHOT_SCHEMA = "fiboki.paper_forward.state/1"
#: Beside the ledgers, in ``<state_dir>/<ledger_dir>/``.
SNAPSHOT_FILE = "state_snapshot.json"


def watermark(trades_path: Path, intents_path: Path) -> dict[str, int]:
    """Byte lengths of the two ledgers whose records a restore must agree with.

    Both are append-only and every append is flushed before the cycle moves
    on, so a ledger longer than the snapshot says is a record the snapshot
    does not contain. A torn final record is quarantined (truncated back) by
    :func:`~fiboki.core.durable.read_payloads` before this is compared, so a
    write the dead process never finished does not count.
    """
    return {
        "trades_bytes": trades_path.stat().st_size if trades_path.exists() else 0,
        "intents_bytes": intents_path.stat().st_size if intents_path.exists() else 0,
    }


def write(path: Path, body: Mapping[str, Any]) -> None:
    write_record_atomic(path, json.dumps(body, sort_keys=True, separators=(",", ":")))


@dataclass(frozen=True, slots=True)
class SnapshotAssessment:
    """What start-up found, and whether it may restore from it."""

    status: str  # restore | absent | corrupt | mismatch | stale
    reason: str = ""
    body: Mapping[str, Any] | None = None

    @property
    def restorable(self) -> bool:
        return self.status == "restore"

    @property
    def state(self) -> Mapping[str, Any]:
        if self.body is None:
            raise ValueError("no snapshot body")
        return self.body["state"]

    def summary(self) -> dict[str, Any]:
        body = self.body or {}
        return {
            "status": self.status,
            "reason": self.reason,
            "written_at": body.get("written_at"),
            "last_bar": body.get("last_bar"),
            "process": body.get("process"),
        }


def stamps(
    *, wiring_sha256: str, strategy_hashes: list[str], limits_version: str
) -> dict[str, Any]:
    """The fields every snapshot carries and start-up compares."""
    return {
        "schema": SNAPSHOT_SCHEMA,
        "wiring_sha256": wiring_sha256,
        "strategy_content_hashes": list(strategy_hashes),
        "limits_version": limits_version,
    }


def assess(
    path: Path,
    *,
    wiring_sha256: str,
    strategy_hashes: list[str],
    limits_version: str,
    trades_path: Path,
    intents_path: Path,
) -> SnapshotAssessment:
    """Decide, before anything is built, whether start-up may restore."""
    if not path.exists():
        return SnapshotAssessment("absent", "no state snapshot on disk")
    try:
        body = json.loads(read_record(path))
        if not isinstance(body, dict) or not isinstance(body.get("state"), dict):
            raise FrameError("snapshot is not an object with a state")
    except (FrameError, ValueError, OSError) as exc:
        return SnapshotAssessment("corrupt", f"snapshot failed verification: {exc}")

    expected = stamps(
        wiring_sha256=wiring_sha256,
        strategy_hashes=strategy_hashes,
        limits_version=limits_version,
    )
    for key, want in expected.items():
        got = body.get(key)
        if got != want:
            return SnapshotAssessment(
                "mismatch",
                f"{key} is {got!r} in the snapshot but {want!r} in the wiring being "
                "started; positions opened under one configuration are not carried "
                "into another",
                body,
            )

    # Quarantine any torn tail first, so a record the dead process never
    # finished writing is not mistaken for one the snapshot is missing.
    read_payloads(trades_path)
    read_payloads(intents_path)
    now = watermark(trades_path, intents_path)
    was = body.get("watermark") or {}
    if now != was:
        return SnapshotAssessment(
            "stale",
            f"the ledgers grew after the snapshot was written (snapshot {was}, now {now}): "
            "the previous process died part-way through a cycle, and restoring would "
            "re-close a recorded trade or forget a dealt fill",
            body,
        )
    return SnapshotAssessment("restore", "", body)


def quarantine(path: Path, clock: Callable[[], pd.Timestamp]) -> Path | None:
    """Keep a refused snapshot for inspection; the next write starts afresh."""
    if not path.exists():
        return None
    target = path.with_name(f"{path.name}.refused-{clock():%Y%m%dT%H%M%S%fZ}")
    os.replace(path, target)
    return target
