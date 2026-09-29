"""The agent influence tier: a signed, clamped, safety-asymmetric record.

Named tests from research/reports/G_frontend_plans_audit.md §3.7 where they
apply: ``test_tier_record_cannot_express_never_items`` and
``test_policies_default_disabled``.
"""
from __future__ import annotations

import json
from datetime import UTC, datetime

import pytest

from fiboki.cli import main
from fiboki.core.tier import (
    DEFAULT_TIER,
    MAX_AUTHORISED_TIER,
    AgentInfluenceTier,
    TierError,
    TierReading,
    TierRecord,
    read_tier,
    sign_tier_record,
    write_tier_record,
)

SECRET = b"a-test-session-secret-of-some-length"
AT = datetime(2026, 9, 29, 9, 0, tzinfo=UTC)
T = AgentInfluenceTier


def _record(tier: AgentInfluenceTier = T.T1_ANNOTATE_SHADOW, **kw) -> TierRecord:
    return sign_tier_record(
        operator=kw.get("operator", "joe"),
        tier=tier,
        reason=kw.get("reason", "pre-registered shadow evaluation started"),
        secret=kw.get("secret", SECRET),
        at=AT,
    )


# ----------------------------------------------------------------- the enum


def test_the_ladder_is_t0_to_t4_and_nothing_else() -> None:
    assert [t.value for t in T] == [
        "t0_observe", "t1_annotate_shadow", "t2_veto_entries", "t3_dampen_size",
        "t4_author_candidates",
    ]
    assert [t.level for t in T] == [0, 1, 2, 3, 4]


def test_each_tier_permits_exactly_its_channel() -> None:
    assert [t.permits_veto for t in T] == [False, False, True, True, True]
    assert [t.permits_dampen for t in T] == [False, False, False, True, True]
    assert [t.permits_candidates for t in T] == [False, False, False, False, True]


def test_no_tier_permits_upsizing() -> None:
    """The asserted absence ConvictionPolicy.max_factor rests on."""
    assert not any(t.permits_upsizing for t in T)


def test_tier_record_cannot_express_never_items() -> None:
    """G §3.7 'Never': order origination, upsizing, limits, kill switch, modes."""
    forbidden = ("order", "size_up", "upsiz", "limit", "kill", "live", "execute", "t5")
    for member in T:
        for word in forbidden:
            assert word not in member.value, member
    for smuggled in ("t5_protective_suggestions", "live", "upsize", "place_order", ""):
        text = json.dumps(
            {
                "schema": "agent-tier-record:1", "operator": "joe",
                "recorded_at": AT.isoformat(), "tier": smuggled,
                "reason": "a long enough reason", "signature": "00",
            }
        )
        with pytest.raises(TierError):
            TierRecord.from_json(text)


def test_a_record_with_an_unknown_field_is_refused() -> None:
    data = json.loads(_record().to_json())
    data["max_factor"] = 2.0
    with pytest.raises(TierError, match="unknown fields"):
        TierRecord.from_json(json.dumps(data))


def test_the_default_and_the_ceiling_are_shadow_only() -> None:
    """test_policies_default_disabled, tier half: nothing is enabled by default."""
    assert DEFAULT_TIER is T.T1_ANNOTATE_SHADOW
    assert MAX_AUTHORISED_TIER is T.T1_ANNOTATE_SHADOW
    assert not DEFAULT_TIER.permits_veto and not DEFAULT_TIER.permits_dampen


# ------------------------------------------------------------- the record


def test_a_signed_record_round_trips_and_verifies(tmp_path) -> None:
    path = tmp_path / "agent_tier.json"
    write_tier_record(path, _record(T.T0_OBSERVE))
    reading = read_tier(path, SECRET)
    assert reading.tier is T.T0_OBSERVE and reading.source == "record"
    assert reading.operator == "joe" and not reading.needs_alert


def test_an_absent_record_means_t1(tmp_path) -> None:
    reading = read_tier(tmp_path / "missing.json", SECRET)
    assert reading.tier is T.T1_ANNOTATE_SHADOW and reading.source == "absent"
    assert not reading.needs_alert


def test_a_tampered_record_falls_back_to_t1_and_alerts(tmp_path) -> None:
    path = tmp_path / "agent_tier.json"
    write_tier_record(path, _record(T.T0_OBSERVE))
    data = json.loads(path.read_text())
    data["reason"] = "someone edited the file by hand afterwards"
    path.write_text(json.dumps(data))
    reading = read_tier(path, SECRET)
    assert reading.tier is DEFAULT_TIER and reading.source == "invalid_signature"
    assert reading.needs_alert and reading.requested is T.T0_OBSERVE


def test_a_record_signed_with_another_key_does_not_verify(tmp_path) -> None:
    path = tmp_path / "agent_tier.json"
    write_tier_record(path, _record(T.T0_OBSERVE, secret=b"someone-else"))
    assert read_tier(path, SECRET).source == "invalid_signature"


