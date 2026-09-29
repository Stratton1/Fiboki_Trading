"""Durable, self-verifying append-only JSON-lines files, and SQLite hygiene.

The defects this closes (audit F, P1-6 and P2-14)
-------------------------------------------------
1. **``fsync(2)`` is not durable on macOS.** Apple's ``fsync`` pushes data to
   the drive but not through the drive's write cache; the man page tells a
   caller that needs durability to use ``fcntl(fd, F_FULLFSYNC)``. The intent
   store, the kill-switch journal, the agent audit ledger and the alert
   ``FileChannel`` all used plain ``fsync``, so on the Mac a power cut could
   lose a PENDING intent the code believed was on disk.
2. **A torn final line bricked the loader.** Every loader called
   ``json.loads`` on every line and raised on a partial one, so a crash
   mid-append left a worker that could not start until someone hand-edited a
   ledger.
3. **Creating a file was not durable.** No directory ``fsync`` followed the
   ``touch``, so the directory entry itself could vanish.
4. **SQLite stores ran without WAL or a busy timeout**, so the API, a
   campaign and the continuous agents contending for one file produced
   ``database is locked`` after five seconds.

The line format
---------------
A framed line is the payload JSON object with its CRC32 spliced in as the
FIRST key::

    {"_crc32":"8e2c1f0a","action":"activate",...}

The CRC is computed over the exact UTF-8 bytes of the ORIGINAL payload
(``{"action":"activate",...}``), which the reader reconstructs byte for byte
by removing the prefix. The line therefore stays one valid JSON object, so the
existing JSON-lines readers (the incidents router, ``jq``, backup
verification) keep working unchanged and simply see one extra key, while this
module's reader can prove every line is exactly what was written. The quote
recorder's ``<crc> <len> <json>`` framing (``data/recorder.py``) was
considered and rejected here for that reason alone: it would have broken every
reader outside this module. A CRC over the exact bytes also verifies the
length, so no separate length field is needed.

Legacy (unframed) lines are accepted on read, verified only by parsing as
JSON, so existing ledgers stay readable; new writes are framed; a file may
mix both.

Torn tails
----------
A line that is not newline-terminated, or a final line that fails
verification, is a torn tail: the writer died mid-append. The reader never
raises for it. Under an exclusive ``flock`` (the same lock every
:func:`durable_append` holds, so a record still being written is never mistaken
for a torn one) it copies the torn bytes to ``<file>.torn-<UTC timestamp>``,
fsyncs that copy, truncates the ledger back to its last good record, and fires
every registered torn-tail hook (``obs.alerts`` installs one that raises a
CRITICAL alert). A line that fails verification anywhere EXCEPT the tail is
not a crash artefact: it is corruption or an edit, and
:class:`DurableLogCorrupt` is raised so a human looks at it.

``core`` imports nothing from Fiboki; ``fcntl`` makes this POSIX-only (macOS
and Linux), like ``agents/audit.py``.
"""
from __future__ import annotations

import fcntl
import json
import logging
import os
import sys
import zlib
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

__all__ = [
    "IS_DARWIN",
    "DurableLogCorrupt",
    "FrameError",
    "TornTail",
    "configure_sqlite_connection",
    "decode_line",
    "durable_append",
    "frame_line",
    "fsync_directory",
    "full_fsync",
    "install_sqlite_pragmas",
    "read_payloads",
    "set_torn_tail_hook",
    "touch_durable",
]

_log = logging.getLogger("fiboki.core.durable")

#: Evaluated at import. Tests monkeypatch this module attribute, never
#: ``sys.platform`` itself.
IS_DARWIN: bool = sys.platform == "darwin"

_PREFIX = '{"_crc32":"'
_HEX = 8
_AFTER = len(_PREFIX) + _HEX  # index of the character after the hex digits


class FrameError(ValueError):
    """One line failed verification (CRC mismatch, bad frame, not JSON)."""


class DurableLogCorrupt(ValueError):
    """A line that is NOT the tail failed verification.

    A crash can only tear the last record. Anything earlier that does not
    verify was edited or corrupted after the fact, and silently skipping it
    would make an append-only ledger say something it never said.
    """


