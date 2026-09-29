"""``~/.fiboki/env``: the operator's KEY=VALUE file, read the way the services read it.

``scripts/fiboki-service.sh`` reads this file line by line WITHOUT shell
expansion, because operator password hashes are scrypt strings full of ``$``
and sourcing the file would expand them into nothing. Anything else that wants
to see what the services see (``fiboki doctor``, a test) must parse it with
the same rules, which live here so there is one definition:

* blank lines and lines starting with ``#`` are ignored;
* ``KEY=VALUE`` splits at the FIRST ``=``; the value keeps any later ``=``;
* a key must match ``[A-Z0-9_]+``; anything else is reported as malformed;
* a value wrapped in matching single or double quotes loses the quotes and
  nothing else: no escapes, no ``$`` expansion, no ``~`` expansion;
* whitespace inside a value is kept verbatim.

Nothing here reads ``os.environ``; :func:`parse_env_file` is pure and
:func:`read_env_file` is that plus one file read. ``core`` imports nothing
from Fiboki.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path

__all__ = ["DEFAULT_ENV_FILE", "EnvFile", "env_file_path", "parse_env_file", "read_env_file"]

#: Relative to ``FIBOKI_HOME``.
DEFAULT_ENV_FILE = "env"
_KEY = re.compile(r"^[A-Z0-9_]+$")


@dataclass(frozen=True, slots=True)
class EnvFile:
    """What one parse produced: the values, and every line it refused."""

    path: Path | None
    values: dict[str, str] = field(default_factory=dict)
    malformed: tuple[str, ...] = ()
    exists: bool = False


def parse_env_file(text: str, *, path: Path | None = None) -> EnvFile:
    """Parse ``text`` with the service wrapper's rules. Pure."""
    values: dict[str, str] = {}
    malformed: list[str] = []
    for raw in text.splitlines():
        line = raw.rstrip("\r")
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        key, sep, value = line.partition("=")
        if not sep or not _KEY.match(key):
            malformed.append(key if len(key) <= 40 else key[:37] + "...")
            continue
        if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
            value = value[1:-1]
        values[key] = value
    return EnvFile(path=path, values=values, malformed=tuple(malformed), exists=True)


def env_file_path(env: Mapping[str, str]) -> Path:
    """``$FIBOKI_HOME/env`` (``~/.fiboki/env``), expanded against ``env``'s ``HOME``."""
    home = str(env.get("FIBOKI_HOME", "") or "").strip() or "~/.fiboki"
    if home == "~" or home.startswith("~/"):
        base = str(env.get("HOME", "") or "").strip()
        home = (base + home[1:]) if base else str(Path(home).expanduser())
    return Path(home) / DEFAULT_ENV_FILE


def read_env_file(path: Path) -> EnvFile:
    """Parse the file at ``path``; a missing or unreadable file is an empty result."""
    try:
        text = path.read_text(encoding="utf-8")
    except (FileNotFoundError, NotADirectoryError, PermissionError, UnicodeDecodeError):
        return EnvFile(path=path, exists=False)
    return parse_env_file(text, path=path)
