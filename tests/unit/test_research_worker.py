"""The V1 sweep bug, and its fix, stated as tests.

V1 checkpointed every cell as DONE the instant it was computed, then inspected
the result. A cell that returned no data was therefore recorded as complete and
permanently skipped on every subsequent run. At the end it wrote
``phase1_complete.json``, which was false: roughly 8% of the universe had never
been computed and never would be, and every ranking downstream was built on the
truncated set for months.

Three properties are asserted here, and they are the whole point of the module:

1. a resumed sweep does not recompute a DONE cell;
2. a NO_DATA cell is NOT checkpointed as done, and IS recomputed;
3. too many no-data cells raises, and no completion marker can be written.
"""
from __future__ import annotations

import json

import pytest

from fiboki.agents.orchestrator import JobSpec, JobStatus, JobType, Orchestrator
from fiboki.obs.alerts import AlertDispatcher, AlertEvent, MemoryChannel
from fiboki.workers.base import WorkerStore
from fiboki.workers.research_worker import (
    CellOutcome,
    CellStatus,
    CheckpointStore,
    IncompleteSweep,
    NoDataFractionExceeded,
    ResearchWorker,
    ResearchWorkerConfig,
    SweepCell,
    SweepRunner,
    write_completion_marker,
)


@pytest.fixture
def checkpoints(tmp_path):
    store = CheckpointStore(tmp_path / "sweep.db")
    yield store
    store.close()


@pytest.fixture
def store(tmp_path):
    s = WorkerStore.sqlite_at(tmp_path / "state.db")
    yield s
    s.close()


def cells(n: int, prefix: str = "c") -> list[SweepCell]:
    return [SweepCell(key=f"{prefix}{i}", params={"i": i, "instrument": "EURUSD"}) for i in range(n)]


# ------------------------------------------------------------- checkpointing


def test_a_done_cell_is_not_recomputed_on_resume(checkpoints):
    computed: list[str] = []

    def compute(cell):
        computed.append(cell.key)
        return CellOutcome.done({"sharpe": 0.4})

    runner = SweepRunner(checkpoints, min_cells_for_guard=1000)
    first = runner.run("s1", cells(10), compute)
    assert first.done == 10
    assert first.computed_this_run == 10
    assert first.complete is True

    computed.clear()
    second = runner.run("s1", cells(10), compute)
    assert computed == [], "a completed cell was recomputed"
    assert second.computed_this_run == 0
    assert second.skipped_this_run == 10
    assert second.complete is True


def test_a_partial_sweep_resumes_from_where_it_stopped(checkpoints):
    """The 10,000-cell-sweep-survives-a-restart property, in miniature."""
    computed: list[str] = []

    def compute(cell):
        computed.append(cell.key)
        if len(computed) >= 4:
            raise KeyboardInterrupt("simulated Ctrl-C mid-sweep")
        return CellOutcome.done({"ok": True})

    runner = SweepRunner(checkpoints, min_cells_for_guard=1000)
    with pytest.raises(KeyboardInterrupt):
        runner.run("s1", cells(10), compute)
    assert len(computed) == 4

    # Restart. Only the untouched cells are recomputed.
    computed.clear()

    def compute_ok(cell):
        computed.append(cell.key)
        return CellOutcome.done({"ok": True})

    report = runner.run("s1", cells(10), compute_ok)
    assert len(computed) == 7, "3 done + the interrupted one + 6 untouched = 7 to do"
    assert report.done == 10
    assert report.complete is True


# ------------------------------------------------------- THE no-data failure


def test_a_no_data_cell_is_NOT_checkpointed_as_done(checkpoints):
    """THE V1 bug. A no-data cell must remain outstanding."""
    runner = SweepRunner(checkpoints, min_cells_for_guard=1000)

    def compute(cell):
        return CellOutcome.no_data("no bars in this window") if cell.key == "c3" else (
            CellOutcome.done({"sharpe": 0.1})
        )

    report = runner.run("s1", cells(10), compute)

    assert checkpoints.status_of("s1", "c3") is CellStatus.NO_DATA
    assert checkpoints.status_of("s1", "c3") is not CellStatus.DONE
    assert "c3" not in checkpoints.completed_keys("s1")
    assert report.no_data == 1
    assert report.done == 9
    assert report.outstanding == 1
    # And, crucially, the sweep is NOT complete.
    assert report.complete is False


