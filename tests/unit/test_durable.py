"""Durable append-only lines and SQLite hygiene (audit F, P1-6 and P2-14).

The defect: plain ``fsync`` on macOS, no directory fsync on create, and loaders
that raised on a torn final line so a worker could not start after a crash.
"""
from __future__ import annotations

import json
import os
import sqlite3

import pytest

from fiboki.core import durable
from fiboki.core.durable import (
    DurableLogCorrupt,
    FrameError,
    decode_line,
    durable_append,
    frame_line,
    install_sqlite_pragmas,
    read_payloads,
    set_torn_tail_hook,
)


@pytest.fixture
def torn_events():
    seen: list = []
    set_torn_tail_hook("test", seen.append)
    yield seen
    set_torn_tail_hook("test", None)


# ------------------------------------------------------------------ framing


def test_a_framed_line_is_still_one_json_object_and_round_trips() -> None:
    payload = json.dumps({"action": "activate", "reason": 'said "x"\nthen y'}, sort_keys=True)
    line = frame_line(payload)
    assert json.loads(line)["action"] == "activate"  # third-party readers still work
    assert decode_line(line) == (payload, True)
    assert decode_line(frame_line("{}")) == ("{}", True)


def test_a_flipped_byte_fails_the_crc() -> None:
    line = frame_line('{"size":1000}')
    with pytest.raises(FrameError, match="crc32"):
        decode_line(line.replace("1000", "9000"))


def test_legacy_unframed_lines_are_accepted() -> None:
    assert decode_line('{"a": 1}') == ('{"a": 1}', False)
    with pytest.raises(FrameError):
        decode_line('{"a": ')


def test_frame_refuses_non_objects_and_double_framing() -> None:
    for bad in ("[1]", '"x"', "{ \"a\": 1}", frame_line('{"a":1}')):
        with pytest.raises(ValueError):
            frame_line(bad)


# ------------------------------------------------------------ append / read


def test_a_mixed_legacy_and_framed_file_reads_in_order(tmp_path) -> None:
    path = tmp_path / "ledger.jsonl"
    path.write_text('{"n": 1}\n\n{"n": 2}\n')  # legacy, with a blank line
    durable_append(path, json.dumps({"n": 3}))
    durable_append(path, json.dumps({"n": 4}))
    assert [json.loads(p)["n"] for p in read_payloads(path)] == [1, 2, 3, 4]
    raw = path.read_text().splitlines()
    assert raw[0] == '{"n": 1}' and raw[-1].startswith('{"_crc32":"')


def test_a_torn_tail_is_quarantined_alerted_and_never_raises(tmp_path, torn_events) -> None:
    path = tmp_path / "intents.jsonl"
    durable_append(path, json.dumps({"n": 1}))
    with path.open("ab") as fh:
        fh.write(b'{"_crc32":"00000000","n":')  # the process died here
    assert [json.loads(p)["n"] for p in read_payloads(path)] == [1]
    assert len(torn_events) == 1
    torn = torn_events[0]
    assert torn.quarantine_path is not None and torn.quarantine_path.exists()
    assert torn.quarantine_path.read_bytes() == b'{"_crc32":"00000000","n":'
    assert torn.quarantine_path.name.startswith("intents.jsonl.torn-")
    # The ledger was cut back to its last good record and appends cleanly.
    durable_append(path, json.dumps({"n": 2}))
    assert [json.loads(p)["n"] for p in read_payloads(path)] == [1, 2]


def test_a_newline_terminated_but_corrupt_last_line_is_also_torn(tmp_path, torn_events) -> None:
    path = tmp_path / "k.jsonl"
    durable_append(path, json.dumps({"n": 1}))
    with path.open("a") as fh:
        fh.write('{"_crc32":"deadbeef","n":2}\n')
    assert [json.loads(p)["n"] for p in read_payloads(path)] == [1]
    assert torn_events and "verification" in torn_events[0].reason


def test_an_append_after_a_crash_quarantines_first(tmp_path, torn_events) -> None:
    path = tmp_path / "k.jsonl"
    durable_append(path, json.dumps({"n": 1}))
    with path.open("ab") as fh:
        fh.write(b'{"n": 2')
    durable_append(path, json.dumps({"n": 3}))  # must not glue onto the torn bytes
    assert [json.loads(p)["n"] for p in read_payloads(path)] == [1, 3]
    assert len(torn_events) == 1


def test_corruption_before_the_tail_raises(tmp_path) -> None:
    path = tmp_path / "k.jsonl"
    durable_append(path, json.dumps({"n": 1}))
    durable_append(path, json.dumps({"n": 2}))
    lines = path.read_text().splitlines()
    lines[0] = lines[0].replace('"n": 1', '"n": 7')
    assert lines[0] != path.read_text().splitlines()[0]
    path.write_text("\n".join(lines) + "\n")
    with pytest.raises(DurableLogCorrupt, match="line 1"):
        read_payloads(path)