@dataclass(frozen=True, slots=True)
class TornTail:
    """What a reader or writer found and did about a torn final record."""

    path: Path
    #: Where the torn bytes were preserved. ``None`` if the quarantine itself
    #: failed (read-only file system); the ledger was then left untouched.
    quarantine_path: Path | None
    bytes_quarantined: int
    reason: str
    detected_at: datetime


# ---------------------------------------------------------------------------
# fsync
# ---------------------------------------------------------------------------


def full_fsync(fd: int) -> None:
    """Flush ``fd`` to stable storage.

    On Darwin this is ``fcntl(fd, F_FULLFSYNC)``: plain ``fsync`` there does
    not flush the drive's write cache. A file system that refuses
    ``F_FULLFSYNC`` (some network and FUSE mounts) falls back to ``fsync``,
    which is the best that mount can offer.
    """
    if IS_DARWIN and hasattr(fcntl, "F_FULLFSYNC"):
        try:
            fcntl.fcntl(fd, fcntl.F_FULLFSYNC)
            return
        except OSError:
            pass
    os.fsync(fd)


def fsync_directory(directory: str | Path) -> None:
    """Make a directory entry (a created, renamed or truncated file) durable."""
    fd = os.open(os.fspath(directory), os.O_RDONLY)
    try:
        full_fsync(fd)
    finally:
        os.close(fd)


# ---------------------------------------------------------------------------
# Framing
# ---------------------------------------------------------------------------


def _crc(payload: bytes) -> str:
    return f"{zlib.crc32(payload) & 0xFFFFFFFF:08x}"


def frame_line(payload: str) -> str:
    """Splice the CRC32 of ``payload`` into it as the first key.

    ``payload`` must be ONE compact JSON object on one line. It is not
    re-serialised: the CRC covers its exact bytes.
    """
    if not payload.startswith("{") or not payload.endswith("}"):
        raise ValueError("durable lines are JSON objects: payload must be '{...}'")
    if "\n" in payload or "\r" in payload:
        raise ValueError("a durable line may not contain a raw newline")
    if payload.startswith(_PREFIX[:-1]):
        raise ValueError("payload is already framed (it starts with the _crc32 key)")
    crc = _crc(payload.encode("utf-8"))
    if payload == "{}":
        return f'{_PREFIX}{crc}"}}'
    body = payload[1:]
    if body[:1].isspace():
        raise ValueError("payload must start '{\"' (no whitespace after the brace)")
    return f'{_PREFIX}{crc}",{body}'


def decode_line(line: str) -> tuple[str, bool]:
    """Return ``(payload, framed)`` or raise :class:`FrameError`.

    A framed line is verified by CRC. A legacy line (written before framing)
    is verified by parsing as JSON, which is all that can be said of it.
    """
    if line.startswith(_PREFIX):
        crc = line[len(_PREFIX) : _AFTER]
        tail = line[_AFTER:]
        if tail == '"}':
            payload = "{}"
        elif tail.startswith('",'):
            payload = "{" + tail[2:]
        else:
            raise FrameError("malformed frame")
        if len(crc) != _HEX or _crc(payload.encode("utf-8")) != crc.lower():
            raise FrameError("crc32 mismatch")
        return payload, True
    try:
        json.loads(line)
    except ValueError as exc:
        raise FrameError(f"not JSON: {exc}") from exc
    return line, False


# ---------------------------------------------------------------------------
# Torn-tail hooks
# ---------------------------------------------------------------------------

_TORN_HOOKS: dict[str, Callable[[TornTail], Any]] = {}


def set_torn_tail_hook(name: str, hook: Callable[[TornTail], Any] | None) -> None:
    """Register (or with ``None`` remove) a named hook fired on every torn tail.

    Keyed by name so rebuilding a dispatcher replaces its hook instead of
    stacking another one.
    """
    if hook is None:
        _TORN_HOOKS.pop(name, None)
    else:
        _TORN_HOOKS[name] = hook


def _emit(torn: TornTail) -> None:
    _log.critical(
        "torn tail in durable ledger",
        extra={
            "ledger": str(torn.path),
            "quarantine": str(torn.quarantine_path) if torn.quarantine_path else None,
            "bytes": torn.bytes_quarantined,
            "reason": torn.reason,
        },
    )
    for name, hook in list(_TORN_HOOKS.items()):
        try:
            hook(torn)
        except Exception:  # a broken hook must not break the loader
            _log.exception("torn-tail hook %s failed", name)


