"""Structural fingerprints: what a strategy IS, with the numbers taken out.

``StrategyDocument.content_hash`` answers "is this the same strategy?". It is
exact, and it is the right key for a holdout registry. It is the wrong key for
research memory, because the commonest way to waste a week is to rediscover an
idea that was already rejected and arrive at it with a period of 21 instead of
14. Content-hashed, that is a brand new strategy. Structurally, it is the same
one.

So this module produces a second key: the same canonical view of a document with
every NUMBER elided. Two documents that AND the same conditions over the same
indicators, with the same exit shapes, on the same family and direction, share a
``structure_hash`` however their thresholds and periods differ.

What is elided and what is kept
-------------------------------
Elided: every int and float -- indicator periods, comparison thresholds, ATR
multiples, allocations, lookbacks, blackout margins.

Kept: the rule vocabulary (which primitive, comparing what to what), indicator
NAMES, the exit model kinds, booleans (``invert`` flips a rule's meaning rather
than tuning it), the context -- family, direction, timeframes, universe -- and
WHEN the strategy may deal.

Session hours are the one deliberate exception to the elide-every-number rule,
and it is worth spelling out why. A session window is not a threshold somebody
tuned; it is a claim about which market's participants the strategy is trading
against. Elided to ``#``, "the same rules, London only" and "the same rules, New
York only" produce the same token, share a structure hash, and research memory
reports the second as a reparameterisation of the first -- so a campaign that
deliberately varied the dealing window would be told it had already run that
experiment and skip it. The windows, the weekday set and the blocked event tags
are therefore carried as values. The blackout *margins* (how many minutes either
side of an event) are genuine knobs and stay elided, which is why the discovery
layer's own session/event guard in ``discovery/novelty.py`` is still worth
keeping: it sees differences this fingerprint still deliberately cannot.

Absence contributes nothing. A document with no ``sessions`` block and a default
``events`` block emits no restriction tokens at all, so its structure hash is
exactly what it was before these tokens existed. Only a document that actually
restricts when it may deal gets a new hash -- and for those, prior novelty
verdicts keyed on the old hash no longer match.

Tokens are namespaced so similarity can be weighted. Two strategies that differ
only in universe are near-identical research; two that share a universe and
nothing else are not. A flat Jaccard cannot tell those apart, and on a
ten-instrument universe it would be dominated by the instrument tokens.
"""
from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

__all__ = [
    "NAMESPACE_WEIGHTS",
    "StructuralFingerprint",
    "fingerprint",
    "is_reparameterisation",
    "keywords",
    "structural_similarity",
    "structural_tokens",
    "structure_hash",
]

#: How much each namespace counts towards structural similarity. Rules dominate
#: because the rules ARE the strategy; context matters because the same rules on
#: a different asset class is a different piece of research.
NAMESPACE_WEIGHTS: dict[str, float] = {
    "rule": 0.60,
    "exit": 0.20,
    "context": 0.20,
}

_RULE_SECTIONS = (
    "regime",
    "setup",
    "entry",
    "confirmation",
    "filters",
    "invalidation",
)

_STOPWORDS = frozenset(
    """
    a an and are as at be but by for from has have if in into is it its of on or
    that the their then there these this to was were will with we you your do does
    add adding try trying test testing use using run running see check does did
    """.split()
)


def _shape(node: Any) -> str:
    """Canonical string for a JSON-ish value with numbers elided to ``#``.

    Lists are sorted by their own shape so that two documents that declare the
    same conditions in a different order produce the same token -- which is what
    "structurally the same" has to mean to a search process that generates rule
    orders arbitrarily.
    """
    if isinstance(node, bool):
        return "T" if node else "F"
    if isinstance(node, Mapping):
        return "{" + ",".join(f"{k}:{_shape(node[k])}" for k in sorted(node, key=str)) + "}"
    if isinstance(node, str):
        return json.dumps(node)
    if isinstance(node, int | float):
        return "#"
    if node is None:
        return "~"
    if isinstance(node, Sequence):
        return "[" + ",".join(sorted(_shape(v) for v in node)) + "]"
    return json.dumps(str(node))


def _as_mapping(document: Any) -> dict[str, Any]:
    if document is None:
        return {}
    if hasattr(document, "model_dump"):
        return dict(document.model_dump(mode="json"))
    if isinstance(document, Mapping):
        return dict(document)
    raise TypeError(
        f"cannot fingerprint a {type(document).__name__}; supply a StrategyDocument "
        "or a plain mapping of one"
    )


