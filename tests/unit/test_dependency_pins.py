"""Pins that `pip install -e .` must honour without a constraints file.

`deploy/constraints.txt` and `deploy/requirements.lock` are the right place for
a transitive pin **when the installer reads them**. They are opt-in:
``pip install -e '.[dev]'`` — the install an operator actually types — reads
neither. Anything whose absence breaks the product on a clean resolve therefore
has to be in ``pyproject.toml`` as well.

The concrete failure this file exists for: typer 0.15.1 calls
``click.Parameter.make_metavar()`` with the pre-8.2 signature. A clean resolve
picks up click 8.5.0, and then EVERY ``fiboki --help`` raises
``TypeError: Parameter.make_metavar() missing 1 required positional argument``.
Nothing else in the suite catches it, because a broken CLI is a broken operator
tool rather than a wrong number, and no test in this repository invoked
``--help`` before this one.
"""
from __future__ import annotations

import subprocess
import sys
import tomllib
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]

#: Transitive dependencies that must be pinned in ``pyproject.toml`` itself,
#: with the reason each one is not merely a constraints-file concern.
REQUIRED_TRANSITIVE_PINS = {
    "click": (
        "typer 0.15.1 uses the pre-8.2 Parameter.make_metavar() signature; "
        "click >= 8.2 breaks every `fiboki --help`."
    ),
}


def _dependencies() -> dict[str, str]:
    raw = tomllib.loads((REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    out: dict[str, str] = {}
    for spec in raw["project"]["dependencies"]:
        name, _, version = spec.partition("==")
        out[name.strip().lower()] = version.strip()
    return out


def _lock_versions() -> dict[str, str]:
    out: dict[str, str] = {}
    for line in (REPO_ROOT / "deploy" / "requirements.lock").read_text().splitlines():
        line = line.split("#", 1)[0].strip()
        if not line or "==" not in line:
            continue
        name, _, version = line.partition("==")
        out[name.strip().lower()] = version.strip().split()[0]
    return out


@pytest.mark.parametrize("package", sorted(REQUIRED_TRANSITIVE_PINS))
def test_transitive_pin_is_in_pyproject(package: str):
    deps = _dependencies()
    assert package in deps, (
        f"{package} is not pinned in pyproject.toml. "
        + REQUIRED_TRANSITIVE_PINS[package]
        + " A pin that lives only in deploy/constraints.txt does not apply to "
        "`pip install -e .`, which is the install that actually happens."
    )


def test_every_pyproject_dependency_is_an_exact_pin():
    """`>=` in this file is how "the same code" becomes "a different answer"."""
    raw = tomllib.loads((REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    loose = [s for s in raw["project"]["dependencies"] if "==" not in s]
    assert not loose, f"unpinned dependencies: {loose}"


def test_pyproject_pins_agree_with_the_lockfile():
    """Two files naming two versions of the same package is a result that
    depends on which one the installer happened to read."""
    lock = _lock_versions()
    disagreements = [
        f"{name}: pyproject {version} vs lock {lock[name]}"
        for name, version in _dependencies().items()
        if name in lock and lock[name] != version
    ]
    assert not disagreements, "\n".join(disagreements)


def test_the_cli_help_actually_runs():
    """The end the pin exists for. Runs the real entry point in a subprocess,
    because an in-process import of the app object does not build the help
    text and would pass against a broken click."""
    proc = subprocess.run(
        [sys.executable, "-m", "fiboki.cli", "--help"],
        capture_output=True,
        text=True,
        cwd=str(REPO_ROOT),
        timeout=120,
        check=False,
    )
    assert proc.returncode == 0, (
        f"`fiboki --help` exited {proc.returncode}\n"
        f"STDOUT:\n{proc.stdout}\nSTDERR:\n{proc.stderr}"
    )
    assert "make_metavar" not in proc.stderr
    assert "Usage:" in proc.stdout


def test_installed_click_matches_the_pin():
    import click

    assert click.__version__ == _dependencies()["click"], (
        f"the environment has click {click.__version__} but pyproject pins "
        f"{_dependencies()['click']}; this environment cannot prove anything "
        "about the pinned one"
    )
