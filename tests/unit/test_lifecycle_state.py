"""The lifecycle state machine: legal edges, the LIVE invariant, the hash chain.

The tests that matter most here are the ones that would have caught the absence
this package was written to fix -- a promotion that skipped a stage, an automated
demotion that could also promote, and a history somebody could tidy.
"""
from __future__ import annotations

import itertools
import json
from datetime import timedelta

import pytest

from fiboki.core.enums import StrategyLifecycle
from fiboki.lifecycle.state import (
    HEALTH_STATES,
    LADDER,
    LEGAL_TRANSITIONS,
    RUNNING_STATES,
    Actor,
    ActorKind,
    Evidence,
    EvidenceKind,
    FileTransitionLog,
    HumanAuthorisation,
    HumanAuthorisationRequired,
    IllegalTransition,
    InMemoryTransitionLog,
    LifecycleError,
    LifecycleStateMachine,
    TamperedLog,
    TransitionKind,
    TransitionRecord,
    UnknownStrategy,
    ladder_index,
    transition_table,
)
from tests.lifecycle_fixtures import (
    AUTOMATED_ACTORS,
    HASH_A,
    HUMAN,
    RULE,
    T0,
    authorisation,
    machine_at,
    note,
)

# ---------------------------------------------------------------- the table


def test_every_state_is_reachable_and_retired_is_terminal():
    reachable = {t.to_state for t in LEGAL_TRANSITIONS}
    assert reachable == set(StrategyLifecycle), (
        "a state nothing can reach is a state the enum claims exists and the "
        "machine cannot produce"
    )
    outgoing = {t.from_state for t in LEGAL_TRANSITIONS if t.to_state is not t.from_state}
    assert StrategyLifecycle.RETIRED not in outgoing


def test_promotions_are_adjacent_only():
    promotions = [t for t in LEGAL_TRANSITIONS if t.kind is TransitionKind.PROMOTION]
    assert {t.key for t in promotions} == set(itertools.pairwise(LADDER))
    for t in promotions:
        assert ladder_index(t.to_state) - ladder_index(t.from_state) == 1


def test_demotions_may_drop_several_stages():
    assert LifecycleStateMachine.edge(
        StrategyLifecycle.LIVE, StrategyLifecycle.PAPER
    ) is not None
    assert LifecycleStateMachine.edge(
        StrategyLifecycle.PAPER, StrategyLifecycle.LIVE
    ) is None


@pytest.mark.parametrize(
    ("lower", "upper"),
    [
        (a, b)
        for a, b in itertools.product(LADDER, LADDER)
        if ladder_index(b) - ladder_index(a) >= 2
    ],
)
def test_promotion_cannot_skip_a_stage(lower, upper):
    m = machine_at(lower)
    with pytest.raises(IllegalTransition, match="illegal lifecycle transition"):
        m.transition(
            HASH_A, upper, actor=HUMAN, reason="fast path", evidence=note()
        )


def test_every_promotion_beyond_candidate_requires_a_human_actor():
    for t in LEGAL_TRANSITIONS:
        if t.kind is not TransitionKind.PROMOTION:
            continue
        assert t.requires_human_actor is (t.to_state in RUNNING_STATES)


def test_every_recovery_requires_a_human_actor():
    recoveries = [t for t in LEGAL_TRANSITIONS if t.kind is TransitionKind.RECOVERY]
    assert recoveries
    assert all(t.requires_human_actor for t in recoveries)


def test_transition_table_is_serialisable():
    rows = transition_table()
    assert len(rows) == len(LEGAL_TRANSITIONS)
    json.dumps(rows)  # must not raise


# ------------------------------------------------------------- the machine


def test_unregistered_strategy_raises_rather_than_defaulting():
    m = LifecycleStateMachine()
    with pytest.raises(UnknownStrategy, match="register it before"):
        m.state(HASH_A)


def test_double_registration_is_refused():
    m = machine_at(StrategyLifecycle.DISCOVERY)
    with pytest.raises(LifecycleError, match="already registered"):
        m.register(
            strategy_id="x", strategy_content_hash=HASH_A, actor=HUMAN, reason="again"
        )


