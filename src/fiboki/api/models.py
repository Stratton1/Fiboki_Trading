"""Response models shared by more than one router.

Two conventions hold everywhere:

* Every numeric field is a :class:`~fiboki.api.provenance.Figure`. If you find
  a bare ``float`` in a response model, that is a bug — ``tests/api/
  test_provenance_contract.py`` walks every registered response model and fails
  on one.
* Every list response carries ``as_of`` and ``source``, so a stale page can say
  so rather than silently showing yesterday.
"""
from __future__ import annotations

from datetime import datetime
from typing import Generic, TypeVar

from pydantic import BaseModel, ConfigDict, Field

from fiboki.api.provenance import Caveat, Figure, Series
from fiboki.core.enums import ExecutionMode, Provenance

__all__ = [
    "Envelope",
    "ExecutionModeBanner",
    "KeyFigure",
    "Page",
    "SourceNote",
]

T = TypeVar("T")


class SourceNote(BaseModel):
    """Where a payload came from, always rendered, never inferred from the URL."""

    model_config = ConfigDict(frozen=True)

    kind: str = Field(pattern="^(live|seed|absent|mixed)$")
    detail: str
    as_of: datetime | None = None


class Envelope(BaseModel, Generic[T]):
    """One object plus its provenance context."""

    data: T
    source: SourceNote
    caveats: tuple[Caveat, ...] = ()


class Page(BaseModel, Generic[T]):
    """A list plus its provenance context and honest totals."""

    items: list[T]
    total: int
    offset: int = 0
    limit: int = 100
    source: SourceNote
    caveats: tuple[Caveat, ...] = ()


class KeyFigure(BaseModel):
    """A headline number for a tile. Label and figure travel together."""

    model_config = ConfigDict(frozen=True)

    key: str
    label: str
    figure: Figure
    help_text: str = ""


class ExecutionModeBanner(BaseModel):
    """The payload behind the sticky banner.

    Carries everything the banner needs to be correct without the page making a
    single assumption. V1 hardcoded "Paper trading only — no live execution"
    into a ``confirm()`` string, so the sentence stayed the same in every mode.
    """

    model_config = ConfigDict(frozen=True)

    mode: ExecutionMode
    provenance: Provenance
    touches_broker: bool
    touches_real_money: bool
    severity: str = Field(pattern="^(info|caution|danger)$")
    headline: str
    detail: str
    live_execution_compiled_in: bool
    kill_switch_active: bool
    kill_switch_mode: str | None = None
    as_of: datetime


class ChartSeries(BaseModel):
    """Chart payloads are data, not rendering instructions."""

    model_config = ConfigDict(frozen=True)

    title: str
    x_label: str = ""
    y_label: str = ""
    series: tuple[Series, ...] = ()
    caveats: tuple[Caveat, ...] = ()