def test_a_no_data_cell_is_recomputed_on_the_next_run(checkpoints):
    """V1 skipped it forever. Here it is retried, and can then succeed."""
    attempts: dict[str, int] = {}

    def compute(cell):
        attempts[cell.key] = attempts.get(cell.key, 0) + 1
        # c3 has no data the first time (the provider was down) and data the
        # second time. V1 could never have discovered this.
        if cell.key == "c3" and attempts[cell.key] == 1:
            return CellOutcome.no_data("provider timeout")
        return CellOutcome.done({"sharpe": 0.2})

    runner = SweepRunner(checkpoints, min_cells_for_guard=1000)
    first = runner.run("s1", cells(5), compute)
    assert first.complete is False
    assert first.no_data == 1

    second = runner.run("s1", cells(5), compute)
    assert attempts["c3"] == 2, "the no-data cell was not retried"
    assert attempts["c0"] == 1, "a done cell was recomputed"
    assert second.done == 5
    assert second.complete is True


def test_returning_None_is_treated_as_no_data_not_as_success(checkpoints):
    """The exact coercion V1 got backwards."""
    runner = SweepRunner(checkpoints, min_cells_for_guard=1000)
    report = runner.run("s1", cells(3), lambda cell: None)
    assert report.done == 0
    assert report.no_data == 3
    assert report.complete is False
    assert checkpoints.completed_keys("s1") == set()


def test_an_empty_mapping_is_no_data(checkpoints):
    runner = SweepRunner(checkpoints, min_cells_for_guard=1000)
    report = runner.run("s1", cells(2), lambda cell: {})
    assert report.no_data == 2


def test_a_raising_cell_is_failed_not_done(checkpoints):
    runner = SweepRunner(checkpoints, min_cells_for_guard=1000)

    def compute(cell):
        if cell.key == "c1":
            raise ValueError("bad params")
        return CellOutcome.done({})

    report = runner.run("s1", cells(3), compute)
    assert checkpoints.status_of("s1", "c1") is CellStatus.FAILED
    assert report.failed == 1
    assert report.complete is False


def test_an_unrecognised_return_type_raises_rather_than_guessing(checkpoints):
    """A compute function with the wrong return type is a BUG, not a data
    condition, so it propagates immediately rather than being coerced.

    Guessing whether an arbitrary object counts as success is the ambiguity
    that produced the false completion marker in the first place.
    """
    runner = SweepRunner(checkpoints, min_cells_for_guard=1000)
    with pytest.raises(TypeError, match="must return CellOutcome"):
        runner.run("s1", cells(1), lambda cell: 42)
    # And nothing was recorded as done.
    assert checkpoints.completed_keys("s1") == set()


# --------------------------------------------------------- the no-data guard


def test_the_no_data_fraction_guard_fires(checkpoints):
    """Above the threshold the sweep STOPS. V1 carried on and shipped."""
    channel = MemoryChannel()
    dispatcher = AlertDispatcher([channel])
    runner = SweepRunner(
        checkpoints,
        max_no_data_fraction=0.10,
        min_cells_for_guard=10,
        dispatcher=dispatcher,
    )

    def compute(cell):
        # 30% no data -- well above the 10% threshold.
        return CellOutcome.no_data("gap") if int(cell.key[1:]) % 10 < 3 else CellOutcome.done({})

    with pytest.raises(NoDataFractionExceeded) as exc:
        runner.run("s1", cells(100), compute)

    assert exc.value.fraction > 0.10
    # The message must carry the RAW COUNT: "6% missing" reads as noise,
    # "612 of 10,000 produced nothing" does not.
    assert "returned NO DATA" in str(exc.value)
    assert AlertEvent.SWEEP_NO_DATA_EXCEEDED in channel.events()


