"""Secret hygiene and a mode-guard structural pin (audit F P2-16, P2-21).

* ``OandaConfig`` printed its API token in its ``repr``, so any traceback or
  logged config leaked it.
* Operator passwords were unsalted SHA-256. They are now scrypt, with the old
  format still verifying so no operator is locked out by the upgrade.
* Nothing stopped a refactor from passing ``compiled_in=True`` to the mode
  guard (or ``live_host_compiled_in=True`` to the OANDA config) from src/.
"""
from __future__ import annotations

import ast
import hashlib
from pathlib import Path

import pytest

from fiboki.api.routers.auth import (
    SCRYPT_PREFIX,
    hash_password,
    is_legacy_hash,
    verify_password,
)
from fiboki.broker.oanda import OandaConfig

SRC = Path(__file__).resolve().parents[2] / "src" / "fiboki"


def test_the_oanda_token_is_not_in_the_repr() -> None:
    config = OandaConfig(account_id="101-004-1", api_token="tok-SECRET-123")
    assert "tok-SECRET-123" not in repr(config)
    assert "101-004-1" in repr(config)
    assert config.api_token == "tok-SECRET-123"


def test_scrypt_hashes_are_salted_and_verify() -> None:
    first, second = hash_password("correct horse"), hash_password("correct horse")
    assert first.startswith(SCRYPT_PREFIX) and first != second  # salted
    assert ":" not in first and "," not in first  # fits FIBOKI_OPERATORS
    assert verify_password("correct horse", first)
    assert not verify_password("correct horsE", first)
    assert not is_legacy_hash(first)


@pytest.mark.parametrize("prefix", ["", "sha256$"])
def test_legacy_sha256_entries_still_verify_and_are_flagged(prefix: str) -> None:
    legacy = prefix + hashlib.sha256(b"old password").hexdigest()
    assert verify_password("old password", legacy)
    assert verify_password("old password", legacy.upper() if not prefix else legacy)
    assert not verify_password("wrong", legacy)
    assert is_legacy_hash(legacy)


@pytest.mark.parametrize("stored", ["scrypt$", "scrypt$x$y$z$a$b", "scrypt$16384$8$1$!!$!!", ""])
def test_malformed_entries_refuse_rather_than_raise(stored: str) -> None:
    assert verify_password("anything", stored) is False


def test_src_never_passes_compiled_in_or_live_host_compiled_in() -> None:
    """Those parameters exist so TESTS can prove the other controls still bind.

    Production code must take the module constants. A call in src/ passing
    either keyword is a refactor quietly routing the build-time live control
    through a value, which is the V1 failure mode (AGENTS.md §1).
    """
    offenders: list[str] = []
    for path in SRC.rglob("*.py"):
        tree = ast.parse(path.read_text(), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                for kw in node.keywords:
                    if kw.arg in {"compiled_in", "live_host_compiled_in"}:
                        offenders.append(f"{path.relative_to(SRC)}:{node.lineno} {kw.arg}=")
    assert not offenders, offenders
