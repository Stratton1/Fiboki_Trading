"""WHEN a strategy may deal is part of its structural fingerprint.

The fingerprint elides numbers so that a rediscovery arrived at with a period of
21 instead of 14 is still recognised as a rediscovery. Session hours are the one
place that rule was actively harmful: elided, "these rules, London only" and
"these rules, New York only" produced the same token, shared a structure hash,
and research memory reported the second as a reparameterisation of the first —
so a campaign that deliberately varied the dealing window was told it had
already run that experiment.

This module pins the fix, and pins its two edges:

* a document that imposes **no** restriction emits no restriction tokens, so
  adding them did not move its hash (the migration property);
* the blackout **margins** stay elided, because how many minutes either side of
  an event you stand aside is a knob, not an idea.

It also records, explicitly, that the five stored seed documents DO change hash,
because every one of them restricts its dealing window. Any novelty verdict
keyed on their old structure hashes is stale.
"""
from __future__ import annotations

import json

import pytest

from fiboki.research.structure import (
    fingerprint,
    is_reparameterisation,
    structural_tokens,
    structure_hash,
)
from fiboki.strategy.dsl import StrategyDocument
from tests.discovery_fixtures import seed


def rewritten(document: StrategyDocument, **fields: object) -> StrategyDocument:
    raw = json.loads(document.to_json())
    raw.update(fields)
    return StrategyDocument.model_validate(raw)


def with_events(document: StrategyDocument, **fields: object) -> StrategyDocument:
    raw = json.loads(document.to_json())
    raw["events"] = {**(raw.get("events") or {}), **fields}
    return StrategyDocument.model_validate(raw)


LONDON = {"windows": [[7, 16]], "weekdays": [0, 1, 2, 3, 4],
          "block_bars_before_weekend": 0}
NEW_YORK = {"windows": [[13, 21]], "weekdays": [0, 1, 2, 3, 4],
            "block_bars_before_weekend": 0}


# =====================================================================
# The fix
# =====================================================================


def test_two_session_variants_are_structurally_distinguishable() -> None:
    """The headline case, and the one the discovery layer had to work around."""
    base = seed("donchian_breakout_atr")
    assert base.sessions is None
    london = rewritten(base, sessions=LONDON)
    new_york = rewritten(base, sessions=NEW_YORK)

    assert structure_hash(london) != structure_hash(new_york)
    assert structure_hash(london) != structure_hash(base)
    assert structure_hash(new_york) != structure_hash(base)
    assert not is_reparameterisation(london, new_york)
    assert not is_reparameterisation(base, london)


def test_the_same_session_restriction_is_still_a_reparameterisation() -> None:
    """The fix must not turn every numeric tweak into a new idea."""
    base = rewritten(seed("donchian_breakout_atr"), sessions=LONDON)
    raw = json.loads(base.to_json())
    first = next(iter(raw["parameters"]))
    raw["parameters"][first]["default"] = raw["parameters"][first]["default"] + 1
    tweaked = StrategyDocument.model_validate(raw)
    assert is_reparameterisation(base, tweaked)


def test_a_narrower_trading_week_is_visible() -> None:
    base = seed("donchian_breakout_atr")
    weekdays_all = rewritten(base, sessions=LONDON)
    no_friday = rewritten(
        base, sessions={**LONDON, "weekdays": [0, 1, 2, 3]}
    )
    assert structure_hash(weekdays_all) != structure_hash(no_friday)


def test_standing_aside_before_the_weekend_is_visible() -> None:
    base = seed("donchian_breakout_atr")
    plain = rewritten(base, sessions=LONDON)
    guarded = rewritten(base, sessions={**LONDON, "block_bars_before_weekend": 2})
    assert structure_hash(plain) != structure_hash(guarded)


def test_which_events_are_avoided_is_structure() -> None:
    base = seed("donchian_breakout_atr")
    assert base.events.blocked_event_tags
    dropped = with_events(base, blocked_event_tags=[])
    swapped = with_events(base, blocked_event_tags=["cpi"])
    assert structure_hash(dropped) != structure_hash(base)
    assert structure_hash(swapped) != structure_hash(base)
    assert structure_hash(swapped) != structure_hash(dropped)


def test_month_end_and_rollover_choices_are_structure() -> None:
    base = seed("donchian_breakout_atr")
    month_end = with_events(base, avoid_month_end=True)
    rollover_off = with_events(base, avoid_rollover_hour=False)
    assert structure_hash(month_end) != structure_hash(base)
    assert structure_hash(rollover_off) != structure_hash(base)


# =====================================================================
# The edges the fix must NOT cross
# =====================================================================


def test_a_blackout_margin_is_still_a_number() -> None:
    """How wide the blackout is, is tuning. Which events it covers, is not."""
    base = seed("donchian_breakout_atr")
    widened = with_events(base, block_minutes_before=90, block_minutes_after=90)
    assert structure_hash(widened) == structure_hash(base)
    assert is_reparameterisation(base, widened)


def test_turning_a_blackout_on_is_not_a_margin_change() -> None:
    base = seed("donchian_breakout_atr")
    off = with_events(base, block_minutes_before=0, block_minutes_after=0)
    assert structure_hash(off) != structure_hash(base)


