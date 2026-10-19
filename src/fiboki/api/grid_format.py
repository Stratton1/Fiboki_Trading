"""Conditional formatting for data grids, decided by the API.

A rule names a grid, a column, a comparison and a TONE. The API evaluates the
rules against the values it is about to send and attaches the resulting tones
to each row (``tones: {column: tone}``); the workstation maps a tone to a CSS
class and decides nothing. ``GET /api/workspace/format-rules/{grid}`` lists the
rules so a grid can show what each colour means.

Tones are deliberately not P&L colours: ``rise``/``fall`` use the diverging
palette (green and red mean P&L on this terminal, and a price change is not
P&L), ``stale`` and ``warn`` the state colours, ``muted`` dims.

Thresholds here are DISPLAY thresholds (when to dim a row, when to flag a wide
spread), not trading thresholds; nothing in a decision path reads them.
"""
from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict

__all__ = ["FORMAT_RULES", "FormatRule", "Tone", "tones_for"]

Tone = Literal["rise", "fall", "warn", "stale", "muted", "accent"]
Comparison = Literal["gt", "lt", "ge", "le", "eq", "ne"]


class FormatRule(BaseModel):
    model_config = ConfigDict(frozen=True)

    grid: str
    column: str
    #: The value compared is ``row[source]`` (defaults to the column itself).
    source: str = ""
    when: Comparison
    value: float | str | bool
    tone: Tone
    description: str


def _r(grid: str, column: str, when: Comparison, value: float | str | bool, tone: Tone,
       description: str, source: str = "") -> FormatRule:
    return FormatRule(grid=grid, column=column, source=source, when=when, value=value, tone=tone,
                      description=description)


_QUOTE_RULES = (
    _r("watchlist", "change", "gt", 0.0, "rise", "Mid above the window's first mid.", "change_pct"),
    _r("watchlist", "change", "lt", 0.0, "fall", "Mid below the window's first mid.", "change_pct"),
    _r("watchlist", "age", "eq", "stale", "stale", "Session open but no recorded quote recently.", "data_state"),
    _r("watchlist", "age", "eq", "closed", "muted", "The instrument's session is closed.", "data_state"),
    _r("watchlist", "age", "eq", "none", "muted", "The recorder has no quote in the window.", "data_state"),
    _r("watchlist", "spread", "ge", 2.0, "warn", "Spread at least twice the instrument's typical spread.",
       "spread_ratio"),
)

FORMAT_RULES: dict[str, tuple[FormatRule, ...]] = {
    "watchlist": _QUOTE_RULES,
    "pulse": tuple(r.model_copy(update={"grid": "pulse"}) for r in _QUOTE_RULES),
    "notifications": (
        _r("notifications", "state", "eq", "unread", "accent", "Not yet marked read.", "read_state"),
    ),
}


def _match(when: Comparison, left: Any, right: float | str | bool) -> bool:
    if left is None:
        return False
    if when == "eq":
        return bool(left == right)
    if when == "ne":
        return bool(left != right)
    if isinstance(left, bool) or not isinstance(left, int | float) or isinstance(right, str | bool):
        return False
    return {
        "gt": left > right,
        "lt": left < right,
        "ge": left >= right,
        "le": left <= right,
    }[when]


def tones_for(grid: str, values: Mapping[str, Any]) -> dict[str, Tone]:
    """The tone per column for one row; the first matching rule per column wins."""
    out: dict[str, Tone] = {}
    for rule in FORMAT_RULES.get(grid, ()):
        if rule.column in out:
            continue
        if _match(rule.when, values.get(rule.source or rule.column), rule.value):
            out[rule.column] = rule.tone
    return out