def _rule_tokens(data: Mapping[str, Any]) -> list[str]:
    out: list[str] = []
    for section in _RULE_SECTIONS:
        node = data.get(section)
        if not node:
            continue
        if isinstance(node, Mapping):  # RuleSet: {"long": [...], "short": [...]}
            for side in sorted(node):
                for rule in node.get(side) or ():
                    out.append(f"rule:{section}.{side}:{_shape(rule)}")
        else:  # a bare tuple of rules
            for rule in node:
                out.append(f"rule:{section}:{_shape(rule)}")
    return out


def _exit_tokens(data: Mapping[str, Any]) -> list[str]:
    out: list[str] = []
    stop = data.get("stop")
    if isinstance(stop, Mapping):
        out.append(f"exit:stop:{stop.get('kind', '?')}")
        for attr in ("atr", "level"):
            operand = stop.get(attr)
            if operand:
                out.append(f"exit:stop.{attr}:{_shape(operand)}")
    legs = data.get("take_profits") or ()
    for i, leg in enumerate(legs):
        if isinstance(leg, Mapping):
            out.append(f"exit:tp{i}:{leg.get('kind', '?')}")
    trailing = data.get("trailing")
    if isinstance(trailing, Mapping):
        out.append(f"exit:trailing:{trailing.get('kind', 'none')}")
    pm = data.get("position_management")
    if isinstance(pm, Mapping):
        out.append(f"exit:pyramiding:{bool(pm.get('allow_pyramiding'))}")
        out.append(f"exit:reversal:{bool(pm.get('allow_reversal_on_opposite_signal'))}")
        if pm.get("max_bars_in_trade") is not None:
            out.append("exit:time_stop:present")
    return out


def _context_tokens(data: Mapping[str, Any]) -> list[str]:
    out: list[str] = []
    if data.get("family"):
        out.append(f"context:family:{data['family']}")
    if data.get("direction"):
        out.append(f"context:direction:{data['direction']}")
    for tf in sorted(data.get("timeframes") or ()):
        out.append(f"context:timeframe:{tf}")
    for inst in sorted(data.get("universe") or ()):
        out.append(f"context:instrument:{inst}")
    for name in sorted(data.get("parameters") or {}):
        out.append(f"context:tunable:{name}")
    out.extend(_when_tokens(data))
    return out


def _when_tokens(data: Mapping[str, Any]) -> list[str]:
    """Tokens for WHEN the strategy may deal: sessions and event blackouts.

    Nothing is emitted for a document that imposes no restriction, so adding
    these tokens leaves an unrestricted document's structure hash untouched.
    See the module docstring for why the hours themselves are not elided.
    """
    out: list[str] = []
    sessions = data.get("sessions")
    if isinstance(sessions, Mapping):
        for window in sessions.get("windows") or ():
            try:
                start, end = window
            except (TypeError, ValueError):  # pragma: no cover - malformed input
                out.append(f"context:session.window:{_shape(window)}")
                continue
            out.append(f"context:session.window:{int(start)}-{int(end)}")
        weekdays = sessions.get("weekdays")
        if weekdays is not None:
            days = ",".join(str(int(d)) for d in sorted(weekdays))
            # The default Monday-to-Friday set is not a restriction anyone
            # chose; only a narrower or wider week is worth a token.
            if days != "0,1,2,3,4":
                out.append(f"context:session.weekdays:{days}")
        if sessions.get("block_bars_before_weekend"):
            # HOW MANY bars is a knob; that the rule exists at all is not.
            out.append("context:session.weekend_block:present")

    events = data.get("events")
    if isinstance(events, Mapping):
        if events.get("block_minutes_before") or events.get("block_minutes_after"):
            out.append("context:event.blackout:present")
        for tag in sorted(events.get("blocked_event_tags") or ()):
            out.append(f"context:event.tag:{tag}")
        if events.get("avoid_month_end"):
            out.append("context:event.month_end:present")
        if events.get("avoid_rollover_hour") is False:
            # The default is to avoid it, so only switching it OFF is a choice.
            out.append("context:event.rollover:allowed")
    return out


def structural_tokens(document: Any) -> tuple[str, ...]:
    """Namespaced, number-free tokens describing a strategy's structure."""
    data = _as_mapping(document)
    tokens = _rule_tokens(data) + _exit_tokens(data) + _context_tokens(data)
    return tuple(sorted(set(tokens)))


