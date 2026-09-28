"""Derived keys, and the version that makes two of them comparable.

A **derived key** is a value computed from something else and then PERSISTED so a
later lookup can find it again: ``strategy_content_hash``, ``structure_hash``,
``validation_report_hash``, an exit-policy fingerprint, a campaign cell key.
Every one of them is a pure function of an input document AND of the algorithm
that reduced it. Change either and the key moves.

That is fine while the key is recomputed on both sides of a comparison. It is not
fine once one side is on disk, because then a stored key and a freshly computed
key can disagree for two completely different reasons:

* the inputs differ -- which is what the key exists to detect, or
* the algorithm or input SCHEMA changed underneath -- which the key cannot
  express at all, and which every naive comparison reads as "different inputs".

The second case is the dangerous one, and it has bitten this codebase three
times: the structural fingerprint, the holdout consumption registry, and the
experiment ledger. In each the failure was silent and permissive -- a non-match
was read as "never seen", which for the holdout registry means a strategy gets a
SECOND look at the one segment it is allowed one look at.

The rule this module enforces
-----------------------------
**A persisted derived key must be stored next to the version of the algorithm
that derived it.** Then a reader can tell "different inputs" from "not
comparable", and code that cannot tell the difference can refuse instead of
guessing.

Mechanically that is one naming convention and one import-time check:

* a column named ``<x>_hash`` / ``<x>_content_hash`` / ``<x>_fingerprint`` /
  ``<x>_checksum`` / ``<x>_key`` is a derived key column;
* it must have a companion column ``<same name>_key_version``;
* :func:`require_key_versions` asserts that for a whole
  :class:`~sqlalchemy.MetaData` and raises at IMPORT time if it does not hold.

Modules that own such a table call :func:`require_key_versions` at the bottom of
the module, so a derived-key column cannot be added to them and still import.
:data:`KNOWN_UNVERSIONED` is the explicit, reasoned allowlist -- an escape hatch
that has to be argued for in writing, in this file, rather than an omission
nobody notices.

Where the version VALUES live
-----------------------------
Not here. This module is the foundation layer and may not import a Fiboki package
above it, which is enforced by ``tests/unit/test_layering.py`` and is also the right
shape: a version belongs with the thing whose changes move it.
:func:`fiboki.strategy.dsl.strategy_key_version` owns the DSL schema stamp,
:func:`fiboki.validation.report.report_key_version` the report stamp, and each
store stamps the columns it writes.

The empty version string
------------------------
:data:`UNSTAMPED` (``""``) means "written before the stamp existed", exactly as
:data:`fiboki.backtest.version.ENGINE_VERSION`'s empty stamp does. It is NOT
"unknown, probably fine": code that must be sure refuses on it, and a
restatement row (see ``fiboki.research.experiment.ExperimentKeyRestatementRow``
and ``fiboki.validation.holdout.HoldoutKeyRestatementRow``) is how an operator
states, on the record, which version an unstamped row was written under.
"""
from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from sqlalchemy import MetaData, Table, text
from sqlalchemy.engine import Engine

__all__ = [
    "DERIVED_KEY_SUFFIXES",
    "KEY_VERSION_SUFFIX",
    "KNOWN_UNVERSIONED",
    "UNSTAMPED",
    "MissingKeyVersion",
    "add_missing_columns",
    "audit_metadata",
    "derived_key_columns",
    "is_derived_key_column",
    "key_version_column",
    "require_key_versions",
    "resolve_key_version",
    "stamp_key_version",
    "unstamped_column_ddl",
]


#: The version of a row written before the version column existed. Empty rather
#: than ``"unknown"`` so a column default can express it without a sentinel that
#: could be mistaken for a real version.
UNSTAMPED = ""

#: Companion column suffix. ``strategy_content_hash`` ->
#: ``strategy_content_hash_key_version``.
KEY_VERSION_SUFFIX = "_key_version"

#: Column-name suffixes that mark a column as a persisted derived key. Deliberately
#: crude: the point is that a future maintainer naming a column the obvious thing
#: gets caught without having to know this module exists.
DERIVED_KEY_SUFFIXES: tuple[str, ...] = (
    "_content_hash",
    "_structure_hash",
    "_hash",
    "_fingerprint",
    "_checksum",
    "_key",
)

#: Derived-key columns that are deliberately NOT versioned, and why. Keyed on
#: ``(table, column)``. Every entry is a known gap, stated rather than hidden;
#: the class-level test in ``tests/unit/test_versioned_keys.py`` reads this dict,
#: so an entry is the only way to keep a key column unversioned and green.
KNOWN_UNVERSIONED: dict[tuple[str, str], str] = {
    (
        "dataset_version",
        "content_checksum",
    ): (
        "Same class of bug, deliberately left: the checksum is over RAW BYTES, so "
        "the only thing that can move it is the checksum function itself, and the "
        "row's own version_id is minted from it -- a changed function mints new "
        "version ids rather than colliding with old ones. Owned by "
        "src/fiboki/data/versioning.py, which this change does not touch."
    ),
}


class MissingKeyVersion(RuntimeError):
    """A persisted derived key with no version column beside it."""


# --------------------------------------------------------------------------
# Resolving and stamping
# --------------------------------------------------------------------------