def test_the_guard_does_not_fire_on_a_small_sample(checkpoints):
    """Two no-data cells out of three is 67% -- and statistically nothing."""
    runner = SweepRunner(checkpoints, max_no_data_fraction=0.02, min_cells_for_guard=50)
    report = runner.run("s1", cells(3), lambda cell: None)
    assert report.no_data == 3  # recorded, not raised on


def test_the_guard_fires_early_rather_than_after_the_whole_sweep(checkpoints):
    """A sweep destined to fail the guard must not burn nine hours first."""
    computed: list[str] = []

    def compute(cell):
        computed.append(cell.key)
        return CellOutcome.no_data("nothing here")

    runner = SweepRunner(checkpoints, max_no_data_fraction=0.05, min_cells_for_guard=20)
    with pytest.raises(NoDataFractionExceeded):
        runner.run("s1", cells(5000), compute)
    assert len(computed) == 20, "the guard should stop at the first evaluation point"


def test_accumulated_no_data_across_restarts_still_trips_the_guard(checkpoints):
    """Three runs each under the per-run threshold can still leave a truncated
    sweep. The whole-sweep guard is what catches that."""
    runner = SweepRunner(checkpoints, max_no_data_fraction=0.05, min_cells_for_guard=20)
    all_cells = cells(100)
    persistently_empty = {c.key for c in all_cells[:6]}

    # Earlier runs: 90 succeeded, 6 found nothing, 4 were never reached.
    for cell in all_cells[10:]:
        checkpoints.record("s1", cell.key, CellOutcome.done({}))
    for key in persistently_empty:
        checkpoints.record("s1", key, CellOutcome.no_data("earlier run"))
    checkpoints.begin_sweep("s1", 100)

    def compute(cell):
        # Only 10 cells are pending, so the PER-RUN guard (min 20 computed)
        # never evaluates. The whole-sweep guard is the only thing that can
        # catch this, which is exactly why it exists.
        if cell.key in persistently_empty:
            return CellOutcome.no_data("still nothing")
        return CellOutcome.done({})

    with pytest.raises(NoDataFractionExceeded) as exc:
        runner.run("s1", all_cells, compute)
    assert exc.value.total == 100
    assert exc.value.no_data == 6


# ------------------------------------------------- the false completion marker


def test_a_completion_marker_cannot_be_written_for_an_incomplete_sweep(
    checkpoints, tmp_path
):
    """``phase1_complete.json``, the artefact that lied for months."""
    runner = SweepRunner(checkpoints, min_cells_for_guard=1000)

    def compute(cell):
        return CellOutcome.no_data("gap") if cell.key == "c1" else CellOutcome.done({})

    report = runner.run("s1", cells(5), compute)
    marker = tmp_path / "phase1_complete.json"

    with pytest.raises(IncompleteSweep) as exc:
        write_completion_marker(marker, report)

    assert not marker.exists()
    assert "INCOMPLETE" in str(exc.value)


def test_a_completion_marker_is_written_when_the_sweep_really_is_complete(
    checkpoints, tmp_path
):
    runner = SweepRunner(checkpoints, min_cells_for_guard=1000)
    report = runner.run("s1", cells(5), lambda cell: CellOutcome.done({"sharpe": 0.3}))
    marker = write_completion_marker(tmp_path / "complete.json", report)
    payload = json.loads(marker.read_text())
    assert payload["complete"] is True
    assert payload["no_data"] == 0
    assert payload["done"] == 5


def test_a_sweep_stopped_early_is_never_complete(checkpoints):
    stop = {"now": False}
    runner = SweepRunner(
        checkpoints, min_cells_for_guard=1000, should_stop=lambda: stop["now"]
    )

    def compute(cell):
        if cell.key == "c2":
            stop["now"] = True
        return CellOutcome.done({})

    report = runner.run("s1", cells(10), compute)
    assert report.stopped_early is True
    assert report.complete is False


