"""The paper maturity report: ADVISORY, and it enables nothing.

The question an operator asks before even considering an IG demo account for a
strategy is "has the paper record shown me enough, and is it the record the
backtest predicted?". This answers it item by item, pass / fail /
not_evaluated, from the platform's own records:

``min_trades``           paper closed trades, against a floor.
``min_days``             days the session has been live, against a floor.
``drawdown_in_band``     the paper's realised max drawdown (closed-trade
                         balance curve) against the backtest's drawdown band AT
                         THE SAME NUMBER OF TRADES: the stored backtest run's R
                         multiples, resampled (stationary bootstrap, seeded) and
                         re-sized at the session's risk fraction off running
                         equity (:func:`fiboki.stats.bootstrap.resample_with_compounding`),
                         max drawdown over the first n trades of each path.
                         Pass when the paper's is at or below the band's 95th
                         percentile.
``costs_as_expected``    realised cost per unit traded against the backtest's
                         per instrument, weighted by what the paper traded.
``reconciliation_clean`` the latest reconciliation has no unexplained
                         divergence and covers the session's latest trade.
``audit_chain_intact``   the paper audit chain verifies from genesis.

``ready`` is true only when EVERY item passes; ``not_evaluated`` blocks, as
``GateStatus.NOT_EVALUATED`` blocks promotion. The thresholds are
:data:`ADVISORY_MATURITY_V1`, versioned, and gate nothing: no lifecycle state,
no execution mode, no limit and no flag reads this report. Reaching IG demo
still needs every control in ``docs/v2/EXECUTION_ARCHITECTURE.md`` section 6
and a reviewed change. A strategy that passes every item has shown the paper
record agrees with its backtest; it has not shown it has an edge.
"""
from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass, field
from typing import Any

import numpy as np

from fiboki.stats.bootstrap import optimal_block_length, resample_with_compounding

__all__ = [
    "ADVISORY_MATURITY_V1",
    "MATURITY_SEED",
    "MaturityCriteria",
    "MaturityItem",
    "MaturityReport",
    "drawdown_band",
    "maturity_report",
]

MATURITY_SEED = 20261019
ADVISORY_NOTE = (
    "Advisory only. This report enables nothing: no lifecycle state, execution mode, limit or "
    "flag reads it. Moving to an IG demo account still requires every control in "
    "EXECUTION_ARCHITECTURE section 6 and a reviewed change. Passing every item shows the paper "
    "record agrees with its backtest, not that the strategy has an edge."
)


@dataclass(frozen=True, slots=True)
class MaturityCriteria:
    version: str
    min_trades: int
    min_days: float
    drawdown_percentile: float
    max_cost_ratio: float
    bootstrap_paths: int


ADVISORY_MATURITY_V1 = MaturityCriteria(
    version="maturity_advisory_v1",
    min_trades=30,
    min_days=28.0,
    drawdown_percentile=95.0,
    max_cost_ratio=1.25,
    bootstrap_paths=2000,
)


@dataclass(frozen=True, slots=True)
class MaturityItem:
    key: str
    label: str
    status: str  # pass | fail | not_evaluated
    value: float | None
    threshold: float | None
    unit: str
    detail: str


@dataclass(frozen=True, slots=True)
class DrawdownBand:
    horizon_trades: int
    source_trades: int
    n_paths: int
    seed: int
    risk_fraction: float
    block_length: float
    p5: float
    p50: float
    p95: float


@dataclass(frozen=True, slots=True)
class MaturityReport:
    criteria: MaturityCriteria
    ready: bool
    verdict: str  # ready_to_consider | not_ready | not_evaluated
    items: tuple[MaturityItem, ...]
    band: DrawdownBand | None
    paper_max_drawdown_pct: float | None
    realised_cost_per_unit: float | None
    expected_cost_per_unit: float | None
    note: str = ADVISORY_NOTE
    notes: tuple[str, ...] = field(default_factory=tuple)

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def _max_dd_pct(balances: np.ndarray) -> float:
    if balances.size < 2:
        return 0.0
    peak = np.maximum.accumulate(balances)
    dd = np.where(peak > 0, (peak - balances) / peak, 0.0)
    return float(np.max(dd) * 100.0)


