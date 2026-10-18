#!/usr/bin/env python3
"""Pre-flight for scripts/server-migrate.sh: may the paper sessions be moved?

The rule (docs/v2/SERVER.md, "Migrate"): a RUNNING Mac paper process that holds an open position,
and has no state snapshot the next process can restore from, is NOT moved. A start without a
restorable snapshot journals ``positions_abandoned_at_restart`` and drops the unrealised P&L
(entrypoints/paper_state.py), which is the loss the snapshot was written to prevent. The server
would be that start.

What counts as "no restorable snapshot":
  * ``<ledger_dir>/state_snapshot.json`` is missing or fails its CRC frame (corrupt), or
  * with ``--require-fresh`` (used AFTER the Mac process has stopped): the ledgers (trades.jsonl,
    intents.jsonl) are longer than the snapshot's watermark, which the next start refuses as "stale".
(The wiring-sha and strategy-hash stamps are compared at start-up by the process itself; the shell
script compares the wiring files' SHA-256 between the two machines beforehand.)

How many positions are open: the snapshot's book when it verifies, else the session summary
(``<paper_root>/forward-<wiring>/summary.json``, ``open_positions``), else UNKNOWN. Unknown counts
as possibly open.

Exit 0: nothing refused (warnings may print). Exit 1: at least one refusal. Pure reading; nothing is
written. Usage:
    migrate_preflight.py --state-dir DIR [--paper-root DIR] [--running paper,paper-crypto]
                         [--require-fresh] [--accept-abandoned-positions] [--json]
"""
from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

#: wiring ledger directory name -> the service that runs it (scripts/fiboki-service.sh).
SERVICE_OF_WIRING = {
    "paper_forward_v1": "paper",
    "paper_forward_crypto_v1": "paper-crypto",
}
SNAPSHOT_FILE = "state_snapshot.json"


@dataclass
class Finding:
    wiring: str
    service: str
    running: bool
    snapshot: str            # absent | ok | corrupt
    watermark: str           # n/a | matches | differs
    open_positions: int | None   # None = unknown
    open_source: str         # snapshot | summary | unknown
    verdict: str             # ok | warn | refuse
    message: str


def _read_snapshot(path: Path) -> tuple[str, dict[str, Any] | None]:
    if not path.exists():
        return "absent", None
    try:
        from fiboki.core.durable import FrameError, read_record

        try:
            body = json.loads(read_record(path))
        except (FrameError, ValueError, OSError):
            return "corrupt", None
    except ImportError as exc:  # pragma: no cover - run with PYTHONPATH=src or the venv
        raise SystemExit(
            "migrate_preflight.py needs the fiboki package (run it with the repo's .venv python)"
        ) from exc
    if not isinstance(body, dict) or not isinstance(body.get("state"), dict):
        return "corrupt", None
    return "ok", body


def _snapshot_open(body: dict[str, Any]) -> int | None:
    try:
        return len(body["state"]["manager"]["book"]["open"])
    except (KeyError, TypeError):
        return None


def _summary_open(paper_root: Path, wiring: str) -> int | None:
    path = paper_root / f"forward-{wiring}" / "summary.json"
    try:
        value = json.loads(path.read_text(encoding="utf-8")).get("open_positions")
    except (OSError, ValueError):
        return None
    return int(value) if isinstance(value, int) else None


def _watermark(ledger: Path) -> dict[str, int]:
    def size(name: str) -> int:
        p = ledger / name
        return p.stat().st_size if p.exists() else 0

    return {"trades_bytes": size("trades.jsonl"), "intents_bytes": size("intents.jsonl")}


def assess(state_dir: Path, paper_root: Path, running: set[str], *, require_fresh: bool,
           accept_abandoned: bool) -> list[Finding]:
    root = state_dir / "paper_forward"
    findings: list[Finding] = []
    if not root.is_dir():
        return findings
    for ledger in sorted(p for p in root.iterdir() if p.is_dir()):
        wiring = ledger.name
        service = SERVICE_OF_WIRING.get(wiring, "paper")
        is_running = service in running
        snap_status, body = _read_snapshot(ledger / SNAPSHOT_FILE)
        wm = "n/a"
        if body is not None:
            wm = "matches" if (body.get("watermark") or {}) == _watermark(ledger) else "differs"
        if body is not None and _snapshot_open(body) is not None:
            n, source = _snapshot_open(body), "snapshot"
        else:
            n = _summary_open(paper_root, wiring)
            source = "summary" if n is not None else "unknown"
        restorable = body is not None and (wm == "matches" or not require_fresh)
        possibly_open = n is None or n > 0
        label = f"{wiring} ({service})"
        if restorable:
            verdict, msg = "ok", (
                f"{label}: {n if n is not None else '?'} open position(s); a restorable state snapshot "
                f"exists (watermark {wm}); the server restores them on first start")
        elif not possibly_open:
            verdict, msg = "ok", f"{label}: flat (0 open positions per the {source}); nothing to lose"
        elif is_running and not accept_abandoned:
            why = ("the snapshot's ledger watermark differs: the next start refuses it as stale"
                   if body is not None else
                   f"the state snapshot is {snap_status}")
            verdict, msg = "refuse", (
                f"{label}: the RUNNING Mac process has "
                f"{'an unknown number of' if n is None else n} open position(s) and {why}. Moving it would "
                "journal positions_abandoned_at_restart and drop their unrealised P&L. Wait until it is flat, "
                "or restart the Mac service once on a build that writes state_snapshot.json and re-run, "
                "or accept the loss with --accept-abandoned-positions.")
        elif is_running:
            verdict, msg = "warn", (
                f"{label}: open position(s) ({n if n is not None else '?'}) will be ABANDONED at the move "
                f"(snapshot {snap_status}); accepted with --accept-abandoned-positions")
        else:
            verdict, msg = "warn", (
                f"{label}: not running; {n if n is not None else '?'} open position(s) recorded and no "
                f"restorable snapshot ({snap_status}). Any restart, on the Mac or the server, abandons them.")
        findings.append(Finding(wiring, service, is_running, snap_status, wm, n, source, verdict, msg))
    return findings


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--state-dir", required=True, type=Path)
    ap.add_argument("--paper-root", type=Path, default=None)
    ap.add_argument("--running", default="", help="comma list of Mac services running: paper,paper-crypto")
    ap.add_argument("--require-fresh", action="store_true")
    ap.add_argument("--accept-abandoned-positions", action="store_true")
    ap.add_argument("--json", action="store_true")
    ns = ap.parse_args(argv)
    paper_root = ns.paper_root or ns.state_dir / "paper"
    running = {s.strip() for s in ns.running.split(",") if s.strip()}
    findings = assess(ns.state_dir, paper_root, running, require_fresh=ns.require_fresh,
                      accept_abandoned=ns.accept_abandoned_positions)
    if ns.json:
        print(json.dumps([asdict(f) for f in findings], indent=2))
    else:
        if not findings:
            print("no paper session ledgers under", ns.state_dir / "paper_forward", "(nothing to protect)")
        for f in findings:
            print(f"{f.verdict.upper():7} {f.message}")
    return 1 if any(f.verdict == "refuse" for f in findings) else 0


if __name__ == "__main__":
    sys.exit(main())