# ---------------------------------------------------------------------------
# Scan, quarantine, append, read
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class _Scan:
    payloads: list[str]
    good_len: int
    torn: bytes | None
    reason: str = ""


def _scan(blob: bytes, path: Path) -> _Scan:
    parts = blob.split(b"\n")
    complete, partial = parts[:-1], parts[-1]
    payloads: list[str] = []
    pos = 0
    for index, raw in enumerate(complete):
        end = pos + len(raw) + 1
        text = raw.strip()
        if not text:
            pos = end
            continue
        try:
            payload, _ = decode_line(text.decode("utf-8"))
        except (FrameError, UnicodeDecodeError) as exc:
            rest_blank = all(not r.strip() for r in complete[index + 1 :])
            if rest_blank and not partial.strip():
                return _Scan(payloads, pos, blob[pos:], f"last record failed verification: {exc}")
            raise DurableLogCorrupt(
                f"{path}: line {index + 1} failed verification ({exc}) and is not the "
                "last record, so it is not a crash artefact. Refusing to read past it."
            ) from exc
        payloads.append(payload)
        pos = end
    if partial:
        return _Scan(payloads, pos, blob[pos:], "last record is not newline-terminated")
    return _Scan(payloads, pos, None)


@contextmanager
def _locked(path: Path, flags: int) -> Iterator[int]:
    fd = os.open(os.fspath(path), flags, 0o644)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        try:
            yield fd
        finally:
            fcntl.flock(fd, fcntl.LOCK_UN)
    finally:
        os.close(fd)


def _read_fd(fd: int) -> bytes:
    size = os.fstat(fd).st_size
    chunks: list[bytes] = []
    offset = 0
    while offset < size:
        chunk = os.pread(fd, min(1 << 20, size - offset), offset)
        if not chunk:
            break
        chunks.append(chunk)
        offset += len(chunk)
    return b"".join(chunks)


def _quarantine(path: Path, fd: int, scan: _Scan) -> TornTail:
    """Lock held. Preserve the torn bytes, then cut the ledger back to good."""
    torn = scan.torn or b""
    now = datetime.now(tz=UTC)
    candidate = path.with_name(f"{path.name}.torn-{now.strftime('%Y%m%dT%H%M%S%fZ')}")
    target: Path | None = candidate
    try:
        qfd = os.open(os.fspath(candidate), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o644)
        try:
            view = memoryview(torn)
            while view:
                view = view[os.write(qfd, view) :]
            full_fsync(qfd)
        finally:
            os.close(qfd)
        os.ftruncate(fd, scan.good_len)
        full_fsync(fd)
        fsync_directory(path.parent)
    except OSError as exc:
        _log.critical("could not quarantine torn tail of %s: %s", path, exc)
        target = None
    result = TornTail(path, target, len(torn), scan.reason, now)
    _emit(result)
    return result


def _tail_is_clean(fd: int, size: int) -> bool:
    """Does the file end with a complete, verifiable record? Lock held."""
    window = min(size, 1 << 16)
    tail = os.pread(fd, window, size - window)
    if not tail.endswith(b"\n"):
        return False
    body = tail[:-1]
    cut = body.rfind(b"\n")
    if cut < 0 and window < size:
        return True  # a single record longer than the window: trust the newline
    last = body[cut + 1 :].strip()
    if not last:
        return True
    try:
        decode_line(last.decode("utf-8"))
    except (FrameError, UnicodeDecodeError):
        return False
    return True


def durable_append(path: str | Path, line: str, *, frame: bool = True) -> None:
    """Append ONE record and do not return until it is on stable storage.

    Holds an exclusive ``flock`` on the file for the whole append, so readers
    in other processes never see half a record as a torn tail. A torn tail
    left by a previous crash is quarantined BEFORE this record is written,
    so a new record never lands glued to the end of a broken one.
    """
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    text = frame_line(line) if frame else line
    if "\n" in text:
        raise ValueError("a durable record is one line")
    data = (text + "\n").encode("utf-8")
    created = not target.exists()
    with _locked(target, os.O_RDWR | os.O_CREAT | os.O_APPEND) as fd:
        size = os.fstat(fd).st_size
        if size and not _tail_is_clean(fd, size):
            scan = _scan(_read_fd(fd), target)
            if scan.torn is not None:
                _quarantine(target, fd, scan)
        view = memoryview(data)
        while view:
            view = view[os.write(fd, view) :]
        full_fsync(fd)
    if created:
        fsync_directory(target.parent)


