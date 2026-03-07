"""Filtering utilities for research results."""

from fibokei.research.matrix import ResearchResult


def apply_minimum_trade_filter(
    results: list[ResearchResult], min_trades: int = 80
) -> tuple[list[ResearchResult], list[ResearchResult]]:
    """Split results into qualified (>= min_trades) and insufficient."""
    qualified = []
    insufficient = []
    for r in results:
        if r.total_trades >= min_trades:
            qualified.append(r)
        else:
            insufficient.append(r)
    return qualified, insufficient


def apply_exploratory_filter(
    results: list[ResearchResult], min_trades: int = 40
) -> list[ResearchResult]:
    """Return results with 40-79 trades, marked as exploratory."""
    exploratory = []
    for r in results:
        if min_trades <= r.total_trades < 80:
            r.status = "exploratory"
            exploratory.append(r)
    return exploratory
