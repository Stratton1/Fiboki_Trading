#!/usr/bin/env python3
"""Record and verify the resolved dependency set.

Why this exists
---------------
``pyproject.toml`` pins the direct dependencies that can change a number.  It
does not pin their transitive dependencies, and it does not record what was
ACTUALLY installed when a result was computed.  Two machines can both satisfy
``pandas==2.2.3`` while differing in a transitive C library, and the first time
anybody notices is when a golden test fails on one machine and passes on the
other -- at which point nobody can reconstruct what the other environment was.

So: :func:`record` writes every installed distribution and its version into a
lockfile, and :func:`verify` compares the current environment against it.  The
lockfile is committed.  CI fails on drift.  ``fiboki system doctor`` reports
it.  A worker can check it at startup.

This is deliberately NOT a resolver.  It does not decide what to install; it
records what IS installed, so that a result is attributable to an environment
rather than to a hope.  Pair it with ``pip install -e '.[dev]'`` from the
pinned ``pyproject.toml``.

Usage::

    python scripts/lockfile.py record            # write deploy/requirements.lock
    python scripts/lockfile.py verify            # exit 1 on drift
    python scripts/lockfile.py verify --warn     # exit 0, print drift
"""
from __future__ import annotations

import argparse
import hashlib
import json
import platform
import sys
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_LOCKFILE = REPO_ROOT / "deploy" / "requirements.lock"

#: Packages whose version can change a NUMBER. Drift in these is an error even
#: under --warn, because a stored backtest computed against a different numpy
#: is not comparable with one computed against this one.
NUMERICAL = frozenset({"numpy", "pandas", "scipy", "pyarrow"})

#: Packages we do not lock: they vary by platform and cannot change a result.
IGNORED = frozenset({"pip", "setuptools", "wheel", "pkg-resources", "distribute"})


def _normalise(name: str) -> str:
    return name.lower().replace("_", "-").replace(".", "-")


def installed_distributions() -> dict[str, str]:
    """``{normalised name: version}`` for everything importable right now."""
    from importlib.metadata import distributions

    found: dict[str, str] = {}
    for dist in distributions():
        raw = dist.metadata["Name"] if dist.metadata else None
        if not raw:
            continue
        name = _normalise(raw)
        if name in IGNORED:
            continue
        found[name] = dist.version or "0"
    return dict(sorted(found.items()))


@dataclass(frozen=True, slots=True)
class Lockfile:
    packages: dict[str, str]
    python: str
    platform: str
    recorded_at: str
    digest: str = ""

    @staticmethod
    def compute_digest(packages: Mapping[str, str]) -> str:
        blob = json.dumps(dict(sorted(packages.items())), separators=(",", ":"))
        return hashlib.sha256(blob.encode("utf-8")).hexdigest()

    def to_text(self) -> str:
        header = [
            "# fiboki dependency lockfile -- GENERATED, do not hand-edit.",
            "# Regenerate with: python scripts/lockfile.py record",
            f"# python: {self.python}",
            f"# platform: {self.platform}",
            f"# recorded_at: {self.recorded_at}",
            f"# digest: {self.digest or self.compute_digest(self.packages)}",
            "",
        ]
        body = [f"{name}=={version}" for name, version in sorted(self.packages.items())]
        return "\n".join([*header, *body]) + "\n"

    @classmethod
    def parse(cls, text: str) -> Lockfile:
        packages: dict[str, str] = {}
        meta: dict[str, str] = {}
        for line in text.splitlines():
            stripped = line.strip()
            if not stripped:
                continue
            if stripped.startswith("#"):
                key, _, value = stripped.lstrip("# ").partition(":")
                if value:
                    meta[key.strip()] = value.strip()
                continue
            name, _, version = stripped.partition("==")
            if name:
                packages[_normalise(name)] = version
        return cls(
            packages=packages,
            python=meta.get("python", ""),
            platform=meta.get("platform", ""),
            recorded_at=meta.get("recorded_at", ""),
            digest=meta.get("digest", ""),
        )

    @classmethod
    def from_environment(cls) -> Lockfile:
        packages = installed_distributions()
        return cls(
            packages=packages,
            python=platform.python_version(),
            platform=f"{platform.system()}-{platform.machine()}",
            recorded_at=datetime.now(tz=UTC).isoformat(),
            digest=cls.compute_digest(packages),
        )


