"""TRADING / RESEARCH: the promotion ladder's current state, read-only.

``lifecycle/service.py`` ends ``StrategyStatus`` and ``LifecycleEvaluation`` with
``to_dict()`` methods shaped for an API, and nothing served them. An operator
could not answer "is this strategy still trading, and if not, what stopped it?"
from the platform at all — the answer existed only in a worker's memory and a
JSONL file on a disk.

These routes serve exactly those two objects and nothing else. In particular:

**Everything here is a GET.** Promotion is a named human's act with cited
evidence, recorded by ``LifecycleStateMachine``; demotion is an automated rule's
act on a worker's timer. Neither belongs behind an HTTP verb, and adding one
would create a route that can put risk back on from a browser.

**Every number is a labelled** :class:`~fiboki.api.provenance.Figure`. A health
score and a degradation score are measurements of forward performance, so they
carry the provenance of the mode that produced them —
``PAPER`` on a paper deployment — and a figure nobody has evaluated yet is
:meth:`Figure.missing`, never ``0.0``. ``health`` in particular is ``1 - score``
only where a score exists; an unevaluated strategy is not healthy, it is
unobserved, and ``ever_evaluated`` says which.
"""
from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, Query, status
from pydantic import BaseModel, ConfigDict

from fiboki.api.deps import PlatformDep, SettingsDep
from fiboki.api.errors import ApiError
from fiboki.api.models import Envelope, Page, SourceNote
from fiboki.api.provenance import Figure, figure
from fiboki.core.enums import Provenance

router = APIRouter(prefix="/api/trading/lifecycle", tags=["trading", "lifecycle"])

__all__ = ["router"]


def _note(platform: Any) -> SourceNote:
    source = platform.lifecycle_source()
    return SourceNote(
        kind="live" if source.healthy else "absent",
        detail=source.detail,
        as_of=datetime.now(tz=UTC),
    )


def _provenance(settings: Any) -> Provenance:
    """Where a lifecycle number came from: the mode that produced the forward data.

    Not a constant. A degradation score computed from demo fills and one
    computed from paper fills are different evidence, and the figure has to say
    which.
    """
    return settings.provenance_for_execution()


# ---------------------------------------------------------------- models


class StrategyStatusView(BaseModel):
    """The queryable current state of one strategy on the ladder."""

    model_config = ConfigDict(frozen=True)

    strategy_id: str
    strategy_content_hash: str
    lifecycle: str
    band: str
    degraded: bool
    ever_evaluated: bool
    entered_state_at: datetime
    last_evaluated_at: datetime | None
    latched_halts: tuple[str, ...]
    missing_rule_registrations: tuple[str, ...]
    health: Figure
    last_score: Figure
    last_confidence: Figure
    n_transitions: Figure


class RuleEvaluationView(BaseModel):
    model_config = ConfigDict(frozen=True)

    kind: str
    fired: bool
    registration_id: str
    statistic: Figure
    threshold: Figure
    detail: str = ""


class DivergenceDimensionView(BaseModel):
    model_config = ConfigDict(frozen=True)

    dimension: str
    diverged: bool
    expected: Figure
    observed: Figure


class LifecycleEvaluationView(BaseModel):
    """Everything one monitoring tick concluded about one strategy."""

    model_config = ConfigDict(frozen=True)

    strategy_content_hash: str
    at: datetime
    state_before: str
    state_after: str
    demoted: bool
    summary: str
    score: Figure
    confidence: Figure
    rule_evaluations: tuple[RuleEvaluationView, ...]
    divergence: tuple[DivergenceDimensionView, ...]
    latched_halts: tuple[str, ...]
    notes: tuple[str, ...]


# ------------------------------------------------------------ rendering


def _f(value: Any, provenance: Provenance, *, unit: str, reason: str) -> Figure:
    """A labelled number, or an explicitly absent one. Never a coerced zero."""
    if value is None:
        return Figure.missing(provenance, unit=unit, reason=reason)
    return figure(float(value), provenance, unit=unit)


def _status_view(status_obj: Any, provenance: Provenance) -> StrategyStatusView:
    raw = status_obj.to_dict()
    unevaluated = "This strategy has never been evaluated by the monitors."
    return StrategyStatusView(
        strategy_id=raw["strategy_id"],
        strategy_content_hash=raw["strategy_content_hash"],
        lifecycle=raw["lifecycle"],
        band=raw["band"],
        degraded=raw["degraded"],
        ever_evaluated=raw["ever_evaluated"],
        entered_state_at=raw["entered_state_at"],
        last_evaluated_at=raw["last_evaluated_at"],
        latched_halts=tuple(raw["latched_halts"]),
        missing_rule_registrations=tuple(raw["missing_rule_registrations"]),
        # health is 1.0 by construction when nothing has been evaluated. That is
        # not a measurement, so it is reported as present only alongside
        # ``ever_evaluated``; the score beside it is explicitly missing.
        health=figure(float(raw["health"]), provenance, unit="ratio"),
        last_score=_f(raw["last_score"], provenance, unit="ratio", reason=unevaluated),
        last_confidence=_f(
            raw["last_confidence"], provenance, unit="ratio", reason=unevaluated
        ),
        n_transitions=figure(float(raw["n_transitions"]), provenance, unit="count"),
    )


