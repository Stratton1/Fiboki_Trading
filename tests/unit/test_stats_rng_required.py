"""Every ``rng`` in the resampling statistics is a REQUIRED argument (audit P3-2).

``np.random.default_rng(None)`` draws from OS entropy. A bootstrap, SPA or
stress call that forgot its seed would therefore produce a different confidence
interval, p-value or stress curve on every run -- a non-reproducible research
number that looks exactly like a reproducible one. Every current caller seeds;
this test is what keeps a future one from not seeding. It is an AST test
because "nobody can call this without a seed" is a property of signatures, not
of any one call.
"""
from __future__ import annotations

import ast
from pathlib import Path

import numpy as np
import pytest

from fiboki.stats.bootstrap import iid_bootstrap_indices, require_rng
from fiboki.stats.spa import superior_predictive_ability
from fiboki.stats.stress import spread_multiplier_stress

SRC = Path(__file__).resolve().parents[2] / "src" / "fiboki" / "stats"
MODULES = ("bootstrap.py", "spa.py", "stress.py")


def _functions_with_rng():
    for name in MODULES:
        tree = ast.parse((SRC / name).read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.FunctionDef):
                continue
            args = node.args
            positional = args.posonlyargs + args.args
            pos_defaults = dict(
                zip(
                    [a.arg for a in positional][len(positional) - len(args.defaults):],
                    args.defaults,
                    strict=True,
                )
            )
            kw_defaults = {
                a.arg: d for a, d in zip(args.kwonlyargs, args.kw_defaults, strict=True)
            }
            names = [a.arg for a in positional] + [a.arg for a in args.kwonlyargs]
            if "rng" in names:
                default = pos_defaults.get("rng", kw_defaults.get("rng"))
                annotation = next(
                    a.annotation for a in positional + args.kwonlyargs if a.arg == "rng"
                )
                yield f"{name}:{node.name}", default, annotation


FOUND = list(_functions_with_rng())


def test_the_scan_found_the_resampling_functions() -> None:
    names = {n for n, _, _ in FOUND}
    for expected in (
        "bootstrap.py:stationary_bootstrap_indices",
        "bootstrap.py:bootstrap_confidence_interval",
        "spa.py:superior_predictive_ability",
        "spa.py:step_m",
        "stress.py:random_deletion_stress",
        "stress.py:run_stress_suite",
    ):
        assert expected in names
    assert len(FOUND) >= 20


@pytest.mark.parametrize(("where", "default", "annotation"), FOUND, ids=[f[0] for f in FOUND])
def test_rng_has_no_default_and_does_not_admit_none(where, default, annotation) -> None:
    assert default is None, f"{where}: rng has a default ({ast.unparse(default)})"
    if annotation is not None:
        assert "None" not in ast.unparse(annotation), f"{where}: rng is typed Optional"


def test_none_is_refused_at_runtime_too() -> None:
    with pytest.raises(TypeError, match="rng is required"):
        require_rng(None)  # type: ignore[arg-type]
    with pytest.raises(TypeError, match="rng is required"):
        iid_bootstrap_indices(10, 5, None)  # type: ignore[arg-type]


def test_omitting_rng_is_a_type_error() -> None:
    with pytest.raises(TypeError):
        superior_predictive_ability(np.zeros((50, 2)))  # type: ignore[call-arg]
    with pytest.raises(TypeError):
        spread_multiplier_stress([])  # type: ignore[call-arg]