# The version VALUES live with the thing they describe, not here: this module is
# rank 0 and may not import a Fiboki package above it (tests/unit/test_layering.py
# enforces that, and a foundation module reaching upwards for a constant is how a
# foundation stops being one). See fiboki.strategy.dsl.strategy_key_version and
# fiboki.validation.report.report_key_version.


def resolve_key_version(stored: str | None, restated: str | None = None) -> str:
    """The version a row's keys were computed under.

    The column wins; a restatement covers a row written before the column
    existed; :data:`UNSTAMPED` means nobody has said, and a caller that needs to
    be sure must refuse rather than assume.
    """
    if stored:
        return str(stored)
    if restated:
        return str(restated)
    return UNSTAMPED


def stamp_key_version(
    payload: Mapping[str, Any], version: str, *, field: str = "key_version"
) -> dict[str, Any]:
    """Return ``payload`` with its derived-key version recorded inside it.

    For the fingerprints that are persisted as JSON rather than as a column, where
    there is no companion column to hang the version on. Refuses to overwrite an
    existing stamp, because two different versions in one payload is a bug worth
    hearing about.
    """
    out = dict(payload)
    if field in out and out[field] != version:
        raise MissingKeyVersion(
            f"payload already carries {field}={out[field]!r}; refusing to restamp "
            f"it as {version!r}"
        )
    out[field] = str(version)
    return out


# --------------------------------------------------------------------------
# The structural check
# --------------------------------------------------------------------------


def key_version_column(column: str) -> str:
    """Companion column name for a derived-key column."""
    return f"{column}{KEY_VERSION_SUFFIX}"


def is_derived_key_column(name: str) -> bool:
    """True when this column name names a persisted derived key."""
    if name.endswith(KEY_VERSION_SUFFIX):
        return False
    return any(name.endswith(suffix) for suffix in DERIVED_KEY_SUFFIXES)


def derived_key_columns(table: Table) -> tuple[str, ...]:
    """Every persisted derived-key column on this table, in declaration order."""
    return tuple(c.name for c in table.columns if is_derived_key_column(c.name))


def audit_metadata(
    metadata: MetaData,
    *,
    exempt: Mapping[tuple[str, str], str] | None = None,
) -> tuple[str, ...]:
    """Every derived-key column in ``metadata`` with no version companion.

    Returns one human-readable line per violation, empty when the rule holds.
    Separated from :func:`require_key_versions` so a test can report all of them
    at once instead of one per run.
    """
    allow = dict(KNOWN_UNVERSIONED if exempt is None else exempt)
    violations: list[str] = []
    for table in metadata.sorted_tables:
        names = {c.name for c in table.columns}
        for column in derived_key_columns(table):
            if (table.name, column) in allow:
                continue
            companion = key_version_column(column)
            if companion not in names:
                violations.append(
                    f"{table.name}.{column} is a persisted derived key with no "
                    f"{companion} column: a reader cannot tell 'different inputs' "
                    f"from 'computed under a different algorithm', so a non-match "
                    f"reads as 'never seen'. Add {companion}, or add "
                    f"({table.name!r}, {column!r}) to "
                    f"fiboki.core.versioned_key.KNOWN_UNVERSIONED with a reason."
                )
    return tuple(violations)


def require_key_versions(
    metadata: MetaData,
    *,
    exempt: Mapping[tuple[str, str], str] | None = None,
) -> None:
    """Raise unless every derived-key column in ``metadata`` carries a version.

    Called at import time by the modules that own such a table, so the rule is a
    property of the code rather than a convention someone remembers.
    """
    violations = audit_metadata(metadata, exempt=exempt)
    if violations:
        raise MissingKeyVersion("\n".join(violations))


# --------------------------------------------------------------------------
# Adding the column to a database that predates it
# --------------------------------------------------------------------------


def add_missing_columns(
    engine: Engine, table: str, columns: Mapping[str, str]
) -> tuple[str, ...]:
    """``ALTER TABLE ... ADD COLUMN`` for any of ``columns`` not already present.

    ``columns`` maps column name to its SQL type-and-default clause. Returns the
    names actually added.

    ADD COLUMN, not UPDATE: the ledger tables are append-only and enforce it with
    SQLite triggers, which fire on UPDATE and DELETE but not on DDL. Existing rows
    therefore acquire the column's DEFAULT -- :data:`UNSTAMPED` -- which is the
    honest value for them: nobody has yet stated which version they were written
    under. A restatement row is how that gets stated, which keeps the amendment
    append-only and attributable instead of a silent rewrite of history.
    """
    if engine.dialect.name != "sqlite":  # pragma: no cover - SQLite-only stores
        raise NotImplementedError(
            f"add_missing_columns supports SQLite only, not {engine.dialect.name}"
        )
    added: list[str] = []
    with engine.begin() as conn:
        present = {
            row[1] for row in conn.exec_driver_sql(f"PRAGMA table_info({table})").all()
        }
        if not present:
            return ()
        for name, ddl in columns.items():
            if name in present:
                continue
            conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {name} {ddl}"))
            added.append(name)
    return tuple(added)


def unstamped_column_ddl(length: int = 32) -> str:
    """DDL clause for a key-version column: text, never null, unstamped default."""
    return f"VARCHAR({int(length)}) NOT NULL DEFAULT ''"
