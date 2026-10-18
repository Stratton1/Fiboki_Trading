"""The limit board: every limit, how much of it is used, and its band.

Before this module the WORKSTATION decided these things (``apps/web/lib/
limits-core.ts``): it divided two API figures to get a utilisation, compared
the result against bands it invented (70% and 90%), compared values against
limits to call a breach, and sorted rows by urgency. The attention queue, on
the server, decided breaches separately. Two deciders for one fact can
disagree, and an operator then sees a green bar under a red alarm.

Now the server decides once:

* ``utilisation_pct`` and ``headroom`` are computed here, by the rule the
  gateway's accounting applies (daily loss breaches below minus the limit,
  drawdown above the limit, exposure above its limit);
* ``band`` is ``ok`` / ``warn`` / ``critical`` / ``breached`` / ``absent``,
  from :data:`DISPLAY_BANDS`;
* rows and families arrive ORDERED, most used first, absent rows last;
* :func:`attention_items` turns the same rows into attention-queue items, so
  the queue and the bars are the same decision rendered twice.

The display bands colour a bar and rank the queue. They never gate an order:
the gateway's own checks (``risk/``) do that, against the limit set in force.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from fiboki.api import links
from fiboki.api.models import SourceNote
from fiboki.api.provenance import Figure
from fiboki.core.enums import Provenance

__all__ = [
    "DISPLAY_BANDS",
    "BandsView",
    "LimitBoardView",
    "LimitFamilyView",
    "LimitRowView",
    "band_of",
    "build_limit_board",
]


@dataclass(frozen=True, slots=True)
class DisplayBands:
    """Percentages of a limit at which a bar turns NEAR and CRITICAL."""

    warn_at_pct: float = 70.0
    critical_at_pct: float = 90.0


#: The platform's display bands. The limit set (``risk/limits``) carries no
#: per-limit warning thresholds, so these are one platform-wide pair, stated
#: here and sent with every board so the workstation never invents its own.
DISPLAY_BANDS = DisplayBands()

FAMILY_TITLES: dict[str, str] = {
    "account": "Account",
    "loss": "Daily / weekly loss",
    "drawdown": "Drawdown",
    "margin": "Margin",
    "correlation": "Correlation",
    "exposure": "Exposure",
    "data_quality": "Data quality",
}
FAMILY_ORDER = tuple(FAMILY_TITLES)


class BandsView(BaseModel):
    model_config = ConfigDict(frozen=True)

    warn_at: Figure
    critical_at: Figure
    detail: str


class LimitRowView(BaseModel):
    """One limit. Every decision on it was made by the server."""

    model_config = ConfigDict(frozen=True)

    key: str
    family: str
    label: str
    #: Exposure rows: ``instrument``, ``strategy`` or ``currency``.
    kind: str | None = None
    value: Figure | None = None
    limit: Figure | None = None
    utilisation_pct: Figure
    #: Distance from the value to the breach boundary, in the value's unit.
    headroom: Figure
    band: str = Field(pattern="^(ok|warn|critical|breached|absent)$")
    breached: bool
    #: How utilisation and headroom were computed, in words.
    formula: str | None = None
    absent_reason: str | None = None
    see_also: str | None = None
    deep_link: str
    #: 0 (unused) to 5 (at or over the limit): the exposure matrix's heat step,
    #: 20% of the limit per step. ``None`` when there is no utilisation.
    heat: int | None = None


class LimitFamilyView(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: str
    title: str
    #: Row keys in display order (most used first).
    rows: list[str]
    worst_key: str | None
    #: Where this family's figures come from (an endpoint), or ``none``.
    source: str


class LimitBoardView(BaseModel):
    model_config = ConfigDict(frozen=True)

    limits_version: str
    #: Every row, ordered: breached, then most used, then absent rows.
    rows: list[LimitRowView]
    #: Families ordered by their most-used row; families with nothing measured
    #: follow in canonical order.
    families: list[LimitFamilyView]
    closest_key: str | None
    bands: BandsView
    sources: dict[str, SourceNote]


def band_of(utilisation: float | None, breached: bool, bands: DisplayBands = DISPLAY_BANDS) -> str:
    if breached:
        return "breached"
    if utilisation is None:
        return "absent"
    if utilisation >= bands.critical_at_pct:
        return "critical"
    if utilisation >= bands.warn_at_pct:
        return "warn"
    return "ok"


def _heat(utilisation: float | None) -> int | None:
    if utilisation is None:
        return None
    if not utilisation > 0:
        return 0
    steps = int(-(-utilisation // 20))  # ceil without float drift on exact steps
    return min(5, steps)


def _missing_reason(fig: Figure) -> str | None:
    if fig.value is not None:
        return None
    for caveat in fig.caveats:
        if caveat.code == "value_unavailable":
            return caveat.message
    return "The API returned no value for this figure."


def _absent(
    family: str,
    key: str,
    label: str,
    reason: str,
    provenance: Provenance,
    *,
    value: Figure | None = None,
    limit: Figure | None = None,
    see_also: str | None = None,
    kind: str | None = None,
    breached: bool = False,
    deep_link: str = links.RISK,
) -> LimitRowView:
    return LimitRowView(
        key=key,
        family=family,
        label=label,
        kind=kind,
        value=value,
        limit=limit,
        utilisation_pct=Figure.missing(provenance, unit="pct", reason=reason),
        headroom=Figure.missing(provenance, unit="pct", reason=reason),
        band="breached" if breached else "absent",
        breached=breached,
        absent_reason=reason,
        see_also=see_also,
        deep_link=deep_link,
        heat=None,
    )


def _measured(
    family: str,
    key: str,
    label: str,
    *,
    value: Figure,
    limit: Figure,
    utilisation: float,
    headroom: float,
    breached: bool,
    formula: str,
    kind: str | None = None,
    deep_link: str = links.RISK,
    utilisation_estimated: bool | None = None,
) -> LimitRowView:
    p = value.provenance
    estimated = value.estimated or limit.estimated
    return LimitRowView(
        key=key,
        family=family,
        label=label,
        kind=kind,
        value=value,
        limit=limit,
        utilisation_pct=Figure(
            value=round(utilisation, 2),
            provenance=p,
            unit="pct",
            as_of=value.as_of,
            estimated=estimated if utilisation_estimated is None else utilisation_estimated,
        ),
        headroom=Figure(
            value=round(headroom, 4), provenance=p, unit="pct", as_of=value.as_of, estimated=estimated
        ),
        band=band_of(utilisation, breached),
        breached=breached,
        formula=formula,
        deep_link=deep_link,
        heat=_heat(utilisation),
    )


def _risk_rows(risk: Any) -> list[LimitRowView]:
    """Daily loss, weekly loss, drawdown, margin from ``RiskStateView``."""
    p = risk.max_daily_loss_pct.provenance
    rows: list[LimitRowView] = []

    daily, daily_limit = risk.daily_loss_pct, risk.max_daily_loss_pct
    if daily.value is None or daily_limit.value is None or not daily_limit.value > 0:
        rows.append(
            _absent(
                "loss",
                "risk:daily_loss",
                "Daily loss",
                _missing_reason(daily)
                or _missing_reason(daily_limit)
                or "No usable daily-loss limit is in force.",
                p,
                value=daily,
            )
        )
    else:
        used = max(0.0, -daily.value)
        rows.append(
            _measured(
                "loss",
                "risk:daily_loss",
                "Daily loss",
                value=daily,
                limit=daily_limit,
                utilisation=used / daily_limit.value * 100.0,
                headroom=daily.value + daily_limit.value,
                breached=daily.value < -daily_limit.value,
                formula="used = the day's loss ÷ the limit; headroom = day P&L + limit "
                "(breached below -limit)",
            )
        )

    rows.append(
        _absent(
            "loss",
            "risk:weekly_loss",
            "Weekly loss",
            "Not reported: the gateway has a weekly-loss check, but no weekly figure or "
            "limit is exposed by the API.",
            p,
        )
    )

    dd, dd_limit = risk.drawdown_pct, risk.max_drawdown_limit_pct
    if dd.value is None or dd_limit.value is None or not dd_limit.value > 0:
        rows.append(
            _absent(
                "drawdown",
                "risk:drawdown",
                "Total drawdown",
                _missing_reason(dd)
                or _missing_reason(dd_limit)
                or "No usable drawdown limit is in force.",
                p,
                value=dd,
            )
        )
    else:
        rows.append(
            _measured(
                "drawdown",
                "risk:drawdown",
                "Total drawdown",
                value=dd,
                limit=dd_limit,
                utilisation=dd.value / dd_limit.value * 100.0,
                headroom=dd_limit.value - dd.value,
                breached=dd.value > dd_limit.value,
                formula="used = drawdown ÷ limit; headroom = limit - drawdown "
                "(breached above the limit)",
            )
        )

    margin = risk.margin_utilisation_pct
    rows.append(
        _absent(
            "margin",
            "risk:margin",
            "Margin utilisation",
            _missing_reason(margin)
            or "The API reports margin utilisation but no margin limit, so how close it "
            "is to one is unknown.",
            p,
            value=margin,
        )
    )
    return rows


def _exposure_rows(items: list[Any]) -> list[LimitRowView]:
    rows: list[LimitRowView] = []
    for row in items:
        kind = row.key.split(":", 1)[0] if ":" in row.key else "bucket"
        key = f"exposure:{row.key}"
        value, limit, util = row.exposure_pct, row.limit_pct, row.utilisation_pct
        link = links.EXPOSURE
        if util.value is None or value.value is None or limit.value is None:
            rows.append(
                _absent(
                    "exposure",
                    key,
                    row.label,
                    _missing_reason(value)
                    or _missing_reason(util)
                    or "No utilisation was computed for this bucket.",
                    value.provenance,
                    value=value,
                    limit=limit,
                    kind=kind,
                    breached=row.breached,
                    deep_link=link,
                )
            )
            continue
        rows.append(
            _measured(
                "exposure",
                key,
                row.label,
                value=value,
                limit=limit,
                utilisation=util.value,
                headroom=limit.value - value.value,
                breached=row.breached,
                formula="used = exposure ÷ its limit; headroom = limit - exposure "
                "(breached above the limit)",
                kind=kind,
                deep_link=link,
                utilisation_estimated=util.estimated,
            )
        )
    return rows


def _unreported_rows(p: Provenance) -> list[LimitRowView]:
    return [
        _absent(
            "account",
            "none:account_risk",
            "Open risk against the account limit",
            "Not reported: the API exposes no open-risk (risk at stop) figure and no "
            "account-risk limit.",
            p,
        ),
        _absent(
            "correlation",
            "none:correlated_exposure",
            "Correlated exposure",
            "Not reported: the gateway checks correlated exposure per order, but no "
            "correlated-exposure figure or limit is exposed by the API.",
            p,
        ),
        _absent(
            "data_quality",
            "none:data_quality",
            "Data-quality tolerances",
            "Not reported: no data-quality limit utilisation is exposed by the API.",
            p,
            see_also=links.DATA_QUALITY,
        ),
    ]


def _urgency(row: LimitRowView) -> float:
    util = row.utilisation_pct.value
    if row.breached:
        return 1e9 + (util or 0.0)
    return float("-inf") if util is None else util


def order_rows(rows: list[LimitRowView]) -> list[LimitRowView]:
    """Most used first; absent rows last in their given order (stable)."""
    def key(row: LimitRowView) -> tuple[int, float]:
        u = _urgency(row)
        return (1, 0.0) if u == float("-inf") else (0, -u)

    return sorted(rows, key=key)


def build_limit_board(platform: Any, settings: Any) -> LimitBoardView:
    """The whole board, from the same readers the risk and exposure routes use."""
    from fiboki.api.routers import trading as trading_router

    p = settings.provenance_for_execution()
    risk = trading_router.risk_state(platform, settings)
    exposure = trading_router.exposure(platform, settings)
    all_rows = (
        _unreported_rows(p) + _risk_rows(risk.data) + _exposure_rows(list(exposure.items))
    )
    ordered = order_rows(all_rows)
    sources = {"risk": "/api/trading/risk", "exposure": "/api/trading/exposure"}

    families: list[tuple[int, LimitFamilyView, float]] = []
    for index, fid in enumerate(FAMILY_ORDER):
        members = [r for r in ordered if r.family == fid]
        if fid != "exposure" and not members:
            continue
        worst = members[0] if members and members[0].band != "absent" else None
        source = (
            sources["exposure"]
            if fid == "exposure"
            else sources["risk"]
            if fid in ("loss", "drawdown", "margin")
            else "none"
        )
        families.append(
            (
                index,
                LimitFamilyView(
                    id=fid,
                    title=FAMILY_TITLES[fid],
                    rows=[r.key for r in members],
                    worst_key=worst.key if worst else None,
                    source=source,
                ),
                _urgency(worst) if worst else float("-inf"),
            )
        )
    families.sort(key=lambda f: (1, 0.0, f[0]) if f[2] == float("-inf") else (0, -f[2], f[0]))
    measured = [r for r in ordered if r.band != "absent"]
    closest = measured[0].key if measured else None
    bands = BandsView(
        warn_at=Figure(value=DISPLAY_BANDS.warn_at_pct, provenance=p, unit="pct"),
        critical_at=Figure(value=DISPLAY_BANDS.critical_at_pct, provenance=p, unit="pct"),
        detail="Platform display bands: a bar is NEAR from the first and CRITICAL from "
        "the second percentage of its limit. They colour and rank; the gateway, not "
        "these bands, decides whether an order may go.",
    )
    return LimitBoardView(
        limits_version=risk.data.limits_version,
        rows=ordered,
        families=[f[1] for f in families],
        closest_key=closest,
        bands=bands,
        sources={"risk": risk.source, "exposure": exposure.source},
    )


#: How the attention queue weighs a band (routers/command.py takes these).
BAND_SEVERITY: dict[str, str] = {"breached": "critical", "critical": "error", "warn": "warning"}


def attention_rows(board: LimitBoardView) -> list[tuple[LimitRowView, str]]:
    """Rows the attention queue must list, with the severity it lists them at."""
    return [(row, BAND_SEVERITY[row.band]) for row in board.rows if row.band in BAND_SEVERITY]