def test_transition_requires_evidence():
    m = machine_at(StrategyLifecycle.DISCOVERY)
    with pytest.raises(LifecycleError, match="needs evidence"):
        m.transition(
            HASH_A, StrategyLifecycle.RESEARCH, actor=HUMAN, reason="why not", evidence=()
        )


def test_automatic_flag_is_derived_from_the_actor_not_supplied():
    m = machine_at(StrategyLifecycle.PAPER)
    record = m.transition(
        HASH_A,
        StrategyLifecycle.WATCH,
        actor=RULE,
        reason="monitor",
        evidence=note(),
    )
    assert record.history[-1].automatic is True
    assert record.history[-1].actor.kind is ActorKind.AUTOMATED_RULE

    m2 = machine_at(StrategyLifecycle.PAPER, content_hash="c" * 64)
    record2 = m2.transition(
        "c" * 64,
        StrategyLifecycle.WATCH,
        actor=HUMAN,
        reason="operator judgement",
        evidence=note(),
    )
    assert record2.history[-1].automatic is False


def test_an_automated_rule_must_name_itself():
    with pytest.raises(ValueError, match="must name itself"):
        Actor.rule("something")


def test_recovery_may_not_return_a_strategy_higher_than_it_left():
    m = machine_at(StrategyLifecycle.DEMO)
    m.transition(
        HASH_A, StrategyLifecycle.WATCH, actor=RULE, reason="drift", evidence=note()
    )
    with pytest.raises(IllegalTransition, match="may not return it higher"):
        m.transition(
            HASH_A,
            StrategyLifecycle.APPROVED,
            actor=HUMAN,
            reason="promote out of watch",
            evidence=note(),
        )
    record = m.transition(
        HASH_A,
        StrategyLifecycle.DEMO,
        actor=HUMAN,
        reason="cause understood and fixed",
        evidence=note(),
    )
    assert record.state is StrategyLifecycle.DEMO
    assert record.pre_health_state is None


def test_retired_is_terminal_at_runtime():
    m = machine_at(StrategyLifecycle.RETIRED)
    with pytest.raises(IllegalTransition, match="RETIRED is terminal"):
        m.transition(
            HASH_A, StrategyLifecycle.RESEARCH, actor=HUMAN, reason="revive", evidence=note()
        )


def test_time_in_state_measures_the_current_state_only():
    m = machine_at(StrategyLifecycle.PAPER)
    record = m.record(HASH_A)
    # machine_at promotes on day i for each ladder step; PAPER is step 4.
    assert record.entered_state_at() == T0 + timedelta(days=4)
    assert record.time_in_state(T0 + timedelta(days=34)) == pytest.approx(30 * 86400.0)


def test_evidence_chain_accumulates_across_the_history():
    m = machine_at(StrategyLifecycle.PAPER)
    chain = m.record(HASH_A).evidence_chain()
    assert len(chain) == len(m.record(HASH_A).history)
    assert all(isinstance(e, Evidence) for e in chain)


# ------------------------------------------------------------ LIVE is sealed


@pytest.mark.parametrize("actor", AUTOMATED_ACTORS, ids=lambda a: a.name)
def test_no_automated_actor_can_reach_live_even_with_an_authorisation(actor):
    """The parametrisation is the point: EVERY automated path, not a sample.

    An authorisation is supplied deliberately. The refusal must come from the
    actor's kind, so that forging an authorisation would still not open a path.
    """
    m = machine_at(StrategyLifecycle.APPROVED)
    with pytest.raises(HumanAuthorisationRequired, match="may not move a strategy to"):
        m.transition(
            HASH_A,
            StrategyLifecycle.LIVE,
            actor=actor,
            reason="criteria met",
            evidence=(*note(), authorisation().as_evidence()),
        )
    assert m.state(HASH_A) is StrategyLifecycle.APPROVED