def test_excluding_a_cell_requires_a_reason(checkpoints):
    with pytest.raises(ValueError):
        checkpoints.exclude("s1", "c0", "")
    checkpoints.exclude("s1", "c0", "instrument did not exist before 2015")
    assert checkpoints.status_of("s1", "c0") is CellStatus.EXCLUDED
    assert "c0" in checkpoints.completed_keys("s1")


def test_an_excluded_cell_counts_toward_completeness(checkpoints):
    runner = SweepRunner(checkpoints, min_cells_for_guard=1000)
    checkpoints.exclude("s1", "c4", "no venue listing in this period")
    report = runner.run("s1", cells(5), lambda cell: CellOutcome.done({}))
    assert report.excluded == 1
    assert report.done == 4
    assert report.complete is True


# ------------------------------------------------------------------- worker


def _orchestrator_with_handler(results=None):
    orchestrator = Orchestrator()
    calls: list[str] = []

    def handler(ctx):
        calls.append(ctx.job_id)
        if results is not None and results.get(ctx.payload.get("tag")) == "raise":
            raise RuntimeError("handler exploded")
        return {"ok": True}

    for job_type in (JobType.BACKTEST, JobType.VALIDATION):
        orchestrator.register_handler(job_type, handler)
    return orchestrator, calls


def test_the_worker_drains_the_orchestrator_queue(store):
    orchestrator, calls = _orchestrator_with_handler()
    for i in range(3):
        orchestrator.submit(
            JobSpec(job_type=JobType.BACKTEST, queue="research", payload={"i": i})
        )
    config = ResearchWorkerConfig(
        max_cycles=3, idle_sleep_seconds=0.0, busy_sleep_seconds=0.0, jobs_per_cycle=1
    )
    worker = ResearchWorker(orchestrator, store, config, worker="a@h:1")
    worker.run(install_signals=False)
    assert len(calls) == 3
    assert all(r.status is JobStatus.SUCCEEDED for r in worker.processed)


def test_the_worker_does_not_reimplement_retries(store):
    """Retry policy belongs to the orchestrator; two sources of truth for
    'did this job run' is how a job runs twice."""
    orchestrator, _ = _orchestrator_with_handler({"boom": "raise"})
    orchestrator.submit(
        JobSpec(
            job_type=JobType.BACKTEST,
            queue="research",
            payload={"tag": "boom"},
            max_attempts=2,
            backoff_seconds=0.0,
        )
    )
    config = ResearchWorkerConfig(
        max_cycles=3, idle_sleep_seconds=0.0, busy_sleep_seconds=0.0
    )
    worker = ResearchWorker(orchestrator, store, config, worker="a@h:1")
    worker.run(install_signals=False)
    statuses = [r.status for r in worker.processed]
    assert JobStatus.DEAD_LETTER in statuses


def test_a_dead_lettered_job_raises_an_alert(store):
    channel = MemoryChannel()
    dispatcher = AlertDispatcher([channel])
    orchestrator, _ = _orchestrator_with_handler({"boom": "raise"})
    orchestrator.submit(
        JobSpec(
            job_type=JobType.BACKTEST,
            queue="research",
            payload={"tag": "boom"},
            max_attempts=1,
        )
    )
    config = ResearchWorkerConfig(max_cycles=2, idle_sleep_seconds=0.0, busy_sleep_seconds=0.0)
    worker = ResearchWorker(orchestrator, store, config, worker="a@h:1", dispatcher=dispatcher)
    worker.run(install_signals=False)
    assert AlertEvent.JOB_DEAD_LETTERED in channel.events()


def test_an_idle_worker_writes_a_heartbeat_anyway(store):
    """An idle worker and a dead worker must not look the same."""
    orchestrator, _ = _orchestrator_with_handler()
    config = ResearchWorkerConfig(max_cycles=2, idle_sleep_seconds=0.0)
    worker = ResearchWorker(orchestrator, store, config, worker="a@h:1")
    worker.run(install_signals=False)
    rows = store.heartbeat_rows()
    assert rows[0]["cycles_ok"] == 2
    assert rows[0]["jobs_done"] == 0
