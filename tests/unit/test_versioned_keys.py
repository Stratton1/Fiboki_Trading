"""Every persisted derived key carries the version that derived it.

This is the class-level test. The three instances found so far -- the structural
fingerprint, the holdout consumption registry and the experiment ledger -- were all
the same bug: a key computed from a document AND from an algorithm, stored, and then
compared against a freshly computed one, with no way to tell "different inputs" from
"different algorithm". The tests below do not check those three; they enumerate
every persisted key column in the package and fail on a FOURTH.
"""
from __future__ import annotations

import pytest
from sqlalchemy import String
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from fiboki.core.versioned_key import (
    DERIVED_KEY_SUFFIXES,
    KNOWN_UNVERSIONED,
    UNSTAMPED,
    MissingKeyVersion,
    audit_metadata,
    derived_key_columns,
    is_derived_key_column,
    key_version_column,
    require_key_versions,
    resolve_key_version,
    stamp_key_version,
)
from fiboki.strategy.dsl import strategy_key_version
from fiboki.validation.report import report_key_version


def iter_metadatas():
    """Every declarative MetaData in the package.

    Listed here, in the test, rather than in ``core/versioned_key.py``: that module
    is the foundation layer and may not import the packages that own these stores
    (``tests/unit/test_layering.py`` enforces it). A new SQLAlchemy store must be
    added to this list -- ``test_every_declarative_store_is_enumerated`` fails if
    one is missing, so the list cannot silently go stale.
    """
    import fiboki.research.artefacts  # noqa: F401  (shares the experiment Base)
    from fiboki.data.versioning import Base as data_base
    from fiboki.research.experiment import Base as research_base
    from fiboki.validation.holdout import Base as holdout_base

    return (
        ("fiboki.research.experiment", research_base.metadata),
        ("fiboki.validation.holdout", holdout_base.metadata),
        ("fiboki.data.versioning", data_base.metadata),
    )


def test_every_declarative_store_is_enumerated() -> None:
    """The enumeration above must cover every ``__tablename__`` in ``src/``.

    Without this, the class test below can pass by omission: a new store with an
    unversioned key column is invisible to it until somebody remembers to add the
    module, and "somebody remembers" is what this whole change exists to replace.
    """
    import re
    from pathlib import Path

    src = Path(__file__).resolve().parents[2] / "src" / "fiboki"
    declared = {
        m.group(1)
        for path in src.rglob("*.py")
        for m in re.finditer(r'__tablename__\s*=\s*"([a-z_]+)"', path.read_text())
    }
    enumerated = {
        table.name for _module, metadata in iter_metadatas() for table in metadata.sorted_tables
    }
    assert declared <= enumerated, f"not covered by iter_metadatas(): {declared - enumerated}"

# --------------------------------------------------------------------------
# The class test
# --------------------------------------------------------------------------


def test_no_persisted_derived_key_lacks_a_version_companion() -> None:
    """Enumerates the whole package. A new offender fails here, not in production.

    The failure message names the table, the column, the companion to add, and the
    allowlist entry to write instead -- because a test that only says "no" gets
    deleted by whoever is in a hurry.
    """
    violations: list[str] = []
    for module, metadata in iter_metadatas():
        violations.extend(f"{module}: {v}" for v in audit_metadata(metadata))
    assert not violations, "\n".join(violations)


def test_every_exemption_names_a_column_that_exists() -> None:
    """The allowlist must not accumulate entries for columns that are long gone.

    A stale exemption is worse than none: it reads as "considered and accepted"
    while silently covering nothing, so the next column to take that name inherits
    a waiver nobody granted it.
    """
    known: set[tuple[str, str]] = set()
    for _module, metadata in iter_metadatas():
        for table in metadata.sorted_tables:
            for column in table.columns:
                known.add((table.name, column.name))
    stale = sorted(k for k in KNOWN_UNVERSIONED if k not in known)
    assert not stale, f"exemptions for columns that no longer exist: {stale}"


def test_every_exemption_carries_a_reason() -> None:
    for key, reason in KNOWN_UNVERSIONED.items():
        assert len(reason.strip()) > 40, f"{key} is exempted without an argument"


def test_the_known_ledger_and_registry_keys_are_all_versioned() -> None:
    """The three instances, named, so a regression on them reads plainly."""
    tables = {
        table.name: table
        for _module, metadata in iter_metadatas()
        for table in metadata.sorted_tables
    }
    expected = {
        "holdout_consumption": ("strategy_content_hash",),
        "experiment": (
            "strategy_content_hash",
            "structure_hash",
            "validation_report_hash",
        ),
        "research_artefact": ("content_hash",),
    }
    for name, columns in expected.items():
        present = {c.name for c in tables[name].columns}
        assert derived_key_columns(tables[name]) == columns
        for column in columns:
            assert key_version_column(column) in present


# --------------------------------------------------------------------------
# The mechanism detects the class, not just today's instances
# --------------------------------------------------------------------------


def test_a_new_unversioned_key_column_is_caught() -> None:
    """A table nobody has seen before, declared the obvious way, is flagged."""

    class Base(DeclarativeBase):
        pass

    class Offender(Base):
        __tablename__ = "some_future_table"

        id: Mapped[str] = mapped_column(String(16), primary_key=True)
        signal_content_hash: Mapped[str] = mapped_column(String(64))

    violations = audit_metadata(Base.metadata)
    assert len(violations) == 1
    assert "some_future_table.signal_content_hash" in violations[0]
    assert "signal_content_hash_key_version" in violations[0]
    with pytest.raises(MissingKeyVersion):
        require_key_versions(Base.metadata)