@dataclass
class VerifyResult:
    ok: bool
    missing: dict[str, str] = field(default_factory=dict)
    extra: dict[str, str] = field(default_factory=dict)
    changed: dict[str, tuple[str, str]] = field(default_factory=dict)
    lockfile_missing: bool = False
    #: Drift restricted to packages that can change a number.
    numerical_drift: dict[str, tuple[str, str]] = field(default_factory=dict)

    def summary(self) -> str:
        if self.lockfile_missing:
            return "no lockfile recorded"
        if self.ok:
            return "environment matches the lockfile"
        parts = []
        if self.numerical_drift:
            parts.append(
                "NUMERICAL DRIFT: "
                + ", ".join(
                    f"{n} {a}→{b}" for n, (a, b) in sorted(self.numerical_drift.items())
                )
            )
        if self.changed:
            parts.append(f"{len(self.changed)} version change(s)")
        if self.missing:
            parts.append(f"{len(self.missing)} missing")
        if self.extra:
            parts.append(f"{len(self.extra)} unexpected")
        return "; ".join(parts)

    def report(self) -> str:
        lines = [self.summary()]
        for name, (was, now) in sorted(self.changed.items()):
            flag = "  [NUMERICAL]" if name in NUMERICAL else ""
            lines.append(f"  changed  {name}: lock={was} installed={now}{flag}")
        for name, version in sorted(self.missing.items()):
            lines.append(f"  missing  {name}=={version} (in lock, not installed)")
        for name, version in sorted(self.extra.items()):
            lines.append(f"  extra    {name}=={version} (installed, not in lock)")
        return "\n".join(lines)


def record(path: Path = DEFAULT_LOCKFILE) -> Lockfile:
    lock = Lockfile.from_environment()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(lock.to_text(), encoding="utf-8")
    return lock


def verify(path: Path = DEFAULT_LOCKFILE, *, strict_extras: bool = False) -> VerifyResult:
    """Compare the running environment against the lockfile.

    ``strict_extras`` is off by default: an extra package that nothing imports
    cannot change a result, and failing CI over a developer's ``ipython`` is
    how a drift check gets disabled.  A CHANGED version always counts.
    """
    if not path.exists():
        return VerifyResult(ok=False, lockfile_missing=True)
    lock = Lockfile.parse(path.read_text(encoding="utf-8"))
    current = installed_distributions()

    missing = {n: v for n, v in lock.packages.items() if n not in current}
    extra = {n: v for n, v in current.items() if n not in lock.packages}
    changed = {
        n: (lock.packages[n], current[n])
        for n in lock.packages
        if n in current and current[n] != lock.packages[n]
    }
    numerical = {n: v for n, v in changed.items() if n in NUMERICAL}
    numerical.update(
        {n: (lock.packages[n], "<missing>") for n in missing if n in NUMERICAL}
    )
    ok = not changed and not missing and (not extra or not strict_extras)
    return VerifyResult(
        ok=ok,
        missing=missing,
        extra=extra,
        changed=changed,
        numerical_drift=numerical,
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    record_p = sub.add_parser("record", help="write the lockfile from this environment")
    record_p.add_argument("--path", type=Path, default=DEFAULT_LOCKFILE)

    verify_p = sub.add_parser("verify", help="fail on drift")
    verify_p.add_argument("--path", type=Path, default=DEFAULT_LOCKFILE)
    verify_p.add_argument(
        "--warn", action="store_true", help="report drift but exit 0 (never in CI)"
    )
    verify_p.add_argument("--strict-extras", action="store_true")
    verify_p.add_argument("--json", action="store_true")

    args = parser.parse_args(argv)

    if args.command == "record":
        lock = record(args.path)
        print(f"recorded {len(lock.packages)} package(s) to {args.path}")
        print(f"digest {lock.digest}")
        return 0

    result = verify(args.path, strict_extras=args.strict_extras)
    if args.json:
        print(
            json.dumps(
                {
                    "ok": result.ok,
                    "lockfile_missing": result.lockfile_missing,
                    "changed": result.changed,
                    "missing": result.missing,
                    "extra": result.extra,
                    "numerical_drift": result.numerical_drift,
                },
                indent=2,
                sort_keys=True,
            )
        )
    else:
        print(result.report())

    if result.ok:
        return 0
    if result.numerical_drift:
        print(
            "\nNumerical dependency drift is never a warning: a result computed in this "
            "environment is not comparable with one computed in the locked environment. "
            "Re-run the golden tests before trusting anything produced here.",
            file=sys.stderr,
        )
        return 1
    return 0 if args.warn else 1


if __name__ == "__main__":
    raise SystemExit(main())