def test_no_secret_means_the_record_cannot_be_honoured(tmp_path) -> None:
    path = tmp_path / "agent_tier.json"
    write_tier_record(path, _record(T.T0_OBSERVE))
    reading = read_tier(path, None)
    assert reading.tier is DEFAULT_TIER and reading.source == "no_secret" and reading.needs_alert


def test_an_unreadable_record_falls_back_to_t1(tmp_path) -> None:
    path = tmp_path / "agent_tier.json"
    path.write_text("{not json")
    reading = read_tier(path, SECRET)
    assert reading.tier is DEFAULT_TIER and reading.source == "unreadable"


def test_a_signed_record_above_the_ceiling_is_clamped(tmp_path) -> None:
    """Raising a tier needs the reviewed constant AND the record."""
    path = tmp_path / "agent_tier.json"
    write_tier_record(path, _record(T.T3_DAMPEN_SIZE))
    reading = read_tier(path, SECRET)
    assert reading.tier is MAX_AUTHORISED_TIER and reading.source == "clamped"
    assert reading.requested is T.T3_DAMPEN_SIZE
    raised = read_tier(path, SECRET, ceiling=T.T3_DAMPEN_SIZE)
    assert raised.tier is T.T3_DAMPEN_SIZE and raised.source == "record"


def test_unsigned_or_empty_key_records_are_refused(tmp_path) -> None:
    with pytest.raises(TierError):
        sign_tier_record(operator="joe", tier=T.T0_OBSERVE, reason="ten chars plus", secret=b"")
    unsigned = TierRecord(
        operator="joe", recorded_at=AT.isoformat(), tier=T.T0_OBSERVE, reason="ten chars plus"
    )
    with pytest.raises(TierError):
        write_tier_record(tmp_path / "x.json", unsigned)


def test_a_record_needs_an_operator_and_a_reason() -> None:
    with pytest.raises(TierError):
        _record(operator=" ")
    with pytest.raises(TierError):
        _record(reason="short")


def test_the_stamp_names_tier_source_and_ceiling() -> None:
    stamp = TierReading.default("absent").stamp()
    assert stamp == {
        "agent_tier": "t1_annotate_shadow",
        "agent_tier_source": "absent",
        "agent_tier_requested": None,
        "agent_tier_ceiling": "t1_annotate_shadow",
    }


# ------------------------------------------------------------------ the CLI


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("FIBOKI_STATE_DIR", str(tmp_path))
    monkeypatch.setenv("FIBOKI_SESSION_SECRET", SECRET.decode())
    return tmp_path


def test_cli_status_reports_the_default(env) -> None:
    assert main(["agents", "tier", "status", "--json"]) == 0


def test_cli_set_signs_audits_and_status_reads_it_back(env) -> None:
    code = main([
        "agents", "tier", "set", "--tier", "t0_observe", "--operator", "joe",
        "--reason", "lowering while the classifier is re-evaluated",
    ])
    assert code == 0
    reading = read_tier(env / "agent_tier.json", SECRET)
    assert reading.tier is T.T0_OBSERVE and reading.operator == "joe"
    audit = [json.loads(line) for line in (env / "agent_tier_audit.jsonl").read_text().splitlines()]
    assert len(audit) == 1
    assert audit[0]["tier"] == "t0_observe" and audit[0]["operator"] == "joe"
    assert audit[0]["previous"]["agent_tier_source"] == "absent"
    assert main(["agents", "tier", "status"]) == 0


def test_cli_set_above_the_ceiling_is_refused_and_writes_nothing(env) -> None:
    code = main([
        "agents", "tier", "set", "--tier", "t3_dampen_size", "--operator", "joe",
        "--reason", "trying to enable dampening early",
    ])
    assert code == 2
    assert not (env / "agent_tier.json").exists()
    assert not (env / "agent_tier_audit.jsonl").exists()


def test_cli_set_requires_operator_reason_and_a_real_secret(env, monkeypatch) -> None:
    assert main(["agents", "tier", "set", "--tier", "t0_observe", "--reason", "x" * 20]) == 2
    assert main(["agents", "tier", "set", "--tier", "t0_observe", "--operator", "joe"]) == 2
    monkeypatch.delenv("FIBOKI_SESSION_SECRET")
    assert main([
        "agents", "tier", "set", "--tier", "t0_observe", "--operator", "joe",
        "--reason", "no secret configured here",
    ]) == 2
    assert not (env / "agent_tier.json").exists()


def test_cli_status_fails_on_a_record_it_cannot_verify(env) -> None:
    write_tier_record(env / "agent_tier.json", _record(T.T0_OBSERVE, secret=b"other-key"))
    assert main(["agents", "tier", "status"]) == 1
