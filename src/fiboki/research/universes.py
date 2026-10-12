"""Named research universes: a universe is selected by NAME, never assembled ad hoc.

Why names
---------
A campaign's universe is part of its trial count and of its evidence: the same
document on sixteen FX/CFD series and on six crypto books is two experiments,
deflated separately. Naming the set (and freezing it here, versioned by suffix)
makes "which instruments?" a one-word answer that a manifest can record and a
reviewer can diff, instead of a command-line list that drifts between runs.

The universes
-------------
* ``k5_e4_v1``: the sixteen OANDA MID series K5 ran on and the E-4 pooled
  pre-registration (``research/preregistration/pooled_universe_e4.json``) fixed.
  Recorded here for reference only. E-4 reads its OWN filed list, and nothing in
  this module may change it; a test pins the two equal.
* ``crypto_core_v1``: BTCGBP and ETHGBP, the two deepest Coinbase GBP books and
  the paper-forward crypto wiring's universe.
* ``crypto_majors_v1``: the Coinbase GBP products whose mean daily GBP turnover
  over the 30 days to 2026-10-10 was at least GBP 100,000
  (``core/instruments_crypto.CRYPTO_GBP_TURNOVER_30D``): BTC, ETH, SOL, LINK,
  ADA, DOT. LTC (GBP 97k) and DOGE (GBP 80k) are registered and can be
  backfilled but are outside it: on a book that thin, a retail order and the
  integrity checker both see gaps where nothing traded.

Crypto universes are SEPARATE by construction: :func:`get_universe` returns one
asset class's set, :func:`assert_single_venue` refuses a mixture, and no
crypto symbol appears in ``k5_e4_v1``. A crypto campaign is its own campaign,
with its own trial count, its own cost profile (``COINBASE_UK_SPOT``) and its
own holdout claims.
"""
from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass

from fiboki.core import instruments as instrument_registry
from fiboki.core.instruments_crypto import CRYPTO_GBP_TURNOVER_30D
from fiboki.strategy.dsl import StrategyDocument

__all__ = [
    "CRYPTO_MAJORS_MIN_TURNOVER_GBP",
    "UNIVERSES",
    "Universe",
    "UnknownUniverse",
    "assert_single_venue",
    "get_universe",
    "retarget",
    "universe_names",
]

#: The liquidity floor ``crypto_majors_v1`` was drawn at (mean daily GBP turnover).
CRYPTO_MAJORS_MIN_TURNOVER_GBP = 100_000


class UnknownUniverse(KeyError):
    pass


@dataclass(frozen=True, slots=True)
class Universe:
    """A frozen, named instrument set with the facts a campaign must record."""

    name: str
    #: Short tag for derived strategy ids (``<id>__<tag>``); the DSL caps ids at 64.
    tag: str
    symbols: tuple[str, ...]
    venue: str
    #: The cost profile research on this universe uses by default.
    profile: str
    #: The stress profile a survivor must also clear.
    stress_profile: str
    #: Long only because a member cannot be shorted (``Instrument.shortable``).
    long_only: bool
    description: str

    def __post_init__(self) -> None:
        if not self.tag.isidentifier() or len(self.tag) > 8:
            raise ValueError(f"universe {self.name}: tag {self.tag!r} must be a short identifier")
        if not self.symbols or len(set(self.symbols)) != len(self.symbols):
            raise ValueError(f"universe {self.name}: symbols must be non-empty and unique")
        missing = [s for s in self.symbols if not instrument_registry.exists(s)]
        if missing:
            raise ValueError(f"universe {self.name}: unregistered symbols {missing}")
        assert_single_venue(self.symbols)

    def to_dict(self) -> dict[str, object]:
        return {
            "name": self.name, "tag": self.tag, "symbols": list(self.symbols), "venue": self.venue,
            "profile": self.profile, "stress_profile": self.stress_profile,
            "long_only": self.long_only, "description": self.description,
        }


def assert_single_venue(symbols: Iterable[str]) -> str:
    """Refuse a set that mixes spot crypto with CFDs. Returns the venue."""
    venues = {instrument_registry.get(s).venue for s in symbols}
    if len(venues) != 1:
        raise ValueError(
            f"a research universe is one venue; {sorted(symbols)} mixes {sorted(venues)}. "
            "Crypto campaigns are separate campaigns (fiboki.research.universes)."
        )
    return venues.pop()


