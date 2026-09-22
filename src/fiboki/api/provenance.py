"""The labelled-number contract.

Every numeric field the API emits is a :class:`Figure`. A ``Figure`` cannot be
constructed without a :class:`~fiboki.core.enums.Provenance`, so "which run
produced this?" is answerable at the pixel rather than at the page heading.

This is the direct fix for the V1 failure where ``/trades`` was titled
"Paper / Backtest" and contained no paper trades at all: with a per-row label
there is no need for the title to make a claim, and no way for it to lie.

Realism caveats are computed here too, from the assumptions actually in force
(:class:`fiboki.api.settings.Settings`), and travel attached to the figure they
qualify. V1 printed "Estimated realistic return: 190-230%" as static page copy
beside a live computed number; nothing recomputed it and nothing invalidated it.
"""
from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel, ConfigDict, Field

from fiboki.core.enums import ExecutionMode, Provenance

if TYPE_CHECKING:  # pragma: no cover
    from fiboki.api.settings import Settings

__all__ = [
    "Caveat",
    "Figure",
    "Series",
    "SeriesPoint",
    "realism_caveats",
    "trust",
]


#: How far a provenance can be trusted, ordered. The UI sorts and colours on
#: this rather than re-deriving a hierarchy of its own.
_TRUST: dict[Provenance, int] = {
    Provenance.BACKTEST: 10,
    Provenance.WALKFORWARD: 30,
    Provenance.OUT_OF_SAMPLE: 50,
    Provenance.HOLDOUT: 60,
    Provenance.SHADOW: 70,
    Provenance.PAPER: 75,
    Provenance.BROKER_DEMO: 85,
    Provenance.BROKER_LIVE: 100,
}


def trust(provenance: Provenance) -> int:
    """0-100 confidence weight implied by where a number came from."""
    return _TRUST[provenance]


class Caveat(BaseModel):
    """A server-computed qualifier. Never written into a page."""

    model_config = ConfigDict(frozen=True)

    code: str
    """Stable machine key, e.g. ``slippage_not_modelled``."""
    severity: str = Field(default="info", pattern="^(info|warning|critical)$")
    message: str
    """Human sentence. Generated from configuration, so it cannot go stale."""
    affects: str = ""
    """What the caveat distorts, e.g. ``net_pnl`` or ``sharpe``."""
    direction: str = Field(default="unknown", pattern="^(optimistic|pessimistic|unknown)$")
    """Which way the figure is likely to be wrong. Operators need the sign."""


class Figure(BaseModel):
    """One number, with the label that makes it interpretable.

    ``value`` may be ``None``. A missing number is a distinct, renderable state
    and must never be coerced to zero — ``data?.x ?? 0`` in the V1 frontend is
    exactly how a total backend outage rendered as a flat, healthy, idle fleet.
    """

    model_config = ConfigDict(frozen=True)

    value: float | None
    provenance: Provenance
    unit: str = ""
    """``GBP``, ``pct``, ``ratio``, ``count``, ``bps``, ``ms``, ``""``."""
    as_of: datetime | None = None
    sample_size: int | None = None
    """Trades/observations behind the figure. A Sharpe over 9 trades is noise."""
    caveats: tuple[Caveat, ...] = ()
    estimated: bool = False
    """True when the value is modelled rather than observed at a venue."""

    @property
    def trust(self) -> int:
        return trust(self.provenance)

    @classmethod
    def missing(cls, provenance: Provenance, *, unit: str = "", reason: str = "") -> Figure:
        """An explicitly absent value. Renders as 'no data', never as 0."""
        caveats = (
            (
                Caveat(
                    code="value_unavailable",
                    severity="warning",
                    message=reason or "No value was available for this figure.",
                    direction="unknown",
                ),
            )
            if reason
            else ()
        )
        return cls(value=None, provenance=provenance, unit=unit, caveats=caveats)


class SeriesPoint(BaseModel):
    model_config = ConfigDict(frozen=True)

    t: datetime
    v: float | None


class Series(BaseModel):
    """A labelled time series. The label belongs to the series, not the chart."""

    model_config = ConfigDict(frozen=True)

    name: str
    provenance: Provenance
    unit: str = ""
    points: tuple[SeriesPoint, ...] = ()
    caveats: tuple[Caveat, ...] = ()


# --------------------------------------------------------------- caveats


