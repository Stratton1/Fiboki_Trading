#!/usr/bin/env python3
"""Fail the build if any committed config enables live execution.

The V1 incident this exists to prevent
--------------------------------------
``render.yaml`` contained, committed to the default branch::

    envVars:
      - key: FIBOKEI_LIVE_EXECUTION_ENABLED
        value: "true"

It sat there for months.  Nothing reviewed it, nothing tested it, and the only
reason it did no harm is that the broker credentials happened to be absent.
The gate was a string in a YAML file that no human read again after the day it
was written.

So this script reads every committed config file and fails if a live-execution
flag is set to a literal truthy value.  Note the three properties that make it
actually work:

1. **It matches on the KEY, fuzzily.**  ``FIBOKEI`` (sic) was a typo that
   persisted across V1's codebase.  A check that matched only the correctly
   spelled key would have missed the exact bug it was written for, so the
   pattern tolerates the misspelling and any prefix.
2. **It matches a LITERAL only.**  ``value: ${LIVE_ENABLED}`` is a reference
   resolved at deploy time and is not this check's business; ``value: "true"``
   is a decision baked into the repository and is.
3. **It fails the build.**  A warning in a log is what V1 had.

Usage::

    python scripts/check_live_flags.py                # scan the repo
    python scripts/check_live_flags.py path/to/dir    # scan a subtree
"""
from __future__ import annotations

import argparse
import re
import sys
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]

#: Extensions worth scanning. A live flag lives in config, not in a .png.
SCANNED_SUFFIXES = frozenset(
    {".yml", ".yaml", ".env", ".toml", ".json", ".ini", ".cfg", ".sh", ".conf", ".plist"}
)
#: Filenames with no suffix that are still config.
SCANNED_NAMES = frozenset({"Dockerfile", "Makefile", "Procfile", ".env"})

SKIP_DIRS = frozenset(
    {
        ".git",
        ".venv",
        "venv",
        "node_modules",
        "__pycache__",
        ".mypy_cache",
        ".ruff_cache",
        ".pytest_cache",
        ".hypothesis",
        "dist",
        "build",
        "site-packages",
    }
)

#: The flags that turn real money on. Prefix-tolerant, typo-tolerant.
FLAG_PATTERN = re.compile(
    r"""
    (?P<key>
        [A-Z0-9_]*                       # any prefix (FIBOKI_, FIBOKEI_, APP_)
        (?:
            LIVE_EXECUTION(?:_ENABLED)?  # FIBOKEI_LIVE_EXECUTION_ENABLED
          | LIVE_TRADING(?:_ENABLED)?
          | ENABLE_LIVE(?:_EXECUTION|_TRADING)?
          | ALLOW_LIVE(?:_ORDERS|_EXECUTION)?
          | REAL_MONEY(?:_ENABLED)?
          | EXECUTION_LIVE
        )
        [A-Z0-9_]*
    )
    """,
    re.VERBOSE,
)

#: What counts as "on". Quoted or bare; any case.
TRUTHY = re.compile(r"""^["']?\s*(?:true|1|yes|on|enabled)\s*["']?$""", re.IGNORECASE)

#: ``KEY: value``, ``KEY=value``, ``"KEY": "value"``, ``- value: "true"``.
ASSIGNMENT = re.compile(r"""^\s*-?\s*["']?(?P<key>[A-Za-z0-9_.]+)["']?\s*[:=]\s*(?P<value>.*?)\s*$""")

#: A YAML env-var block: ``- key: NAME`` then ``  value: "true"``.
YAML_KEY_LINE = re.compile(r"""^\s*-?\s*key\s*:\s*["']?(?P<name>[A-Za-z0-9_.]+)["']?\s*$""")
YAML_VALUE_LINE = re.compile(r"""^\s*value\s*:\s*(?P<value>.*?)\s*$""")

#: Lines that are obviously documenting the danger rather than enabling it.
ALLOW_MARKER = re.compile(r"#\s*live-flag-check:\s*allow", re.IGNORECASE)


@dataclass(frozen=True, slots=True)
class Finding:
    path: Path
    line_number: int
    key: str
    value: str
    line: str

    def render(self, root: Path) -> str:
        try:
            rel = self.path.relative_to(root)
        except ValueError:
            rel = self.path
        return f"{rel}:{self.line_number}: {self.key} = {self.value}\n    {self.line.strip()}"


def should_scan(path: Path) -> bool:
    if any(part in SKIP_DIRS for part in path.parts):
        return False
    if path.name in SCANNED_NAMES:
        return True
    return path.suffix.lower() in SCANNED_SUFFIXES


def iter_files(roots: Sequence[Path]) -> Iterable[Path]:
    for root in roots:
        if root.is_file():
            if should_scan(root):
                yield root
            continue
        for path in sorted(root.rglob("*")):
            if path.is_file() and should_scan(path):
                yield path


def scan_text(text: str, path: Path) -> list[Finding]:
    """Find every literal truthy assignment to a live-execution flag."""
    findings: list[Finding] = []
    lines = text.splitlines()
    pending_key: tuple[str, int] | None = None

    for index, line in enumerate(lines, start=1):
        if ALLOW_MARKER.search(line):
            pending_key = None
            continue

        # Form A: a YAML env-var pair spread over two lines.
        key_match = YAML_KEY_LINE.match(line)
        if key_match:
            name = key_match.group("name")
            pending_key = (name, index) if FLAG_PATTERN.fullmatch(name.upper()) else None
            continue
        if pending_key is not None:
            value_match = YAML_VALUE_LINE.match(line)
            if value_match:
                value = value_match.group("value")
                if TRUTHY.match(value):
                    findings.append(Finding(path, index, pending_key[0], value, line))
                pending_key = None
                continue
            if line.strip() and not line.strip().startswith("#"):
                pending_key = None

        # Form B: a one-line assignment.
        stripped = line.strip()
        if stripped.startswith("#") and "=" not in stripped and ":" not in stripped:
            continue
        payload = stripped.lstrip("#").strip() if stripped.startswith("#") else stripped
        assignment = ASSIGNMENT.match(payload)
        if assignment is None:
            continue
        key = assignment.group("key")
        if not FLAG_PATTERN.fullmatch(key.upper()):
            continue
        value = assignment.group("value").split("#", 1)[0].strip()
        if TRUTHY.match(value):
            findings.append(Finding(path, index, key, value, line))
    return findings


def scan(roots: Sequence[Path]) -> list[Finding]:
    findings: list[Finding] = []
    for path in iter_files(roots):
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        findings.extend(scan_text(text, path))
    return findings


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "paths", nargs="*", type=Path, default=[REPO_ROOT], help="files or directories to scan"
    )
    args = parser.parse_args(argv)
    roots = [p.resolve() for p in args.paths]
    findings = scan(roots)

    if not findings:
        print(f"live-flag check: clean ({len(list(iter_files(roots)))} config file(s) scanned)")
        return 0

    print("LIVE EXECUTION FLAG SET IN COMMITTED CONFIG", file=sys.stderr)
    print("", file=sys.stderr)
    for finding in findings:
        print(finding.render(REPO_ROOT), file=sys.stderr)
    print(
        "\nA live-execution flag must never be a committed literal. Move it to a runtime "
        "secret, or set it to false here and enable it deliberately at deploy time.\n"
        "V1 shipped FIBOKEI_LIVE_EXECUTION_ENABLED: \"true\" in render.yaml and nobody "
        "noticed for months. If this line is documentation rather than configuration, "
        "append `# live-flag-check: allow`.",
        file=sys.stderr,
    )
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