def test_the_same_column_with_a_companion_passes() -> None:
    class Base(DeclarativeBase):
        pass

    class Compliant(Base):
        __tablename__ = "some_future_table"

        id: Mapped[str] = mapped_column(String(16), primary_key=True)
        signal_content_hash: Mapped[str] = mapped_column(String(64))
        signal_content_hash_key_version: Mapped[str] = mapped_column(String(32))

    assert audit_metadata(Base.metadata) == ()
    require_key_versions(Base.metadata)


@pytest.mark.parametrize(
    "name",
    [
        "strategy_content_hash",
        "structure_hash",
        "validation_report_hash",
        "content_hash",
        "exit_policy_fingerprint",
        "content_checksum",
        "cell_key",
    ],
)
def test_the_naming_rule_recognises_a_derived_key(name: str) -> None:
    assert is_derived_key_column(name)


@pytest.mark.parametrize(
    "name",
    [
        "strategy_content_hash_key_version",
        "code_version",
        "strategy_version",
        "key_version",
        "dataset_version_id",
        "created_at",
        "payload_json",
    ],
)
def test_the_naming_rule_does_not_flag_a_plain_column(name: str) -> None:
    assert not is_derived_key_column(name)


def test_the_suffix_list_is_not_empty_and_covers_the_obvious_spellings() -> None:
    """Guards against the rule being narrowed into uselessness by a later edit."""
    for suffix in ("_hash", "_fingerprint", "_checksum", "_key"):
        assert suffix in DERIVED_KEY_SUFFIXES


# --------------------------------------------------------------------------
# The version values
# --------------------------------------------------------------------------


def test_the_strategy_key_version_tracks_the_dsl_schema(monkeypatch) -> None:
    from fiboki.strategy import dsl

    assert strategy_key_version() == f"dsl:{dsl.SCHEMA_VERSION}"
    monkeypatch.setattr("fiboki.strategy.dsl.SCHEMA_VERSION", "9.9.9")
    assert strategy_key_version() == "dsl:9.9.9"


def test_the_report_key_version_tracks_the_report_version(monkeypatch) -> None:
    from fiboki.validation import report

    assert report_key_version() == f"report:{report.REPORT_VERSION}"
    monkeypatch.setattr("fiboki.validation.report.REPORT_VERSION", "3.0.0")
    assert report_key_version() == "report:3.0.0"


def test_resolution_prefers_the_column_then_the_restatement() -> None:
    assert resolve_key_version("dsl:2.0.0", "dsl:1.0.0") == "dsl:2.0.0"
    assert resolve_key_version("", "dsl:1.0.0") == "dsl:1.0.0"
    assert resolve_key_version("", "") == UNSTAMPED
    assert resolve_key_version(None, None) == UNSTAMPED


def test_stamping_a_json_fingerprint_refuses_to_restamp() -> None:
    stamped = stamp_key_version({"a": 1}, "engine_v2")
    assert stamped == {"a": 1, "key_version": "engine_v2"}
    assert stamp_key_version(stamped, "engine_v2") == stamped
    with pytest.raises(MissingKeyVersion):
        stamp_key_version(stamped, "engine_v3")


# --------------------------------------------------------------------------
# The JSON fingerprints that have no companion column to hang a version on
# --------------------------------------------------------------------------


def test_the_persisted_engine_fingerprints_all_carry_a_key_version() -> None:
    from fiboki.backtest.engine import BacktestConfig
    from fiboki.backtest.exits import DEFAULT_EXIT_POLICY
    from fiboki.backtest.version import ENGINE_VERSION
    from fiboki.sim.profiles import IG_REALISTIC, PROFILE_FINGERPRINT_VERSION

    # The exit policy and the engine config are stamped with the engine generation,
    # which is bumped exactly when stored results stop being comparable. The
    # execution profile carries its OWN version: its fingerprint shape belongs to
    # sim/, which sits below backtest/ and must not reach up for a constant.
    assert DEFAULT_EXIT_POLICY.fingerprint()["key_version"] == ENGINE_VERSION
    assert (
        BacktestConfig(initial_balance=10_000.0).fingerprint()["key_version"]
        == ENGINE_VERSION
    )
    assert IG_REALISTIC.fingerprint()["key_version"] == PROFILE_FINGERPRINT_VERSION
    assert PROFILE_FINGERPRINT_VERSION


def test_the_validation_report_hash_is_self_describing() -> None:
    """The report hash covers ``report_version``, so it MOVES on a bump.

    Which is exactly why the ledger stores ``validation_report_hash_key_version``
    beside it: the hash says nothing about itself to a reader holding only the
    stored string.
    """
    from fiboki.validation.report import ValidationReport

    common = {
        "strategy_id": "x",
        "strategy_content_hash": "a" * 64,
        "dataset_version_id": "eurusd_h1_v7",
        "code_version": "abc1234",
    }
    report = ValidationReport(**common)
    assert "report_version" in report.to_dict()
    bumped = ValidationReport(**common, report_version="9.9.9")
    assert bumped.content_hash() != report.content_hash()
