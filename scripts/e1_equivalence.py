"""E-1 / E-2 equivalence check: a measuring-mode replay against a stored fail-fast run.

    python scripts/e1_equivalence.py STORED.json REPLAY.json [--out REPORT.json]

``STORED`` is a fail-fast E-1 output (the ladder exactly as promotion runs it, one
gate set); ``REPLAY`` is the same study re-run with ``--ladder-mode measuring``.
For every (sr, replicate) the replay's verdict and binding constraint under the
stored run's gate set must equal the stored ones, and every gate the fail-fast
ladder reached must read the same status and value. One disagreement fails the
check (exit 1) and is printed; the pre-registration
(``research/preregistration/gate_calibration_e2.json``, ``metrics.equivalence``)
says what that means: measuring mode is then not E-1/E-2 evidence.
"""
from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path
from typing import Any

#: The audited set's gates by the rung that produces them.
_RUNG_OF = {
    "min_trades": 0, "walk_forward_efficiency": 2, "oos_window_hit_rate": 2,
    "survives_2x_spread": 4, "parameter_plateau": 4,
    "deflated_sharpe": 5, "pbo": 5, "spa_consistent_p": 5, "stepm_survivor": 5,
}


def _close(a: Any, b: Any) -> bool:
    if a is None or b is None:
        return a is b
    return math.isclose(float(a), float(b), rel_tol=1e-9, abs_tol=1e-12)


def compare(stored: dict[str, Any], replay: dict[str, Any]) -> dict[str, Any]:
    name = stored["gate_set_version"]
    if stored.get("ladder_mode", "fail_fast") != "fail_fast":
        raise ValueError("STORED must be a fail-fast run")
    if replay.get("ladder_mode") != "measuring":
        raise ValueError("REPLAY must be a measuring run")
    if name not in replay["judged_gate_sets"]:
        raise ValueError(f"the replay did not judge {name}")
    # A run from before the fingerprint was recorded (E-1 process 1, 2026-09-30)
    # is identified by its version; the version's fingerprint is pinned in code
    # (tests/unit/test_validation_gates.py) and has not changed since.
    stored_fp = stored.get("gate_set_fingerprint")
    if stored_fp is not None and stored_fp != replay["judged_gate_sets"][name]:
        raise ValueError(f"the replay did not judge {name} at the stored fingerprint")
    for key in ("process", "config"):
        s, r = stored[key], replay[key]
        if key == "config":
            s = {k: v for k, v in s.items() if k != "min_trades"}  # 400 vs the 150 floor
            r = {k: v for k, v in r.items() if k != "min_trades"}
        if s != r:
            raise ValueError(f"{key} differs: stored {s} vs replay {r}")
    by_key = {(float(r["sr"]), int(r["replicate"])): r for r in replay["runs"]}
    disagreements: list[dict[str, Any]] = []
    n = 0
    for s in stored["runs"]:
        key = (float(s["sr"]), int(s["replicate"]))
        r = by_key.get(key)
        if r is None:
            disagreements.append({"sr": key[0], "replicate": key[1], "missing_in_replay": True})
            continue
        u = r["under"][name]
        n += 1
        diff: dict[str, Any] = {}
        for f in ("promoted", "verdict", "binding_kind", "binding"):
            if s[f] != u[f]:
                diff[f] = {"stored": s[f], "replay": u[f]}
        # gates the fail-fast ladder reached: every gate at a rung <= the binding rung
        # (all of them when it promoted)
        reached = 99 if s["binding_kind"] == "none" else _RUNG_OF.get(s["binding"], _rung_index(s["binding"]))
        for gate, st in s["gate_status"].items():
            if _RUNG_OF[gate] <= reached and u["gate_status"].get(gate) != st:
                diff.setdefault("gate_status", {})[gate] = {"stored": st, "replay": u["gate_status"].get(gate)}
        for gate, v in s["gate_values"].items():
            if _RUNG_OF[gate] <= reached and not _close(v, u["gate_values"].get(gate)):
                diff.setdefault("gate_values", {})[gate] = {"stored": v, "replay": u["gate_values"].get(gate)}
        if s.get("sanity_n_trades") != r.get("sanity_n_trades"):
            diff["sanity_n_trades"] = {"stored": s.get("sanity_n_trades"), "replay": r.get("sanity_n_trades")}
        if diff:
            disagreements.append({"sr": key[0], "replicate": key[1], **diff})
    return {
        "gate_set": name,
        "rows_compared": n,
        "rows_stored": len(stored["runs"]),
        "rows_replay_under_set": len(replay["runs"]),
        "disagreements": disagreements,
        "equivalent": not disagreements and n == len(stored["runs"]),
        "stored_summary": stored["summary"],
        "replay_summary_under_set": replay["summary_by_gate_set"][name],
    }


def _rung_index(binding: str) -> int:
    """'RUNG 3 PURGED_CV' -> 3 for a rung-level binding."""
    try:
        return int(binding.split()[1])
    except (IndexError, ValueError):
        return 99


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=(__doc__ or "").splitlines()[0])
    ap.add_argument("stored", type=Path)
    ap.add_argument("replay", type=Path)
    ap.add_argument("--out", type=Path, default=None)
    args = ap.parse_args(argv)
    report = compare(
        json.loads(args.stored.read_text(encoding="utf-8")),
        json.loads(args.replay.read_text(encoding="utf-8")),
    )
    if args.out is not None:
        args.out.write_text(json.dumps(report, indent=2, sort_keys=True), encoding="utf-8")
    shown = {k: v for k, v in report.items() if k != "disagreements"}
    shown["n_disagreements"] = len(report["disagreements"])
    print(json.dumps(shown, indent=2, sort_keys=True))
    for d in report["disagreements"][:20]:
        print(json.dumps(d, sort_keys=True))
    return 0 if report["equivalent"] else 1


if __name__ == "__main__":
    sys.exit(main())