def test_a_human_without_an_authorisation_cannot_reach_live():
    m = machine_at(StrategyLifecycle.APPROVED)
    with pytest.raises(HumanAuthorisationRequired, match="requires a HumanAuthorisation"):
        m.transition(
            HASH_A, StrategyLifecycle.LIVE, actor=HUMAN, reason="go", evidence=note()
        )


def test_an_authorisation_for_another_strategy_does_not_transfer():
    m = machine_at(StrategyLifecycle.APPROVED)
    other = authorisation(content_hash="b" * 64)
    with pytest.raises(HumanAuthorisationRequired, match="names strategy"):
        m.transition(
            HASH_A,
            StrategyLifecycle.LIVE,
            actor=HUMAN,
            reason="go",
            evidence=(*note(), other.as_evidence()),
        )


def test_an_authorisation_for_another_state_does_not_transfer():
    m = machine_at(StrategyLifecycle.APPROVED)
    wrong = authorisation(to_state=StrategyLifecycle.DEMO)
    with pytest.raises(HumanAuthorisationRequired, match="not"):
        m.transition(
            HASH_A,
            StrategyLifecycle.LIVE,
            actor=HUMAN,
            reason="go",
            evidence=(*note(), wrong.as_evidence()),
        )


def test_an_authorisation_needs_a_real_statement():
    with pytest.raises(ValueError, match="at least"):
        HumanAuthorisation(
            authorised_by="joe",
            strategy_content_hash=HASH_A,
            to_state=StrategyLifecycle.LIVE,
            statement="approved",
        )


def test_the_only_way_into_live_is_a_named_human_with_a_matching_authorisation():
    m = machine_at(StrategyLifecycle.APPROVED)
    record = m.transition(
        HASH_A,
        StrategyLifecycle.LIVE,
        actor=HUMAN,
        reason="Gate C evidence reviewed",
        evidence=(*note(), authorisation().as_evidence()),
    )
    assert record.state is StrategyLifecycle.LIVE
    assert record.history[-1].automatic is False
    kinds = {e.kind for e in record.history[-1].evidence}
    assert EvidenceKind.HUMAN_AUTHORISATION in kinds


@pytest.mark.parametrize("health", HEALTH_STATES, ids=lambda s: s.value)
@pytest.mark.parametrize("actor", AUTOMATED_ACTORS, ids=lambda a: a.name)
def test_no_automated_recovery_from_any_health_state_into_live(health, actor):
    m = machine_at(StrategyLifecycle.LIVE)
    m.transition(HASH_A, health, actor=RULE, reason="monitor", evidence=note())
    with pytest.raises(HumanAuthorisationRequired):
        m.transition(
            HASH_A,
            StrategyLifecycle.LIVE,
            actor=actor,
            reason="looks fine again",
            evidence=(*note(), authorisation().as_evidence()),
        )


# --------------------------------------------------------- the append-only log


def test_the_log_has_no_update_or_delete():
    for name in ("update", "delete", "remove", "edit", "pop"):
        assert not hasattr(InMemoryTransitionLog(), name)


def test_the_chain_verifies_over_a_real_history():
    m = machine_at(StrategyLifecycle.DEMO)
    verification = m.verify()
    assert verification.ok, verification.describe()
    assert verification.n_records == len(m.record(HASH_A).history)


def test_an_edited_record_breaks_the_chain(tmp_path):
    path = tmp_path / "lifecycle.jsonl"
    m = LifecycleStateMachine(FileTransitionLog(path))
    m.register(
        strategy_id="s", strategy_content_hash=HASH_A, actor=HUMAN, reason="seed", at=T0
    )
    for step in (
        StrategyLifecycle.RESEARCH,
        StrategyLifecycle.VALIDATING,
        StrategyLifecycle.CANDIDATE,
    ):
        m.transition(HASH_A, step, actor=HUMAN, reason="up", evidence=note(), at=T0)
    assert m.verify().ok

    lines = path.read_text().splitlines()
    tampered = json.loads(lines[1])
    tampered["reason"] = "a tidier reason nobody wrote"
    lines[1] = json.dumps(tampered, sort_keys=True, separators=(",", ":"))
    path.write_text("\n".join(lines) + "\n")

    reloaded = FileTransitionLog(path)
    verification = reloaded.verify()
    assert not verification.ok
    assert verification.broken_at == 1
    assert "record_hash" in verification.reason
    with pytest.raises(TamperedLog, match="refusing to load"):
        LifecycleStateMachine(reloaded)


