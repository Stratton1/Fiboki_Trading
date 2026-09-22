"""The orchestrator holds queues and records, not reasoning state."""
from __future__ import annotations

from collections.abc import Mapping
from typing import Any

import pytest

from fiboki.agents.capabilities import Capability
from fiboki.agents.orchestrator import (
    EventTrigger,
    JobContext,
    JobSpec,
    JobStatus,
    JobType,
    ManualClock,
    Orchestrator,
    OrchestratorError,
    ScheduledJob,
    UnknownJobType,
    canonical_key,
    submitter_for,
)


def _ok(ctx: JobContext) -> Mapping[str, Any]:
    return {"echo": dict(ctx.payload), "attempt": ctx.attempt}


def _orchestrator(handler: Any = _ok, **services: Any) -> tuple[Orchestrator, ManualClock]:
    clock = ManualClock()
    orch = Orchestrator(clock=clock)
    orch.register_handler(JobType.BACKTEST, handler, services=services)
    return orch, clock


def _spec(**overrides: Any) -> JobSpec:
    base: dict[str, Any] = {
        "job_type": JobType.BACKTEST,
        "queue": "research",
        "payload": {"strategy_id": "s1", "instrument": "EURUSD"},
    }
    base.update(overrides)
    return JobSpec(**base)


# ------------------------------------------------------------ idempotency


def test_a_repeated_idempotency_key_does_not_run_twice() -> None:
    orch, _clock = _orchestrator()
    first = orch.submit(_spec(idempotency_key="cycle-7"))
    second = orch.submit(_spec(idempotency_key="cycle-7", payload={"different": True}))
    assert second.job_id == first.job_id
    assert second.deduplicated is True
    assert len(orch.records()) == 1
    done = orch.drain("research")
    assert len(done) == 1


def test_an_identical_payload_derives_the_same_key() -> None:
    a = _spec()
    b = _spec(payload={"instrument": "EURUSD", "strategy_id": "s1"})  # key order swapped
    assert a.idempotency_key == b.idempotency_key
    assert a.idempotency_key == canonical_key(JobType.BACKTEST, a.payload)


def test_a_different_payload_derives_a_different_key() -> None:
    a = _spec()
    b = _spec(payload={"strategy_id": "s2", "instrument": "EURUSD"})
    assert a.idempotency_key != b.idempotency_key


def test_resubmitting_after_success_still_deduplicates() -> None:
    orch, _clock = _orchestrator()
    orch.submit(_spec(idempotency_key="once"))
    orch.drain("research")
    again = orch.submit(_spec(idempotency_key="once"))
    assert again.status is JobStatus.SUCCEEDED
    assert again.deduplicated is True
    assert len(orch.records()) == 1


# ------------------------------------------------------ purity of handlers


def test_a_handler_sees_only_its_recorded_inputs_and_injected_services() -> None:
    seen: dict[str, Any] = {}

    def handler(ctx: JobContext) -> Mapping[str, Any]:
        seen["payload"] = dict(ctx.payload)
        seen["services"] = sorted(ctx.services)
        seen["has_orchestrator"] = any(
            isinstance(v, Orchestrator) for v in ctx.services.values()
        )
        return {"ok": True}

    orch, _clock = _orchestrator(handler, research="store", bars="source")
    orch.submit(_spec())
    orch.drain("research")
    assert seen["payload"] == {"strategy_id": "s1", "instrument": "EURUSD"}
    assert seen["services"] == ["bars", "research"]
    assert seen["has_orchestrator"] is False


def test_a_handler_with_the_wrong_signature_is_refused() -> None:
    orch = Orchestrator()
    with pytest.raises(OrchestratorError, match="exactly one argument"):
        orch.register_handler(
            JobType.BACKTEST, lambda ctx, extra: {}, services={}
        )


def test_a_job_with_no_handler_is_refused_at_submit() -> None:
    orch = Orchestrator()
    with pytest.raises(UnknownJobType, match="no handler registered"):
        orch.submit(_spec())


def test_context_require_names_the_missing_payload_keys() -> None:
    def handler(ctx: JobContext) -> Mapping[str, Any]:
        ctx.require("strategy_id", "missing_key")
        return {}

    orch, _clock = _orchestrator(handler)
    orch.submit(_spec(max_attempts=1))
    record = orch.drain("research")[0]
    assert record.status is JobStatus.DEAD_LETTER
    assert "missing_key" in record.error


def test_context_service_names_the_missing_service() -> None:
    def handler(ctx: JobContext) -> Mapping[str, Any]:
        ctx.service("nonexistent")
        return {}

    orch, _clock = _orchestrator(handler)
    orch.submit(_spec(max_attempts=1))
    record = orch.drain("research")[0]
    assert "nonexistent" in record.error


# ---------------------------------------------------- retries and backoff


