"""Confidence bounds for a proportion estimated from few trials.

A walk-forward hit rate is a proportion over a handful of folds. "3 of 5 windows
were profitable" is ``0.6`` as a point estimate and almost nothing as evidence:
with five trials the sampling error is larger than the gap between a coin and a
strategy. A gate that reads the point estimate therefore has little power to
separate the two; one that reads a lower confidence bound says how sure the
folds make us, and is the form the audit proposes (F_backend_audit section 3.1).

Reference
---------
Wilson, E. B. (1927). "Probable Inference, the Law of Succession, and
    Statistical Inference", Journal of the American Statistical Association
    22(158), 209-212.
"""
from __future__ import annotations

import math

from scipy import stats as _sps

__all__ = ["wilson_lower_bound"]


def wilson_lower_bound(successes: int, trials: int, confidence: float = 0.95) -> float | None:
    """One-sided Wilson score lower bound for a binomial proportion.

    ``(p + z^2/(2n) - z * sqrt(p(1-p)/n + z^2/(4n^2))) / (1 + z^2/n)`` with
    ``p = successes / trials`` and ``z = Z^-1(confidence)`` (one-sided, so
    1.6449 at 95%). Unlike the normal approximation it stays inside ``[0, 1]``
    and is informative at ``p = 0`` or ``1``, which with five folds are common.

    Returns ``None`` when ``trials < 1``: no trials is no estimate, not zero.
    """
    if not 0.0 < confidence < 1.0:
        raise ValueError("confidence must be in (0, 1)")
    n = int(trials)
    k = int(successes)
    if n < 1:
        return None
    if not 0 <= k <= n:
        raise ValueError(f"successes must be in [0, trials]; got {k} of {n}")
    z = float(_sps.norm.ppf(confidence))
    p = k / n
    z2 = z * z
    centre = p + z2 / (2.0 * n)
    spread = z * math.sqrt(p * (1.0 - p) / n + z2 / (4.0 * n * n))
    return max(0.0, (centre - spread) / (1.0 + z2 / n))