def drawdown_band(
    r_multiples: Sequence[float],
    *,
    horizon: int,
    risk_fraction: float,
    n_paths: int,
    seed: int = MATURITY_SEED,
) -> DrawdownBand:
    """Max-drawdown percentiles over the first ``horizon`` trades of resampled paths."""
    series = np.asarray(list(r_multiples), dtype=float)
    if series.size < 2:
        raise ValueError("a drawdown band needs at least two backtest trades with an R multiple")
    block = optimal_block_length(series).stationary if series.size >= 8 else 1.0
    sim = resample_with_compounding(
        series,
        n_paths=int(n_paths),
        risk_fraction=float(risk_fraction),
        initial_equity=1.0,
        ruin_fraction=0.0,
        block_length=block,
        rng=int(seed),
        keep_paths=True,
    )
    paths = sim.equity_paths
    assert paths is not None
    k = max(1, min(int(horizon), paths.shape[1] - 1))
    window = paths[:, : k + 1]
    peak = np.maximum.accumulate(window, axis=1)
    dd = np.max(np.where(peak > 0, (peak - window) / peak, 0.0), axis=1) * 100.0
    p5, p50, p95 = (float(np.percentile(dd, q)) for q in (5.0, 50.0, 95.0))
    return DrawdownBand(
        horizon_trades=k, source_trades=int(series.size), n_paths=int(n_paths), seed=int(seed),
        risk_fraction=float(risk_fraction), block_length=float(block), p5=p5, p50=p50, p95=p95,
    )


def _cost(t: Mapping[str, Any]) -> float:
    if t.get("costs") is not None:
        return float(t["costs"])
    return float(sum(float(t.get(k) or 0.0) for k in ("spread_cost", "commission", "slippage_cost", "financing_cost")))