def test_a_removed_record_breaks_the_chain(tmp_path):
    path = tmp_path / "lifecycle.jsonl"
    m = LifecycleStateMachine(FileTransitionLog(path))
    m.register(
        strategy_id="s", strategy_content_hash=HASH_A, actor=HUMAN, reason="seed", at=T0
    )
    for step in (StrategyLifecycle.RESEARCH, StrategyLifecycle.VALIDATING):
        m.transition(HASH_A, step, actor=HUMAN, reason="up", evidence=note(), at=T0)

    lines = path.read_text().splitlines()
    del lines[1]
    path.write_text("\n".join(lines) + "\n")

    verification = FileTransitionLog(path).verify()
    assert not verification.ok
    assert verification.broken_at == 1


def test_a_reordered_record_breaks_the_chain(tmp_path):
    path = tmp_path / "lifecycle.jsonl"
    m = LifecycleStateMachine(FileTransitionLog(path))
    m.register(
        strategy_id="s", strategy_content_hash=HASH_A, actor=HUMAN, reason="seed", at=T0
    )
    for step in (
        StrategyLifecycle.RESEARCH,
        StrategyLifecycle.VALIDATING,
        StrategyLifecycle.CANDIDATE,
    ):
        m.transition(HASH_A, step, actor=HUMAN, reason="up", evidence=note(), at=T0)

    lines = path.read_text().splitlines()
    lines[1], lines[2] = lines[2], lines[1]
    path.write_text("\n".join(lines) + "\n")

    verification = FileTransitionLog(path).verify()
    assert not verification.ok
    assert verification.broken_at == 1


def test_a_record_hash_cannot_be_forged_without_the_predecessor():
    """Rewriting one record's hash does not save it: the NEXT record's
    ``previous_hash`` still names the old digest."""
    m = machine_at(StrategyLifecycle.VALIDATING)
    rows = m.log.records()
    doctored = rows[1].to_dict()
    doctored["reason"] = "rewritten"
    rebuilt = TransitionRecord.from_dict(doctored)
    resealed = TransitionRecord.seal(
        sequence=rebuilt.sequence,
        at=rebuilt.at,
        strategy_id=rebuilt.strategy_id,
        strategy_content_hash=rebuilt.strategy_content_hash,
        from_state=rebuilt.from_state,
        to_state=rebuilt.to_state,
        kind=rebuilt.kind,
        actor=rebuilt.actor,
        reason=rebuilt.reason,
        evidence=rebuilt.evidence,
        previous_hash=rebuilt.previous_hash,
    )
    assert resealed.intact  # the forged record hashes fine on its own...
    forged = InMemoryTransitionLog([rows[0], resealed, *rows[2:]])
    assert not forged.verify().ok  # ...and the chain still refuses it
    assert forged.verify().broken_at == 2


def test_replay_reconstructs_the_state_from_the_log_alone(tmp_path):
    path = tmp_path / "lifecycle.jsonl"
    m = LifecycleStateMachine(FileTransitionLog(path))
    m.register(
        strategy_id="s", strategy_content_hash=HASH_A, actor=HUMAN, reason="seed", at=T0
    )
    for i, step in enumerate(LADDER[1:6], start=1):
        m.transition(
            HASH_A,
            step,
            actor=HUMAN,
            reason="up",
            evidence=note(),
            at=T0 + timedelta(days=i),
        )
    m.transition(
        HASH_A, StrategyLifecycle.WATCH, actor=RULE, reason="drift", evidence=note()
    )

    restarted = LifecycleStateMachine(FileTransitionLog(path))
    assert restarted.state(HASH_A) is StrategyLifecycle.WATCH
    assert restarted.record(HASH_A).pre_health_state is StrategyLifecycle.SHADOW
    assert len(restarted.record(HASH_A).history) == len(m.record(HASH_A).history)