def structure_hash(document: Any) -> str:
    """SHA-256 over the structural tokens.

    Equal hashes mean: same rules over the same indicators, same exit shapes,
    same family, direction, timeframes and universe -- differing only in numbers.
    """
    tokens = structural_tokens(document)
    if not tokens:
        return ""
    return hashlib.sha256("\n".join(tokens).encode("utf-8")).hexdigest()


def keywords(document: Any) -> tuple[str, ...]:
    """Plain words a human would use for this strategy, for text recall.

    Indicator names, family, direction and section names -- enough that "add RSI
    confirmation to an N-wave strategy" finds the experiments that did exactly
    that, without anyone having had to tag them by hand.
    """
    data = _as_mapping(document)
    found: set[str] = set()
    if data.get("family"):
        found.update(str(data["family"]).lower().replace("-", "_").split("_"))
    if data.get("direction"):
        found.add(str(data["direction"]).lower())
    for name in (data.get("parameters") or {}):
        found.update(str(name).lower().split("_"))
    if data.get("strategy_id"):
        found.update(str(data["strategy_id"]).lower().split("_"))
    if data.get("name"):
        found.update(w.lower() for w in str(data["name"]).split())

    def walk(node: Any, in_section: str = "") -> None:
        if isinstance(node, Mapping):
            if "indicator" in node and isinstance(node["indicator"], str):
                found.add(str(node["indicator"]).lower())
            for key, value in node.items():
                walk(value, in_section or (key if key in _RULE_SECTIONS else ""))
        elif isinstance(node, str):
            return
        elif isinstance(node, Sequence):
            for item in node:
                walk(item, in_section)

    for section in _RULE_SECTIONS:
        if data.get(section):
            found.add(section)
            walk(data[section], section)
    for key in ("stop", "trailing", "take_profits"):
        walk(data.get(key))
    return tuple(sorted(w for w in found if w and w not in _STOPWORDS and len(w) > 1))


@dataclass(frozen=True, slots=True)
class StructuralFingerprint:
    """A strategy reduced to its structure, plus the exact hash for comparison."""

    content_hash: str
    structure_hash: str
    tokens: tuple[str, ...]
    keywords: tuple[str, ...] = ()

    def namespace(self, ns: str) -> frozenset[str]:
        return frozenset(t for t in self.tokens if t.startswith(f"{ns}:"))

    def to_dict(self) -> dict[str, Any]:
        return {
            "content_hash": self.content_hash,
            "structure_hash": self.structure_hash,
            "tokens": list(self.tokens),
            "keywords": list(self.keywords),
        }


def fingerprint(document: Any) -> StructuralFingerprint:
    content = ""
    if hasattr(document, "content_hash"):
        content = str(document.content_hash())
    elif isinstance(document, Mapping):
        content = str(document.get("content_hash", ""))
    return StructuralFingerprint(
        content_hash=content,
        structure_hash=structure_hash(document),
        tokens=structural_tokens(document),
        keywords=keywords(document),
    )


def _jaccard(a: frozenset[str], b: frozenset[str]) -> float | None:
    if not a and not b:
        return None
    union = a | b
    return len(a & b) / len(union) if union else None


def structural_similarity(
    left: Sequence[str] | StructuralFingerprint,
    right: Sequence[str] | StructuralFingerprint,
) -> float:
    """Weighted per-namespace Jaccard in ``[0, 1]``.

    A namespace empty on BOTH sides is dropped and the remaining weights are
    renormalised, so a document with no take-profit legs is not penalised for
    matching another that also has none.
    """
    a = left.tokens if isinstance(left, StructuralFingerprint) else tuple(left)
    b = right.tokens if isinstance(right, StructuralFingerprint) else tuple(right)
    if not a and not b:
        return 0.0
    total = 0.0
    weight = 0.0
    for ns, w in NAMESPACE_WEIGHTS.items():
        left_ns = frozenset(t for t in a if t.startswith(f"{ns}:"))
        right_ns = frozenset(t for t in b if t.startswith(f"{ns}:"))
        score = _jaccard(left_ns, right_ns)
        if score is None:
            continue
        total += w * score
        weight += w
    return total / weight if weight > 0 else 0.0


def is_reparameterisation(left: Any, right: Any) -> bool:
    """True when two documents are the same strategy wearing different numbers."""
    a, b = fingerprint(left), fingerprint(right)
    if not a.structure_hash or not b.structure_hash:
        return False
    return a.structure_hash == b.structure_hash and a.content_hash != b.content_hash