def test_an_unrestricted_document_emits_no_restriction_tokens() -> None:
    """The migration property: adding the tokens moved no unrestricted hash."""
    base = seed("donchian_breakout_atr")
    bare = with_events(
        rewritten(base, sessions=None),
        block_minutes_before=0,
        block_minutes_after=0,
        blocked_event_tags=[],
        avoid_month_end=False,
        avoid_rollover_hour=True,
    )
    restriction_tokens = [
        t
        for t in structural_tokens(bare)
        if t.startswith(("context:session.", "context:event."))
    ]
    assert restriction_tokens == []


def test_an_empty_session_block_reads_as_no_restriction() -> None:
    """``SessionRestriction()`` with nothing set restricts nothing.

    Treating it as identical to ``sessions=None`` is deliberate: the two say the
    same thing about when the strategy may deal, and a fingerprint that
    distinguished them would be fingerprinting the encoding.
    """
    base = rewritten(seed("donchian_breakout_atr"), sessions=None)
    empty = rewritten(
        base,
        sessions={"windows": [], "weekdays": [0, 1, 2, 3, 4],
                  "block_bars_before_weekend": 0},
    )
    assert structure_hash(empty) == structure_hash(base)


# =====================================================================
# What this did to the stored seed documents
# =====================================================================

#: The structure hashes the five stored seed documents had BEFORE sessions and
#: events entered the fingerprint. Recorded so the invalidation is a fact in the
#: test suite rather than a claim in a commit message: every novelty verdict,
#: research-memory recall and ``structure_hash`` column keyed on one of these is
#: stale and must be recomputed. Nothing reads these values at runtime.
PRE_SESSION_TOKEN_HASHES = {
    "donchian_breakout_atr":
        "58d66cf11a22a395363f15774168c6d10ce9a6bfa389c30a9cee106ad77fac67",
    "fib_golden_pocket_pullback":
        "a45a66d46ce0c52caf6d814efab34e8a5fba742374019304ea914999e6b78681",
    "ichimoku_kumo_trend":
        "2b7999d9b333929face43376e731d79c01eb330319711a88dab129e300ea87ae",
    "macd_ema_trend_hybrid":
        "884221d8160c03fe6bf799b3a89eb98d94c7d024406eaee5f8acaf5dbfdd5079",
    "rsi_band_mean_reversion":
        "755048afe73674a9e3bd2c85c20bb9e95dd4248f0a9d8fa2ff49c9887aa849fa",
}

#: The hashes now. Pinned so a later, unintended change to the token vocabulary
#: fails loudly instead of quietly invalidating research memory a second time.
CURRENT_HASHES = {
    "donchian_breakout_atr":
        "be82b67714a24a79163c4cbcbc9c007b392c2b4ec16b943c71eac98806359c51",
    "fib_golden_pocket_pullback":
        "ce4d332743861ae4d84f9d579e4727c72907d6df92af894b267426621e2361d3",
    "ichimoku_kumo_trend":
        "cf6c0dbff00ebbb0d239e5673cebaace13a230dcd5b43809355fb7cee6c8e7f1",
    "macd_ema_trend_hybrid":
        "748d749a26d0a4783db630c6f00415c54df82089f2556ee6d85929559c59257e",
    "rsi_band_mean_reversion":
        "2e000d030a63f903bb81f7baeb7097fd13789c72c60adf58bff7c8afc1cc8576",
}


def test_every_seed_document_restricts_its_dealing_window() -> None:
    """Which is why all five of them changed hash. This is not a surprise.

    Scoped to the five documents whose hashes are recorded above: a seed added
    later (tsmom_dual_horizon, 2026-09-29) is not part of that invalidation
    note and is free to trade without a dealing restriction."""
    for document in (seed(sid) for sid in sorted(PRE_SESSION_TOKEN_HASHES)):
        restricted = document.sessions is not None or bool(
            document.events.blocked_event_tags
            or document.events.block_minutes_before
            or document.events.block_minutes_after
            or document.events.avoid_month_end
            or document.events.avoid_rollover_hour is False
        )
        assert restricted, (
            f"{document.strategy_id} imposes no dealing restriction, so its "
            "structure hash should not have moved; the invalidation note is wrong"
        )


@pytest.mark.parametrize("strategy_id", sorted(PRE_SESSION_TOKEN_HASHES))
def test_the_seed_hashes_moved_and_both_values_are_recorded(
    strategy_id: str,
) -> None:
    current = structure_hash(seed(strategy_id))
    assert current == CURRENT_HASHES[strategy_id], (
        "a seed document's structure hash moved again. Research memory is keyed "
        "on this value, so moving it invalidates every stored novelty verdict "
        "for this strategy; if the change is intended, re-stamp both tables and "
        "say so."
    )
    assert current != PRE_SESSION_TOKEN_HASHES[strategy_id], (
        "the recorded pre-change hash matches the current one; either the "
        "fingerprint change was reverted or this record is stale"
    )


def test_a_fingerprint_still_carries_its_tokens_and_keywords() -> None:
    fp = fingerprint(seed("ichimoku_kumo_trend"))
    assert fp.structure_hash and fp.content_hash
    assert any(t.startswith("context:session.window:") for t in fp.tokens)
    assert fp.namespace("context")
