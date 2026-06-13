"""Typed, versioned, serialisable strategy definitions.

A StrategySpec is the durable representation of a strategy: it can be
stored, diffed, mutated by the evolution engine, and compiled into the
common Strategy interface. Identical specs always produce identical
content hashes and identical compiled behaviour (the Phase 20 gate).
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone

from pydantic import BaseModel, Field, field_validator


class RuleSpec(BaseModel):
    """One composable rule block: a registered primitive + its params."""

    primitive: str
    params: dict = Field(default_factory=dict)

    @field_validator("primitive")
    @classmethod
    def _known_primitive(cls, v: str) -> str:
        from fibokei.strategies.factory.primitives import PRIMITIVES

        if v not in PRIMITIVES:
            raise ValueError(
                f"Unknown rule primitive '{v}'. Known: {sorted(PRIMITIVES)}"
            )
        return v


class StopSpec(BaseModel):
    """Stop-loss model. ``atr_multiple``: stop = entry -/+ mult*ATR."""

    model: str = "atr_multiple"  # atr_multiple | fixed_pct | kijun
    multiple: float = 2.0        # ATR multiple, or pct for fixed_pct
    atr_period: int = 14

    @field_validator("model")
    @classmethod
    def _known_model(cls, v: str) -> str:
        if v not in ("atr_multiple", "fixed_pct", "kijun"):
            raise ValueError(f"Unknown stop model '{v}'")
        return v

    @field_validator("multiple")
    @classmethod
    def _positive(cls, v: float) -> float:
        if v <= 0:
            raise ValueError("Stop multiple must be > 0 (stops are mandatory)")
        return v


class TargetSpec(BaseModel):
    """Take-profit model. ``rr_multiple``: target = entry +/- rr*stop_dist."""

    model: str = "rr_multiple"  # rr_multiple | atr_multiple
    multiple: float = 2.0

    @field_validator("model")
    @classmethod
    def _known_target_model(cls, v: str) -> str:
        if v not in ("rr_multiple", "atr_multiple"):
            raise ValueError(f"Unknown target model '{v}'")
        return v

    @field_validator("multiple")
    @classmethod
    def _positive(cls, v: float) -> float:
        if v <= 0:
            raise ValueError("Target multiple must be > 0")
        return v


class TrailingSpec(BaseModel):
    """Optional trailing-stop model."""

    model: str = "none"  # none | atr | kijun
    multiple: float = 2.0

    @field_validator("model")
    @classmethod
    def _known_trailing_model(cls, v: str) -> str:
        if v not in ("none", "atr", "kijun"):
            raise ValueError(f"Unknown trailing model '{v}'")
        return v

    @field_validator("multiple")
    @classmethod
    def _positive_trailing(cls, v: float) -> float:
        if v <= 0:
            raise ValueError("Trailing multiple must be > 0")
        return v


class StrategySpec(BaseModel):
    """Complete, durable strategy definition."""

    # Identity & provenance
    spec_id: str
    name: str
    version: int = 1
    family: str = "factory"
    hypothesis: str = ""
    parent_spec_id: str | None = None
    # manual | parameter_mutation | structural_mutation | crossover | nl_studio
    generation_method: str = "manual"
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    # Scope
    instruments: list[str] = Field(default_factory=list)  # empty = any
    timeframes: list[str] = Field(default_factory=list)   # empty = any
    direction: str = "both"  # long | short | both

    # Rules (all evaluated on CLOSED candles only)
    entry_rules: list[RuleSpec] = Field(min_length=1)
    confirmation_rules: list[RuleSpec] = Field(default_factory=list)
    invalidation_rules: list[RuleSpec] = Field(default_factory=list)
    filters: list[RuleSpec] = Field(default_factory=list)

    # Exits
    stop: StopSpec = Field(default_factory=StopSpec)
    target: TargetSpec = Field(default_factory=TargetSpec)
    trailing: TrailingSpec = Field(default_factory=TrailingSpec)
    max_bars_in_trade: int = 50
    exit_on_opposite_signal: bool = True

    # Risk
    risk_pct: float = 1.0

    @field_validator("direction")
    @classmethod
    def _known_direction(cls, v: str) -> str:
        if v not in ("long", "short", "both"):
            raise ValueError("direction must be long|short|both")
        return v

    @field_validator("risk_pct")
    @classmethod
    def _sane_risk(cls, v: float) -> float:
        if not (0 < v <= 2.0):
            raise ValueError("risk_pct must be in (0, 2.0] — central risk caps apply")
        return v

    # ── Serialisation & identity ─────────────────────────────────

    def canonical_json(self) -> str:
        """Stable JSON: identity-relevant fields only, sorted keys."""
        payload = self.model_dump(mode="json", exclude={"created_at"})
        return json.dumps(payload, sort_keys=True, separators=(",", ":"))

    @property
    def content_hash(self) -> str:
        """Deterministic hash of the definition (lineage/dedup key)."""
        return hashlib.sha256(self.canonical_json().encode()).hexdigest()[:16]

    @classmethod
    def from_json(cls, raw: str) -> "StrategySpec":
        return cls.model_validate_json(raw)