def test_a_missing_file_is_an_empty_ledger(tmp_path) -> None:
    assert read_payloads(tmp_path / "absent.jsonl") == []


# -------------------------------------------------------------------- fsync


def test_darwin_uses_F_FULLFSYNC(monkeypatch, tmp_path) -> None:
    calls: list[tuple[int, int]] = []
    monkeypatch.setattr(durable, "IS_DARWIN", True)
    monkeypatch.setattr(durable.fcntl, "F_FULLFSYNC", 51, raising=False)
    monkeypatch.setattr(durable.fcntl, "fcntl", lambda fd, op, *a: calls.append((fd, op)))
    fsyncs: list[int] = []
    monkeypatch.setattr(durable.os, "fsync", lambda fd: fsyncs.append(fd))
    durable_append(tmp_path / "x.jsonl", '{"a":1}')
    assert calls and all(op == 51 for _, op in calls)
    assert fsyncs == []  # plain fsync is NOT what makes it durable on the Mac


def test_other_platforms_fsync_the_file_and_the_directory_on_create(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(durable, "IS_DARWIN", False)
    synced: list[str] = []
    real_fsync = os.fsync

    def spy(fd: int) -> None:
        synced.append(os.readlink(f"/proc/self/fd/{fd}") if os.path.exists("/proc/self/fd") else "?")
        real_fsync(fd)

    monkeypatch.setattr(durable.os, "fsync", spy)
    target = tmp_path / "new.jsonl"
    durable_append(target, '{"a":1}')
    if os.path.exists("/proc/self/fd"):
        assert str(target) in synced and str(tmp_path) in synced
    first = len(synced)
    durable_append(target, '{"a":2}')
    assert len(synced) == first + 1  # no directory fsync once it exists


def test_F_FULLFSYNC_refused_falls_back_to_fsync(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(durable, "IS_DARWIN", True)
    monkeypatch.setattr(durable.fcntl, "F_FULLFSYNC", 51, raising=False)

    def refuse(fd, op, *a):
        raise OSError("not supported on this mount")

    monkeypatch.setattr(durable.fcntl, "fcntl", refuse)
    fsyncs: list[int] = []
    monkeypatch.setattr(durable.os, "fsync", lambda fd: fsyncs.append(fd))
    durable_append(tmp_path / "x.jsonl", '{"a":1}')
    assert fsyncs


# ------------------------------------------------------------------- SQLite


def test_the_sqlite_listener_runs_on_every_connection(tmp_path) -> None:
    from sqlalchemy import create_engine

    engine = create_engine(f"sqlite+pysqlite:///{tmp_path / 'x.sqlite'}", future=True)
    install_sqlite_pragmas(engine, synchronous="FULL")
    seen = []
    for _ in range(3):
        raw = engine.raw_connection()
        try:
            cur = raw.cursor()
            seen.append(
                (
                    cur.execute("PRAGMA journal_mode").fetchone()[0],
                    cur.execute("PRAGMA busy_timeout").fetchone()[0],
                    cur.execute("PRAGMA synchronous").fetchone()[0],
                )
            )
        finally:
            raw.invalidate()  # force a NEW connection next time round
    engine.dispose()
    assert seen == [("wal", 30000, 2)] * 3  # synchronous FULL == 2


def test_configure_refuses_an_unknown_synchronous_level(tmp_path) -> None:
    conn = sqlite3.connect(str(tmp_path / "y.sqlite"))
    with pytest.raises(ValueError):
        durable.configure_sqlite_connection(conn, synchronous="MAYBE")
    conn.close()


#: Modules that create a SQLAlchemy engine without the shared listener, each with
#: the reason. Owned by another workstream at the time of writing; the entry is
#: the reminder, not a waiver of the rule.
_ENGINE_EXCEPTIONS = {
    "workers/base.py": (
        "WorkerStore.from_url applies WAL/busy_timeout/synchronous with PRAGMAs on one "
        "pooled connection plus connect_args timeout=30; synchronous=FULL therefore "
        "reaches only that connection. Switch it to install_sqlite_pragmas."
    ),
}


def test_every_sqlalchemy_engine_in_src_installs_the_sqlite_listener() -> None:
    import ast
    from pathlib import Path

    src = Path(__file__).resolve().parents[2] / "src" / "fiboki"
    offenders = []
    for path in src.rglob("*.py"):
        tree = ast.parse(path.read_text(), filename=str(path))
        calls = {
            (n.func.id if isinstance(n.func, ast.Name) else getattr(n.func, "attr", ""))
            for n in ast.walk(tree)
            if isinstance(n, ast.Call)
        }
        rel = str(path.relative_to(src))
        if (
            "create_engine" in calls
            and "install_sqlite_pragmas" not in calls
            and rel not in _ENGINE_EXCEPTIONS
        ):
            offenders.append(rel)
    assert not offenders, offenders
    for rel, reason in _ENGINE_EXCEPTIONS.items():
        assert (src / rel).exists() and len(reason) > 40, rel
