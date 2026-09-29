"""Agent influence tiers: how much an LLM may affect money, as a signed record.

Why this exists
---------------
The agentic plan (``docs/v2/AGENTIC_INTEGRATION_PLAN.md`` §4) gives agents two
bounded channels onto trading: the event veto (may only block a NEW entry) and
the conviction dampener (may only make a position SMALLER). Both ship with
``enabled=False``. "Disabled in config" is one control; this module is the
second, independent one. A policy may take effect only when the operator has
recorded a tier that permits it, so flipping a config flag on its own does
nothing but run the policy in shadow.

The ladder (``research/reports/G_frontend_plans_audit.md`` §3.7):

=====================  ==========================================================
Tier                   What agent output may do
=====================  ==========================================================
T0_OBSERVE             research artefacts only; nothing reaches trading
T1_ANNOTATE_SHADOW     annotations and convictions are written and evaluated in
                       SHADOW only (the default when no record exists)
T2_VETO_ENTRIES        the event veto may block a new entry
T3_DAMPEN_SIZE         the conviction policy may reduce a size, never below its
                       floor and never above 1.0
T4_AUTHOR_CANDIDATES   agent-authored strategies may enter the validation ladder
=====================  ==========================================================

There is no T5, and no tier permits upsizing, order origination, a risk-limit
change or a kill-switch action. :attr:`AgentInfluenceTier.permits_upsizing` is
``False`` for every member, and ``tests/unit/test_agent_tier.py`` pins that.

Two controls, safety-asymmetric
-------------------------------
The effective tier is ``min(signed record, MAX_AUTHORISED_TIER)``.
:data:`MAX_AUTHORISED_TIER` is a reviewed source constant, like
``LIVE_EXECUTION_COMPILED_IN``: raising agent influence needs a commit that
raises it AND an operator-signed record. Lowering needs only the record (or
deleting it, which falls back to T1). Nothing here can raise a tier from an
API request; ``fiboki agents tier set`` is the only writer.

The record
----------
``<state_dir>/agent_tier.json`` holds operator, timestamp, tier and reason, and
an HMAC-SHA256 over those fields keyed with ``FIBOKI_SESSION_SECRET``. The
live-authorisation record in ``broker/mode_guard.py`` is the pattern for the
fields and the "missing or unreadable means the safe default" rule; it is not
signed, this one is. A record whose signature does not verify is treated as
absent (T1) and reported as ``invalid_signature`` so the operator is alerted
rather than silently downgraded.

This module imports nothing from Fiboki (it sits in ``core``), so every layer
can read a tier and none has to import another to do it.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import tempfile
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import Enum
from pathlib import Path
from typing import Any

__all__ = [
    "DEFAULT_TIER",
    "MAX_AUTHORISED_TIER",
    "TIER_AUDIT_FILENAME",
    "TIER_RECORD_FILENAME",
    "TIER_RECORD_SCHEMA",
    "AgentInfluenceTier",
    "TierError",
    "TierReading",
    "TierRecord",
    "append_tier_audit",
    "default_tier_audit_path",
    "default_tier_path",
    "read_tier",
    "sign_tier_record",
    "write_tier_record",
]

TIER_RECORD_SCHEMA = "agent-tier-record:1"
TIER_RECORD_FILENAME = "agent_tier.json"
TIER_AUDIT_FILENAME = "agent_tier_audit.jsonl"


class TierError(ValueError):
    """A tier record is malformed, unsigned, or asks for something that does not exist."""


class AgentInfluenceTier(str, Enum):
    """How far agent output may reach. Values are persisted: never rename one."""

    T0_OBSERVE = "t0_observe"
    T1_ANNOTATE_SHADOW = "t1_annotate_shadow"
    T2_VETO_ENTRIES = "t2_veto_entries"
    T3_DAMPEN_SIZE = "t3_dampen_size"
    T4_AUTHOR_CANDIDATES = "t4_author_candidates"

    @property
    def level(self) -> int:
        return _LEVELS[self]

    @property
    def permits_shadow_writes(self) -> bool:
        """Annotations and convictions may be WRITTEN (never acted on below T2/T3)."""
        return self.level >= 1

    @property
    def permits_veto(self) -> bool:
        """The event veto may block a new entry."""
        return self.level >= 2

    @property
    def permits_dampen(self) -> bool:
        """The conviction policy may reduce a size (down only, floor >= 0.5)."""
        return self.level >= 3

    @property
    def permits_candidates(self) -> bool:
        """Agent-authored strategies may enter the validation ladder."""
        return self.level >= 4

    @property
    def permits_upsizing(self) -> bool:
        """Always False. No tier lets an agent make a position LARGER.

        Stated as a property rather than left as an absence so that the one
        policy field that could express upsizing (``ConvictionPolicy.max_factor``)
        has something to assert against, and so a future tier cannot quietly
        acquire it: changing this is a reviewed edit with a failing test.
        """
        return False

    def at_most(self, ceiling: AgentInfluenceTier) -> AgentInfluenceTier:
        return self if self.level <= ceiling.level else ceiling


_LEVELS: dict[AgentInfluenceTier, int] = {
    AgentInfluenceTier.T0_OBSERVE: 0,
    AgentInfluenceTier.T1_ANNOTATE_SHADOW: 1,
    AgentInfluenceTier.T2_VETO_ENTRIES: 2,
    AgentInfluenceTier.T3_DAMPEN_SIZE: 3,
    AgentInfluenceTier.T4_AUTHOR_CANDIDATES: 4,
}

#: The tier when no record exists: shadow only.
DEFAULT_TIER = AgentInfluenceTier.T1_ANNOTATE_SHADOW

#: THE REVIEWED CEILING. Raising it is a code change with a review, and it
#: does nothing on its own: an operator-signed record must also ask for the
#: higher tier. Both channels are pre-registered as shadow-only until a
#: decision date (AGENTIC_INTEGRATION_PLAN.md Wave 5), so this stays at T1.
MAX_AUTHORISED_TIER = AgentInfluenceTier.T1_ANNOTATE_SHADOW


def _utc_iso(ts: datetime) -> str:
    if ts.tzinfo is None or ts.utcoffset() is None:
        raise TierError("tier record timestamps must be timezone-aware UTC")
    return ts.astimezone(UTC).isoformat()


@dataclass(frozen=True, slots=True)
class TierRecord:
    """One operator decision about agent influence. Signed; never edited in place."""

    operator: str
    recorded_at: str
    tier: AgentInfluenceTier
    reason: str
    signature: str = ""
    schema: str = TIER_RECORD_SCHEMA

    def __post_init__(self) -> None:
        if not self.operator.strip():
            raise TierError("a tier record must name the operator who made it")
        if len(self.reason.strip()) < 10:
            raise TierError("a tier record needs a written reason (at least 10 characters)")
        if not isinstance(self.tier, AgentInfluenceTier):
            raise TierError(f"tier must be an AgentInfluenceTier, got {self.tier!r}")
        parsed = datetime.fromisoformat(self.recorded_at)
        if parsed.tzinfo is None:
            raise TierError("recorded_at must carry a UTC offset")

    def signed_fields(self) -> dict[str, str]:
        """Exactly the fields the signature covers, in canonical form."""
        return {
            "schema": self.schema,
            "operator": self.operator,
            "recorded_at": self.recorded_at,
            "tier": self.tier.value,
            "reason": self.reason,
        }

    def canonical(self) -> bytes:
        return json.dumps(self.signed_fields(), sort_keys=True, separators=(",", ":")).encode(
            "utf-8"
        )

    def expected_signature(self, secret: bytes) -> str:
        return hmac.new(secret, self.canonical(), hashlib.sha256).hexdigest()

    def verifies(self, secret: bytes) -> bool:
        if not secret or not self.signature:
            return False
        return hmac.compare_digest(self.signature, self.expected_signature(secret))

    def to_json(self) -> str:
        return json.dumps(
            {**self.signed_fields(), "signature": self.signature}, sort_keys=True, indent=2
        )

    @staticmethod
    def from_json(text: str) -> TierRecord:
        data = json.loads(text)
        if not isinstance(data, dict):
            raise TierError("a tier record is a JSON object")
        unknown = set(data) - {"schema", "operator", "recorded_at", "tier", "reason", "signature"}
        if unknown:
            raise TierError(f"a tier record carries unknown fields {sorted(unknown)}")
        try:
            tier = AgentInfluenceTier(str(data.get("tier", "")))
        except ValueError as exc:
            # Includes anything a hand-edited file might try: "t5_...", "live",
            # "upsize". The enum is the whole vocabulary.
            raise TierError(f"{data.get('tier')!r} is not an agent influence tier") from exc
        schema = str(data.get("schema", ""))
        if schema != TIER_RECORD_SCHEMA:
            raise TierError(f"unknown tier record schema {schema!r}")
        return TierRecord(
            operator=str(data.get("operator", "")),
            recorded_at=str(data.get("recorded_at", "")),
            tier=tier,
            reason=str(data.get("reason", "")),
            signature=str(data.get("signature", "")),
            schema=schema,
        )


def sign_tier_record(
    *,
    operator: str,
    tier: AgentInfluenceTier,
    reason: str,
    secret: bytes,
    at: datetime | None = None,
) -> TierRecord:
    """Build and sign a record. Refuses an empty key: an unsigned record is not a record."""
    if not secret:
        raise TierError("refusing to sign a tier record with an empty key")
    unsigned = TierRecord(
        operator=operator.strip(),
        recorded_at=_utc_iso(at or datetime.now(tz=UTC)),
        tier=tier,
        reason=reason.strip(),
    )
    return TierRecord(
        operator=unsigned.operator,
        recorded_at=unsigned.recorded_at,
        tier=unsigned.tier,
        reason=unsigned.reason,
        signature=unsigned.expected_signature(secret),
    )


@dataclass(frozen=True, slots=True)
class TierReading:
    """The tier in force, and why. Stamped on every allocation and risk decision.

    ``source`` is one of:

    * ``record``            a valid signed record, at or below the ceiling;
    * ``clamped``           a valid signed record above :data:`MAX_AUTHORISED_TIER`;
    * ``absent``            no record: the default (T1, shadow only);
    * ``invalid_signature`` a record exists but does not verify: default, ALERT;
    * ``unreadable``        a record exists but cannot be parsed: default, ALERT;
    * ``no_secret``         no key to verify with: default, ALERT;
    * ``not_supplied``      the composition root passed no reading at all.
    """

    tier: AgentInfluenceTier
    source: str
    detail: str = ""
    requested: AgentInfluenceTier | None = None
    operator: str = ""
    recorded_at: str = ""
    ceiling: AgentInfluenceTier = MAX_AUTHORISED_TIER

    @property
    def needs_alert(self) -> bool:
        """A record exists and could not be honoured as written."""
        return self.source in ("invalid_signature", "unreadable", "no_secret", "clamped")

    def stamp(self) -> dict[str, Any]:
        return {
            "agent_tier": self.tier.value,
            "agent_tier_source": self.source,
            "agent_tier_requested": None if self.requested is None else self.requested.value,
            "agent_tier_ceiling": self.ceiling.value,
        }

    @staticmethod
    def default(source: str = "not_supplied", detail: str = "") -> TierReading:
        return TierReading(tier=DEFAULT_TIER, source=source, detail=detail)


def default_tier_path(state_dir: str | Path) -> Path:
    return Path(state_dir) / TIER_RECORD_FILENAME


def default_tier_audit_path(state_dir: str | Path) -> Path:
    return Path(state_dir) / TIER_AUDIT_FILENAME


def read_tier(
    path: str | Path | None,
    secret: bytes | None,
    *,
    ceiling: AgentInfluenceTier = MAX_AUTHORISED_TIER,
) -> TierReading:
    """Read the effective tier. Never raises; never returns more than ``ceiling``.

    Every failure falls back to :data:`DEFAULT_TIER` (T1: shadow only), which
    is the tier at which neither agent channel can change anything. The
    reading says which failure it was, so the caller can alert.
    """
    if path is None:
        return TierReading.default("absent", "no tier record path configured")
    p = Path(path)
    if not p.exists():
        return TierReading.default("absent", f"{p} does not exist")
    try:
        record = TierRecord.from_json(p.read_text(encoding="utf-8"))
    except Exception as exc:  # malformed JSON, unknown tier, missing fields
        return TierReading.default("unreadable", f"{type(exc).__name__}: {exc}")
    if not secret:
        return TierReading(
            tier=DEFAULT_TIER, source="no_secret",
            detail="FIBOKI_SESSION_SECRET is unset, so the record cannot be verified",
            requested=record.tier, operator=record.operator, recorded_at=record.recorded_at,
            ceiling=ceiling,
        )
    if not record.verifies(secret):
        return TierReading(
            tier=DEFAULT_TIER, source="invalid_signature",
            detail="the record's HMAC does not verify with the configured key",
            requested=record.tier, operator=record.operator, recorded_at=record.recorded_at,
            ceiling=ceiling,
        )
    effective = record.tier.at_most(ceiling)
    return TierReading(
        tier=effective,
        source="record" if effective is record.tier else "clamped",
        detail="" if effective is record.tier else (
            f"requested {record.tier.value} exceeds the reviewed ceiling {ceiling.value}"
        ),
        requested=record.tier,
        operator=record.operator,
        recorded_at=record.recorded_at,
        ceiling=ceiling,
    )


def write_tier_record(path: str | Path, record: TierRecord) -> None:
    """Replace the record atomically (write to a temporary file, then rename)."""
    if not record.signature:
        raise TierError("refusing to write an unsigned tier record")
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".agent_tier.", dir=str(p.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(record.to_json())
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, p)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


def append_tier_audit(path: str | Path, entry: dict[str, Any]) -> None:
    """Append one line to the tier audit trail (JSON lines, fsynced)."""
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    with p.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(entry, sort_keys=True) + "\n")
        fh.flush()
        os.fsync(fh.fileno())