def _majors() -> tuple[str, ...]:
    ranked = sorted(CRYPTO_GBP_TURNOVER_30D.items(), key=lambda kv: (-kv[1], kv[0]))
    return tuple(sym for sym, gbp in ranked if gbp >= CRYPTO_MAJORS_MIN_TURNOVER_GBP)


UNIVERSES: dict[str, Universe] = {
    u.name: u
    for u in (
        Universe(
            name="k5_e4_v1",
            tag="k5e4",
            symbols=(
                "AUDJPY", "AUDUSD", "DE40", "EURGBP", "EURJPY", "EURUSD", "GBPJPY", "GBPUSD",
                "NZDUSD", "UK100", "US500", "USDCAD", "USDCHF", "USDJPY", "XAGUSD", "XAUUSD",
            ),
            venue="oanda",
            profile="OANDA_REALISTIC",
            stress_profile="SEVERE_STRESS",
            long_only=False,
            description=(
                "K5's sixteen OANDA practice MID series, as filed by E-4 (reference only; "
                "E-4 reads its own preregistration)."
            ),
        ),
        Universe(
            name="crypto_core_v1",
            tag="ccore1",
            symbols=("BTCGBP", "ETHGBP"),
            venue="coinbase",
            profile="COINBASE_UK_SPOT",
            stress_profile="COINBASE_UK_SPOT_STRESS",
            long_only=True,
            description="BTC and ETH against GBP on Coinbase UK spot; the paper crypto universe.",
        ),
        Universe(
            name="crypto_majors_v1",
            tag="cmaj1",
            symbols=_majors(),
            venue="coinbase",
            profile="COINBASE_UK_SPOT",
            stress_profile="COINBASE_UK_SPOT_STRESS",
            long_only=True,
            description=(
                "Coinbase UK GBP spot books with mean daily turnover >= GBP 100,000 over the "
                "30 days to 2026-10-10. Histories start 2015 (BTC), 2018 (ETH), 2020 (LINK) "
                "and 2021 (SOL, ADA, DOT): a pooled window starts at the LATEST of them."
            ),
        ),
    )
}


def universe_names() -> list[str]:
    return sorted(UNIVERSES)


def get_universe(name: str) -> Universe:
    key = str(name).strip().lower()
    if key not in UNIVERSES:
        raise UnknownUniverse(f"unknown universe {name!r}; known: {universe_names()}")
    return UNIVERSES[key]


#: Longest suffix a retargeted id may carry; the DSL's slug is 3..64 characters.
_ID_LIMIT = 64


def retarget(document: StrategyDocument, universe: Universe | str) -> StrategyDocument:
    """``document`` with its ``universe`` replaced by a named universe's symbols.

    The rules, stops, exits and parameters are untouched: the result is the SAME
    BET asked of a different market, which is what "run the seeds on crypto"
    means. It is nonetheless a DIFFERENT candidate: ``universe`` is semantic, so
    its content hash differs and the holdout registry, research memory and the
    trial count treat it as its own. Its id is ``<id>__<universe tag>`` (e.g.
    ``donchian_breakout_atr__ccore1``) and it names its parent in
    ``parent_strategy_ids``.

    Direction is NOT changed. On a long-only universe a document's short rules
    still fire and are refused by the engine and the risk gateway
    (``short_not_permitted``), which is where long-only belongs; a document
    silently edited to long-only would be a different document wearing the
    parent's name.
    """
    u = get_universe(universe) if isinstance(universe, str) else universe
    new_id = f"{document.strategy_id}__{u.tag}"
    if len(new_id) > _ID_LIMIT:
        raise ValueError(f"retargeted id {new_id!r} exceeds {_ID_LIMIT} characters")
    body = document.model_dump(mode="json")
    body["strategy_id"] = new_id
    body["universe"] = list(u.symbols)
    parents = list(body.get("parent_strategy_ids") or [])
    if document.strategy_id not in parents:
        parents.append(document.strategy_id)
    body["parent_strategy_ids"] = parents
    note = (
        f"Retargeted from {document.strategy_id} (content {document.short_hash}) onto the "
        f"named universe {u.name} by fiboki.research.universes.retarget; rules unchanged."
        + (" Long only: short entries are refused by the engine and the risk gateway."
           if u.long_only else "")
    )
    body["notes"] = (str(body.get("notes") or "") + ("\n\n" if body.get("notes") else "") + note)
    return StrategyDocument.model_validate(body)
