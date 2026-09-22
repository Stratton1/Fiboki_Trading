"""Broker-specific symbol and contract mapping.

Phase 1 of the multi-broker fan-out architecture. The IG mapping continues
to live on the ``Instrument`` model in ``core/instruments.py`` for backward
compatibility with the data-loader pathway and live chart provider — this
module simply re-exposes the existing ``get_ig_epic`` helper for symmetry.

Tradovate is futures-first and treated very differently from IG CFDs:

* No instrument is supported by default. The operator must explicitly add
  a contract mapping before any Fiboki symbol can route to Tradovate.
* No FX pair (EURUSD, GBPUSD, …) is mapped automatically. CME-style
  currency futures (6E, 6B, …) have wholly different lot semantics from IG
  forex CFDs and must be configured deliberately, not derived.
* Front-month resolution is intentionally a stub in Phase 1: the resolver
  returns a placeholder contract symbol with a clear ``TODO`` flag. Real
  Tradovate ``/contract/find`` integration arrives in a later phase.

Operator note: a missing mapping must surface as a clean
``UnsupportedSymbol`` rejection in the audit log, never as a silent skip.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field

from fibokei.core.instruments import (
    get_ig_epic as _get_ig_epic_inner,
)
from fibokei.core.instruments import (
    get_symbol_by_epic as _get_symbol_by_epic_inner,
)
from fibokei.execution.targets import UnsupportedSymbol

# ── IG passthrough ───────────────────────────────────────────────────────


def resolve_ig_symbol(fiboki_symbol: str) -> "str | UnsupportedSymbol":
    """Map a Fiboki symbol to the IG epic, or return UnsupportedSymbol."""
    try:
        return _get_ig_epic_inner(fiboki_symbol)
    except KeyError as e:
        return UnsupportedSymbol(
            code="UNSUPPORTED_INSTRUMENT_IG",
            detail=str(e),
        )


def fiboki_symbol_for_ig_epic(epic: str) -> "str | UnsupportedSymbol":
    """Reverse-map an IG epic to a Fiboki symbol."""
    try:
        return _get_symbol_by_epic_inner(epic)
    except KeyError as e:
        return UnsupportedSymbol(
            code="UNKNOWN_IG_EPIC",
            detail=str(e),
        )


# ── Tradovate contract resolution ────────────────────────────────────────


@dataclass(frozen=True)
class TradovateContract:
    """A resolved Tradovate contract for trading.

    ``contract_symbol`` is the Tradovate ticker as the API expects it —
    e.g. ``ESM6`` for the June-2026 E-mini S&P 500 future. ``product_code``
    is the root family (``ES``). ``value_per_point`` and ``tick_size`` are
    metadata required for sizing/fee accounting.

    In Phase 1 these come from a static config dictionary. In a later phase
    the live ``LiveContractResolver`` will populate them from Tradovate's
    ``/contract/find`` and ``/product/find`` endpoints.
    """

    contract_symbol: str
    product_code: str
    tick_size: float = 0.0
    value_per_point: float = 0.0
    notes: str = ""


@dataclass(frozen=True)
class _ContractMapping:
    """Static mapping entry for a Fiboki symbol → Tradovate product."""

    product_code: str
    notes: str = ""
    # Optional explicit contract symbol override (rare — mostly for tests).
    explicit_contract_symbol: str | None = None


# ─── Phase 1 default mapping: empty by design. ────────────────────────────
#
# The brief is explicit: "Do not guess contract mapping." Operators must
# opt in to a Tradovate mapping by either:
#   1. Setting ``FIBOKEI_TRADOVATE_FRONT_MONTH=ESM6`` and
#      ``FIBOKEI_TRADOVATE_SYMBOL_MAP="US500:ES,US100:NQ"`` env vars, or
#   2. Adding a row in Phase 2's ``execution_accounts.config_json``.
#
# Candidate mappings — DOCUMENTED ONLY, NOT ACTIVATED:
#   US500   → ES   (E-mini S&P 500)        / MES (Micro)
#   US100   → NQ   (E-mini Nasdaq-100)     / MNQ
#   US30    → YM   (E-mini Dow)            / MYM
#   XAUUSD  → GC   (Gold futures)          / MGC
#   WTIUSD  → CL   (Crude Oil)             / MCL
#   NATGAS  → NG   (Natural Gas)           / MNG
#
# FX pairs (EURUSD, GBPUSD, …) are deliberately NOT mapped. CME currency
# futures (6E, 6B, …) have very different lot economics from IG FX CFDs
# and must be configured explicitly with sized risk review.

_DEFAULT_TRADOVATE_MAP: dict[str, _ContractMapping] = {}


def _parse_env_symbol_map(raw: str) -> dict[str, _ContractMapping]:
    """Parse ``FIBOKEI_TRADOVATE_SYMBOL_MAP`` env value.

    Format: comma-separated ``FIBOKI_SYMBOL:PRODUCT_CODE`` pairs, e.g.
    ``"US500:ES,US100:NQ,XAUUSD:MGC"``. Whitespace is tolerated. Invalid
    entries are skipped with no warning — operators see the result via the
    ``/execution/tradovate-health`` endpoint.
    """
    mapping: dict[str, _ContractMapping] = {}
    if not raw:
        return mapping
    for pair in raw.split(","):
        token = pair.strip()
        if not token or ":" not in token:
            continue
        sym, code = token.split(":", 1)
        sym = sym.strip().upper()
        code = code.strip().upper()
        if not sym or not code:
            continue
        mapping[sym] = _ContractMapping(product_code=code, notes="from-env")
    return mapping


def _load_symbol_map() -> dict[str, _ContractMapping]:
    """Combine the static default map with any env-configured additions."""
    combined = dict(_DEFAULT_TRADOVATE_MAP)
    combined.update(_parse_env_symbol_map(os.environ.get("FIBOKEI_TRADOVATE_SYMBOL_MAP", "")))
    return combined


@dataclass
class TradovateContractResolver:
    """Resolves Fiboki symbols to Tradovate contracts.

    The Phase 1 implementation is a *stub*: it uses a static symbol→product
    mapping and an env-supplied front-month tag (e.g. ``M6`` = June 2026)
    to produce a contract symbol like ``ESM6``. It does **not** call the
    Tradovate API. A real ``LiveContractResolver`` will replace this in a
    later phase with proper expiry-aware lookups against
    ``/contract/find``.

    If the symbol is not in the map, the resolver returns
    :class:`UnsupportedSymbol` so the router can record a clean rejection.
    """

    front_month_suffix: str = ""
    symbol_map: dict[str, _ContractMapping] = field(default_factory=_load_symbol_map)

    def resolve(self, fiboki_symbol: str) -> "TradovateContract | UnsupportedSymbol":
        sym = (fiboki_symbol or "").upper()
        if not sym:
            return UnsupportedSymbol(
                code="EMPTY_SYMBOL",
                detail="No Fiboki symbol supplied to Tradovate resolver",
            )
        entry = self.symbol_map.get(sym)
        if entry is None:
            return UnsupportedSymbol(
                code="UNSUPPORTED_INSTRUMENT_TRADOVATE",
                detail=(
                    f"No Tradovate contract mapping for Fiboki symbol '{sym}'. "
                    "Add via FIBOKEI_TRADOVATE_SYMBOL_MAP or Phase-2 account config."
                ),
            )
        if entry.explicit_contract_symbol:
            contract_symbol = entry.explicit_contract_symbol
        else:
            suffix = self.front_month_suffix or os.environ.get(
                "FIBOKEI_TRADOVATE_FRONT_MONTH", ""
            )
            if not suffix:
                return UnsupportedSymbol(
                    code="MISSING_FRONT_MONTH",
                    detail=(
                        f"Tradovate symbol '{sym}' mapped to product "
                        f"'{entry.product_code}' but no front-month suffix configured. "
                        "Set FIBOKEI_TRADOVATE_FRONT_MONTH (e.g. 'M6')."
                    ),
                )
            contract_symbol = f"{entry.product_code}{suffix.upper()}"
        return TradovateContract(
            contract_symbol=contract_symbol,
            product_code=entry.product_code,
            notes=entry.notes,
        )

    def supported_symbols(self) -> list[str]:
        """List Fiboki symbols currently mapped to a Tradovate product."""
        return sorted(self.symbol_map.keys())


def get_default_tradovate_resolver() -> TradovateContractResolver:
    """Build the resolver used by the default Tradovate adapter."""
    return TradovateContractResolver(
        front_month_suffix=os.environ.get("FIBOKEI_TRADOVATE_FRONT_MONTH", "")
    )