def _evaluation_view(raw: dict[str, Any], provenance: Provenance) -> LifecycleEvaluationView:
    degradation = raw.get("degradation") or {}
    rules = tuple(
        RuleEvaluationView(
            kind=str(item.get("kind", "")),
            # ``RuleEvaluation.to_dict`` reports a STATUS string, not a boolean:
            # "fired", "clear" and "insufficient_data" are three answers and
            # only one of them is a halt. Collapsing the last two into False is
            # right for this flag and the status text is carried in ``detail``.
            fired=str(item.get("status", "")) == "fired",
            registration_id=str(item.get("registration_id", "")),
            statistic=_f(
                item.get("statistic"),
                provenance,
                unit="ratio",
                reason="The rule could not be evaluated on this tick.",
            ),
            threshold=_f(
                item.get("threshold"),
                provenance,
                unit="ratio",
                reason="This rule declares no numeric threshold.",
            ),
            detail=str(item.get("description", "") or item.get("reason", "")),
        )
        for item in raw.get("rule_evaluations") or ()
    )
    divergence_raw = raw.get("divergence") or {}
    dimensions = tuple(
        DivergenceDimensionView(
            dimension=str(item.get("dimension", "")),
            diverged=str(item.get("status", "")) == "diverged",
            expected=_f(
                item.get("expected"),
                provenance,
                unit="ratio",
                reason="No expectation was supplied for this dimension.",
            ),
            observed=_f(
                item.get("observed"),
                provenance,
                unit="ratio",
                reason="No observation was supplied for this dimension.",
            ),
        )
        for item in (divergence_raw.get("divergences") or ())
    )
    return LifecycleEvaluationView(
        strategy_content_hash=raw["strategy_content_hash"],
        at=raw["at"],
        state_before=raw["state_before"],
        state_after=raw["state_after"],
        demoted=bool(raw["demoted"]),
        summary=raw["summary"],
        score=_f(
            degradation.get("score"),
            provenance,
            unit="ratio",
            reason="The tick produced no degradation score.",
        ),
        confidence=_f(
            degradation.get("confidence"),
            provenance,
            unit="ratio",
            reason="The tick produced no confidence.",
        ),
        rule_evaluations=rules,
        divergence=dimensions,
        latched_halts=tuple(raw.get("latched_halts") or ()),
        notes=tuple(raw.get("notes") or ()),
    )


# ------------------------------------------------------------- the routes


@router.get("/strategies", response_model=Page[StrategyStatusView])
def strategies(
    platform: PlatformDep,
    settings: SettingsDep,
    degraded_only: bool = Query(False, description="Only strategies off the healthy band."),
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
) -> Page[StrategyStatusView]:
    """Every strategy the lifecycle store knows about, newest state first."""
    provenance = _provenance(settings)
    statuses = list(platform.lifecycle.statuses())
    if degraded_only:
        statuses = [s for s in statuses if s.degraded]
    statuses.sort(key=lambda s: (not s.degraded, s.strategy_id))
    window = statuses[offset : offset + limit]
    return Page[StrategyStatusView](
        items=[_status_view(s, provenance) for s in window],
        total=len(statuses),
        offset=offset,
        limit=limit,
        source=_note(platform),
    )


@router.get("/strategies/{content_hash}", response_model=Envelope[StrategyStatusView])
def strategy(
    content_hash: str, platform: PlatformDep, settings: SettingsDep
) -> Envelope[StrategyStatusView]:
    try:
        found = platform.lifecycle.status(content_hash)
    except Exception as exc:
        raise ApiError(
            status.HTTP_404_NOT_FOUND,
            "lifecycle_strategy_not_found",
            f"No strategy {content_hash!r} in the lifecycle store: {exc}",
        ) from exc
    return Envelope[StrategyStatusView](
        data=_status_view(found, _provenance(settings)), source=_note(platform)
    )


@router.get(
    "/strategies/{content_hash}/evaluation",
    response_model=Envelope[LifecycleEvaluationView],
)
def last_evaluation(
    content_hash: str, platform: PlatformDep, settings: SettingsDep
) -> Envelope[LifecycleEvaluationView]:
    """The most recent monitoring tick for this strategy.

    404 when the strategy exists but has never been evaluated. That is a
    different answer from "healthy", and returning an empty evaluation would be
    the coercion this API exists to prevent.
    """
    try:
        platform.lifecycle.status(content_hash)
    except Exception as exc:
        raise ApiError(
            status.HTTP_404_NOT_FOUND,
            "lifecycle_strategy_not_found",
            f"No strategy {content_hash!r} in the lifecycle store: {exc}",
        ) from exc
    evaluation = platform.lifecycle.last_evaluation(content_hash)
    if evaluation is None:
        raise ApiError(
            status.HTTP_404_NOT_FOUND,
            "lifecycle_never_evaluated",
            (
                f"Strategy {content_hash!r} is registered but the monitors have "
                "never evaluated it. That is not the same as healthy: nothing has "
                "looked."
            ),
        )
    return Envelope[LifecycleEvaluationView](
        data=_evaluation_view(evaluation.to_dict(), _provenance(settings)),
        source=_note(platform),
    )
