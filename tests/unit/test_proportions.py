"""The one-sided Wilson lower bound the E-2 hit-rate candidate reads."""
from __future__ import annotations

import math

import pytest

from fiboki.stats.proportions import wilson_lower_bound

Z95 = 1.6448536269514722


def _by_hand(k: int, n: int) -> float:
    p = k / n
    z2 = Z95**2
    return (p + z2 / (2 * n) - Z95 * math.sqrt(p * (1 - p) / n + z2 / (4 * n * n))) / (1 + z2 / n)


@pytest.mark.parametrize(
    ("k", "n", "expected"),
    [
        # 5 of 5: (1 + 0.27055 - 1.64485 * 0.16449) / 1.54110 = 0.6488
        (5, 5, 0.6488),
        # 4 of 5: (0.8 + 0.27055 - 1.64485 * 0.24303) / 1.54110 = 0.4353
        (4, 5, 0.4353),
        (3, 5, 0.2725),
        # 7 of 8 clears 0.5; 6 of 8 does not
        (7, 8, 0.5888),
        (6, 8, 0.4601),
    ],
)
def test_hand_computed_values(k: int, n: int, expected: float) -> None:
    assert wilson_lower_bound(k, n) == pytest.approx(_by_hand(k, n), abs=1e-12)
    assert wilson_lower_bound(k, n) == pytest.approx(expected, abs=5e-4)


def test_with_five_folds_only_five_of_five_clears_one_half() -> None:
    """The candidate gate (> 0.5) at today's 5 folds is STRICTER than 3 of 5."""
    assert [k for k in range(6) if wilson_lower_bound(k, 5) > 0.5] == [5]


def test_zero_successes_is_zero_and_no_trials_is_none() -> None:
    assert wilson_lower_bound(0, 5) == pytest.approx(0.0, abs=1e-12)
    assert wilson_lower_bound(0, 0) is None


@pytest.mark.parametrize(("k", "n", "conf"), [(6, 5, 0.95), (-1, 5, 0.95), (1, 5, 1.0)])
def test_rejects_impossible_inputs(k: int, n: int, conf: float) -> None:
    with pytest.raises(ValueError):
        wilson_lower_bound(k, n, conf)