def _pnl_caveats(settings: Settings) -> list[Caveat]:
    out: list[Caveat] = []
    if settings.slippage_model == "zero":
        out.append(
            Caveat(
                code="slippage_not_modelled",
                severity="warning",
                message=(
                    "Slippage model is 'zero': every fill is assumed to happen at "
                    "the requested price. Realised P&L will be worse than shown."
                ),
                affects="net_pnl",
                direction="optimistic",
            )
        )
    if settings.spread_model == "static_typical":
        out.append(
            Caveat(
                code="static_spread",
                severity="warning",
                message=(
                    "Spreads are the instrument's typical value, held constant. "
                    "Real spreads widen at the news and session edges where many "
                    "signals fire."
                ),
                affects="net_pnl",
                direction="optimistic",
            )
        )
    if settings.financing_model == "none":
        out.append(
            Caveat(
                code="financing_not_modelled",
                severity="warning",
                message=(
                    "Overnight financing is not charged. Positions held across "
                    "rollover cost more than shown."
                ),
                affects="net_pnl",
                direction="optimistic",
            )
        )
    if settings.fx_conversion_model == "static_rate":
        out.append(
            Caveat(
                code="static_fx_conversion",
                severity="info",
                message=(
                    "Non-GBP P&L is converted at a static rate, not the rate at "
                    "the time of each fill."
                ),
                affects="net_pnl",
                direction="unknown",
            )
        )
    return out


def realism_caveats(
    settings: Settings,
    provenance: Provenance,
    *,
    affects: str = "net_pnl",
    sample_size: int | None = None,
    min_sample: int = 80,
) -> tuple[Caveat, ...]:
    """Compute the qualifiers that actually apply to one figure.

    Driven by the configuration in force and by the figure's own provenance and
    sample size, so the text changes when the platform changes. Nothing here is
    a fixed string chosen by a page author.
    """
    out: list[Caveat] = []

    if provenance in (
        Provenance.BACKTEST,
        Provenance.WALKFORWARD,
        Provenance.OUT_OF_SAMPLE,
        Provenance.HOLDOUT,
    ):
        out.extend(_pnl_caveats(settings))
        out.append(
            Caveat(
                code="simulated_execution",
                severity="warning",
                message=(
                    f"Produced by simulation ({provenance.value}); no order reached "
                    "a venue, so queue position and rejection are not represented."
                ),
                affects=affects,
                direction="optimistic",
            )
        )
    elif provenance in (Provenance.PAPER, Provenance.SHADOW):
        out.append(
            Caveat(
                code="paper_fill_assumption",
                severity="warning",
                message=(
                    "Paper fills are simulated against recorded executable prices. "
                    "Partial fills and venue rejections are not represented."
                ),
                affects=affects,
                direction="optimistic",
            )
        )
        out.extend(c for c in _pnl_caveats(settings) if c.code != "simulated_execution")
    elif provenance is Provenance.BROKER_DEMO:
        out.append(
            Caveat(
                code="demo_liquidity",
                severity="info",
                message=(
                    "Demo venue fills are not backed by real liquidity; demo books "
                    "fill more generously than production ones."
                ),
                affects=affects,
                direction="optimistic",
            )
        )

    if sample_size is not None and sample_size < min_sample:
        out.append(
            Caveat(
                code="sample_below_ranking_minimum",
                severity="warning",
                message=(
                    f"{sample_size} trades is below the {min_sample}-trade minimum "
                    "required for primary ranking; treat this figure as indicative."
                ),
                affects=affects,
                direction="unknown",
            )
        )
    return tuple(out)


def figure(
    value: float | None,
    provenance: Provenance,
    *,
    settings: Settings | None = None,
    unit: str = "",
    as_of: datetime | None = None,
    sample_size: int | None = None,
    affects: str = "",
    estimated: bool = False,
    extra_caveats: Sequence[Caveat] = (),
) -> Figure:
    """Build a :class:`Figure`, attaching the caveats that genuinely apply."""
    caveats: tuple[Caveat, ...] = ()
    if settings is not None and affects:
        caveats = realism_caveats(
            settings, provenance, affects=affects, sample_size=sample_size
        )
    if extra_caveats:
        caveats = caveats + tuple(extra_caveats)
    return Figure(
        value=value,
        provenance=provenance,
        unit=unit,
        as_of=as_of,
        sample_size=sample_size,
        caveats=caveats,
        estimated=estimated,
    )


def provenance_for_mode(mode: ExecutionMode) -> Provenance:
    from fiboki.api.settings import MODE_PROVENANCE

    return MODE_PROVENANCE[mode]


def as_json(value: Any) -> Any:  # pragma: no cover - convenience
    if isinstance(value, BaseModel):
        return value.model_dump(mode="json")
    return value