def touch_durable(path: str | Path) -> None:
    """Create ``path`` if missing and make its directory entry durable."""
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists():
        return
    fd = os.open(os.fspath(target), os.O_WRONLY | os.O_CREAT, 0o644)
    try:
        full_fsync(fd)
    finally:
        os.close(fd)
    fsync_directory(target.parent)


def read_payloads(path: str | Path, *, quarantine: bool = True) -> list[str]:
    """Every verified record's JSON payload, oldest first. Never raises for a torn tail.

    A missing file is an empty ledger. A torn final record is quarantined
    (see the module docstring) and omitted; corruption anywhere else raises
    :class:`DurableLogCorrupt`.
    """
    target = Path(path)
    if not target.exists():
        return []
    blob = target.read_bytes()
    scan = _scan(blob, target)
    if scan.torn is None:
        return scan.payloads
    if not quarantine:
        _emit(TornTail(target, None, len(scan.torn), scan.reason, datetime.now(tz=UTC)))
        return scan.payloads
    try:
        with _locked(target, os.O_RDWR) as fd:
            # Re-read under the writers' lock: what looked torn may have been
            # a record another process was still writing.
            scan = _scan(_read_fd(fd), target)
            if scan.torn is not None:
                _quarantine(target, fd, scan)
    except OSError as exc:
        # Read-only file or directory: report, skip the torn record, carry on.
        _log.critical("could not lock %s to quarantine its torn tail: %s", target, exc)
        _emit(TornTail(target, None, len(scan.torn or b""), scan.reason, datetime.now(tz=UTC)))
    return scan.payloads


# ---------------------------------------------------------------------------
# SQLite
# ---------------------------------------------------------------------------

_SYNCHRONOUS = frozenset({"OFF", "NORMAL", "FULL", "EXTRA"})

#: Seconds a writer waits for a lock before ``database is locked``.
SQLITE_BUSY_TIMEOUT_MS = 30_000


def configure_sqlite_connection(
    dbapi_conn: Any,
    *,
    synchronous: str = "FULL",
    busy_timeout_ms: int = SQLITE_BUSY_TIMEOUT_MS,
) -> None:
    """WAL, a busy timeout and an explicit ``synchronous`` on ONE connection.

    ``busy_timeout`` and ``synchronous`` are per-connection settings, which is
    why this runs on every connection rather than once per database: applying
    them to one pooled connection (``workers/base.py`` today) leaves every
    other connection at the library defaults.
    """
    level = synchronous.upper()
    if level not in _SYNCHRONOUS:
        raise ValueError(f"synchronous must be one of {sorted(_SYNCHRONOUS)}")
    cursor = dbapi_conn.cursor()
    try:
        cursor.execute(f"PRAGMA busy_timeout={int(busy_timeout_ms)}")
        # Returns 'memory' for an in-memory database, which is harmless.
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute(f"PRAGMA synchronous={level}")
    finally:
        cursor.close()


def install_sqlite_pragmas(
    engine: Any,
    *,
    synchronous: str = "FULL",
    busy_timeout_ms: int = SQLITE_BUSY_TIMEOUT_MS,
) -> None:
    """Attach :func:`configure_sqlite_connection` to every connection ``engine`` opens.

    Call immediately after ``create_engine`` and before the first connection.
    A no-op for a non-SQLite engine. ``synchronous="FULL"`` is for ledgers:
    a committed row must survive a power cut, not merely a process crash.
    """
    if getattr(getattr(engine, "dialect", None), "name", "") != "sqlite":
        return
    level = synchronous.upper()
    if level not in _SYNCHRONOUS:
        raise ValueError(f"synchronous must be one of {sorted(_SYNCHRONOUS)}")
    from sqlalchemy import event

    def _on_connect(dbapi_conn: Any, _record: Any) -> None:
        configure_sqlite_connection(
            dbapi_conn, synchronous=level, busy_timeout_ms=busy_timeout_ms
        )

    event.listen(engine, "connect", _on_connect)