def maturity_report(
    *,
    paper_trades: Sequence[Mapping[str, Any]],
    initial_balance: float | None,
    risk_fraction: float | None,
    days_live: float | None,
    backtest_r_multiples: Sequence[float] | None,
    backtest_trades: Sequence[Mapping[str, Any]] | None,
    backtest_label: str,
    reconciliation: Mapping[str, Any] | None,
    latest_paper_trade_at: str | None,
    chain: Mapping[str, Any] | None,
    criteria: MaturityCriteria = ADVISORY_MATURITY_V1,
) -> MaturityReport:
    c = criteria
    items: list[MaturityItem] = []
    notes: list[str] = []
    n = len(paper_trades)

    items.append(MaturityItem(
        "min_trades", "Paper closed trades", "pass" if n >= c.min_trades else "fail",
        float(n), float(c.min_trades), "count",
        f"{n} closed paper trade(s); the advisory floor is {c.min_trades}.",
    ))
    if days_live is None:
        items.append(MaturityItem("min_days", "Days live", "not_evaluated", None, c.min_days, "days",
                                  "The session's first bar is not known."))
    else:
        items.append(MaturityItem(
            "min_days", "Days live", "pass" if days_live >= c.min_days else "fail",
            round(days_live, 2), c.min_days, "days",
            f"{days_live:.1f} day(s) from the session's first bar to its last; the advisory floor is {c.min_days:g}.",
        ))

    # -- drawdown against the backtest's band at the same trade count ----------
    paper_dd: float | None = None
    band: DrawdownBand | None = None
    if initial_balance and initial_balance > 0:
        ordered = sorted(paper_trades, key=lambda t: str(t.get("exit_time")))
        balances = np.concatenate([[initial_balance], initial_balance + np.cumsum([float(t["net_pnl"]) for t in ordered])])
        paper_dd = _max_dd_pct(balances)
    if paper_dd is None:
        items.append(MaturityItem("drawdown_in_band", "Drawdown vs backtest band", "not_evaluated", None, None,
                                  "pct", "The session's initial balance is not known."))
    elif not risk_fraction:
        items.append(MaturityItem("drawdown_in_band", "Drawdown vs backtest band", "not_evaluated", paper_dd,
                                  None, "pct", "The session's risk fraction is not recorded, so the backtest "
                                  "cannot be re-sized to it."))
    elif not backtest_r_multiples or len(backtest_r_multiples) < 2:
        items.append(MaturityItem("drawdown_in_band", "Drawdown vs backtest band", "not_evaluated", paper_dd,
                                  None, "pct", f"No stored backtest with at least two R multiples ({backtest_label})."))
    elif n == 0:
        items.append(MaturityItem("drawdown_in_band", "Drawdown vs backtest band", "not_evaluated", paper_dd,
                                  None, "pct", "No paper trade yet, so there is no drawdown to compare."))
    else:
        band = drawdown_band(backtest_r_multiples, horizon=n, risk_fraction=float(risk_fraction),
                             n_paths=c.bootstrap_paths)
        within = paper_dd <= band.p95
        items.append(MaturityItem(
            "drawdown_in_band", "Drawdown vs backtest band", "pass" if within else "fail",
            round(paper_dd, 3), round(band.p95, 3), "pct",
            f"Paper max drawdown {paper_dd:.2f}% over {n} trade(s); the backtest's band at {band.horizon_trades} "
            f"trade(s) is p5 {band.p5:.2f}%, p50 {band.p50:.2f}%, p95 {band.p95:.2f}% "
            f"({band.n_paths} seeded paths from {backtest_label}).",
        ))
        if band.horizon_trades < n:
            notes.append(f"The backtest has fewer trades ({band.source_trades}) than the paper record; the band is "
                         f"at its full length of {band.horizon_trades}.")

    # -- realised versus expected cost per unit traded -------------------------
    realised = expected = None
    if not backtest_trades:
        items.append(MaturityItem("costs_as_expected", "Realised vs expected costs", "not_evaluated", None,
                                  c.max_cost_ratio, "ratio", f"No stored backtest trades ({backtest_label})."))
    else:
        per_unit: dict[str, list[float]] = {}
        for t in backtest_trades:
            size = abs(float(t.get("size") or 0.0))
            if size > 0:
                per_unit.setdefault(str(t.get("instrument")), []).append(_cost(t) / size)
        paid = owed = 0.0
        missing: set[str] = set()
        for t in paper_trades:
            sym = str(t.get("instrument"))
            size = abs(float(t.get("size") or 0.0))
            if sym not in per_unit or size <= 0:
                missing.add(sym)
                continue
            paid += _cost(t)
            owed += size * float(np.mean(per_unit[sym]))
        if owed <= 0:
            items.append(MaturityItem(
                "costs_as_expected", "Realised vs expected costs", "not_evaluated", None, c.max_cost_ratio, "ratio",
                "No paper trade on an instrument the backtest traded" + (f" ({sorted(missing)})" if missing else "") + ".",
            ))
        else:
            ratio = paid / owed
            total_units = sum(abs(float(t.get("size") or 0.0)) for t in paper_trades if str(t.get("instrument")) in per_unit)
            realised = paid / total_units if total_units else None
            expected = owed / total_units if total_units else None
            items.append(MaturityItem(
                "costs_as_expected", "Realised vs expected costs", "pass" if ratio <= c.max_cost_ratio else "fail",
                round(ratio, 4), c.max_cost_ratio, "ratio",
                f"Paper paid {ratio:.2f}x the backtest's cost per unit traded on the same instruments"
                + (f"; {sorted(missing)} not compared (not in the backtest)" if missing else "") + ".",
            ))

    # -- reconciliation --------------------------------------------------------
    if not reconciliation:
        items.append(MaturityItem("reconciliation_clean", "Reconciliation", "not_evaluated", None, 0.0, "count",
                                  "No reconciliation report: request one on this bot's page."))
    else:
        status = str(reconciliation.get("status"))
        unexplained = int(reconciliation.get("unexplained") or 0)
        window_end = reconciliation.get("window_end")
        stale = bool(latest_paper_trade_at and window_end and str(latest_paper_trade_at) > str(window_end))
        if status == "not_evaluated":
            items.append(MaturityItem("reconciliation_clean", "Reconciliation", "not_evaluated", None, 0.0, "count",
                                      f"The latest reconciliation could not run: {reconciliation.get('status_reason')}"))
        elif stale:
            items.append(MaturityItem("reconciliation_clean", "Reconciliation", "not_evaluated", float(unexplained),
                                      0.0, "count", f"The latest report covers to {window_end}; the paper has traded "
                                      "since. Request a new one."))
        else:
            items.append(MaturityItem(
                "reconciliation_clean", "Reconciliation", "pass" if unexplained == 0 else "fail",
                float(unexplained), 0.0, "count",
                f"{status}: {reconciliation.get('status_reason')}.",
            ))

    # -- audit chain -------------------------------------------------------------
    if not chain or not chain.get("present"):
        items.append(MaturityItem("audit_chain_intact", "Audit chain", "not_evaluated", None, None, "count",
                                  "No audit chain for this session (it predates Wave 3O, or has not started)."))
    else:
        items.append(MaturityItem(
            "audit_chain_intact", "Audit chain", "pass" if chain.get("intact") else "fail",
            float(chain.get("rows") or 0), None, "count", str(chain.get("reason")),
        ))

    statuses = {i.status for i in items}
    ready = statuses == {"pass"}
    verdict = "ready_to_consider" if ready else ("not_ready" if "fail" in statuses else "not_evaluated")
    return MaturityReport(
        criteria=c, ready=ready, verdict=verdict, items=tuple(items), band=band,
        paper_max_drawdown_pct=paper_dd, realised_cost_per_unit=realised, expected_cost_per_unit=expected,
        notes=tuple(notes),
    )