def test_a_failing_job_retries_with_exponential_backoff_then_dead_letters() -> None:
    attempts: list[int] = []

    def flaky(ctx: JobContext) -> Mapping[str, Any]:
        attempts.append(ctx.attempt)
        raise RuntimeError(f"attempt {ctx.attempt} failed")

    orch, clock = _orchestrator(flaky)
    orch.submit(_spec(max_attempts=3, backoff_seconds=2.0, backoff_factor=2.0))

    first = orch.run_next("research")
    assert first is not None and first.status is JobStatus.FAILED
    assert first.next_attempt_at is not None
    assert (first.next_attempt_at - clock.now()).total_seconds() == pytest.approx(2.0)

    # Too early: the backoff is honoured rather than spun through.
    assert orch.run_next("research") is None
    clock.advance(2.0)
    second = orch.run_next("research")
    assert second is not None and second.status is JobStatus.FAILED

    assert orch.run_next("research") is None  # backoff doubled to 4s
    clock.advance(3.0)
    assert orch.run_next("research") is None
    clock.advance(1.0)
    third = orch.run_next("research")
    assert third is not None and third.status is JobStatus.DEAD_LETTER
    assert attempts == [1, 2, 3]
    assert third.attempt_count == 3
    assert "attempt 3 failed" in third.error


def test_backoff_is_capped() -> None:
    spec = _spec(backoff_seconds=10.0, backoff_factor=10.0, max_backoff_seconds=60.0)
    assert spec.backoff_for(1) == 10.0
    assert spec.backoff_for(2) == 60.0
    assert spec.backoff_for(9) == 60.0


def test_a_recovering_job_succeeds_on_a_later_attempt() -> None:
    calls: list[int] = []

    def flaky(ctx: JobContext) -> Mapping[str, Any]:
        calls.append(ctx.attempt)
        if ctx.attempt < 2:
            raise RuntimeError("transient")
        return {"recovered_on": ctx.attempt}

    orch, clock = _orchestrator(flaky)
    orch.submit(_spec(max_attempts=3, backoff_seconds=1.0))
    orch.run_next("research")
    clock.advance(1.0)
    record = orch.run_next("research")
    assert record is not None
    assert record.status is JobStatus.SUCCEEDED
    assert record.result == {"recovered_on": 2}
    assert calls == [1, 2]


# ------------------------------------------------- schedules and events


def test_a_schedule_submits_once_per_interval_bucket() -> None:
    orch, clock = _orchestrator()
    orch.schedule(
        ScheduledJob(
            name="nightly_backtest",
            job_type=JobType.BACKTEST,
            queue="research",
            payload={"strategy_id": "s1"},
            interval_seconds=3600.0,
        )
    )
    assert len(orch.tick()) == 1
    assert orch.tick() == ()  # same bucket
    clock.advance(3600.0)
    assert len(orch.tick()) == 1
    assert len(orch.records()) == 2


def test_a_duplicate_schedule_name_is_refused() -> None:
    orch, _clock = _orchestrator()
    job = ScheduledJob(
        name="dup", job_type=JobType.BACKTEST, queue="research",
        payload={}, interval_seconds=60.0,
    )
    orch.schedule(job)
    with pytest.raises(OrchestratorError, match="already exists"):
        orch.schedule(job)


def test_an_event_trigger_builds_a_job_from_the_event() -> None:
    orch, _clock = _orchestrator()
    orch.on_event(
        EventTrigger(
            event="new_data_arrived",
            job_type=JobType.BACKTEST,
            queue="research",
            build=lambda payload: {"strategy_id": "s1", "instrument": payload["instrument"]},
        )
    )
    submitted = orch.emit("new_data_arrived", {"instrument": "GBPUSD"})
    assert len(submitted) == 1
    assert submitted[0].spec.payload["instrument"] == "GBPUSD"
    assert submitted[0].spec.trigger == "event:new_data_arrived"
    assert orch.emit("unrelated_event", {}) == ()


def test_the_same_event_twice_deduplicates() -> None:
    orch, _clock = _orchestrator()
    orch.on_event(
        EventTrigger(
            event="e", job_type=JobType.BACKTEST, queue="research",
            build=lambda payload: {"strategy_id": "s1"},
        )
    )
    first = orch.emit("e", {})[0]
    second = orch.emit("e", {})[0]
    assert second.job_id == first.job_id
    assert second.deduplicated


# ------------------------------------------------------------- plumbing


def test_a_spec_records_the_capabilities_its_submitter_needed() -> None:
    spec = _spec(required_capabilities=frozenset({Capability.SUBMIT_JOB}))
    assert spec.fingerprint()["required_capabilities"] == ["submit:job"]


def test_submitter_for_hands_back_a_ticket() -> None:
    orch, _clock = _orchestrator()
    ticket = submitter_for(orch)(_spec())
    assert ticket.job_type == "backtest"
    assert ticket.status == "pending"
    assert ticket.deduplicated is False


def test_a_spec_needs_a_queue_and_a_sane_retry_policy() -> None:
    with pytest.raises(ValueError, match="named queue"):
        _spec(queue="")
    with pytest.raises(ValueError, match="max_attempts"):
        _spec(max_attempts=0)
    with pytest.raises(ValueError, match="backoff"):
        _spec(backoff_factor=0.5)


def test_counts_and_queue_views() -> None:
    orch, _clock = _orchestrator()
    orch.submit(_spec(idempotency_key="k1"))
    orch.submit(_spec(idempotency_key="k2", payload={"strategy_id": "s2"}))
    assert orch.counts()["pending"] == 2
    assert orch.queue_names() == ("research",)
    assert len(orch.pending("research")) == 2
    orch.drain("research")
    assert orch.counts()["succeeded"] == 2
