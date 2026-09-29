"""Point-in-time positioning data: who is long and short, as first observed.

    store         append-only SQLite snapshots; ``PositioningStore.as_of`` is the
                  point-in-time read (``observed_at <= when``, nothing else)
    oanda_books   OANDA v20 position and order books (read-only; availability
                  not established, see the module docstring)
    myfxbook      Myfxbook Community Outlook via the official API (personal use)
    recorder      ``PositioningRecorder.poll_once(now)``

CFTC Commitments of Traders is a weekly REPORT with a published release rule,
so it lives with the macro providers (``data/providers/cftc_cot.py``) and uses
their ``available_at`` model rather than first-seen snapshots.
"""
from __future__ import annotations

from fiboki.data.positioning.myfxbook import MyfxbookOutlookClient
from fiboki.data.positioning.oanda_books import OandaBooksClient
from fiboki.data.positioning.recorder import (
    PositioningPoll,
    PositioningRecorder,
    positioning_clients_from_env,
)
from fiboki.data.positioning.store import (
    PositioningSnapshot,
    PositioningStore,
    PositioningStoreError,
    StoredSnapshot,
    default_positioning_path,
)

__all__ = [
    "MyfxbookOutlookClient",
    "OandaBooksClient",
    "PositioningPoll",
    "PositioningRecorder",
    "PositioningSnapshot",
    "PositioningStore",
    "PositioningStoreError",
    "StoredSnapshot",
    "default_positioning_path",
    "positioning_clients_from_env",
]
