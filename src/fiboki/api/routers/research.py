"""RESEARCH: hypotheses, experiments, strategies, validation reports, parameter lab.

Backed by the real strategy registry (``research/strategies``) and, where one is
configured, the real append-only experiment ledger. When the ledger is absent
this router says so in ``source.kind`` rather than returning an empty list that
reads as "nothing has ever been tried".
"""
from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query, Request, status
from pydantic import BaseModel, ConfigDict, Field

from fiboki.api.deps import AuditDep, PlatformDep, SettingsDep
from fiboki.api.errors import ApiError
from fiboki.api.logging import current_correlation_id
from fiboki.api.models import Envelope, Page, SourceNote
from fiboki.api.provenance import Caveat, Figure
from fiboki.api.security import Principal, require_admin
from fiboki.core.enums import Provenance

router = APIRouter(prefix="/api/research", tags=["research"])

AdminPrincipal = Annotated[Principal, Depends(require_admin)]


def _note(kind: str, detail: str) -> SourceNote:
    return SourceNote(kind=kind, detail=detail, as_of=datetime.now(tz=UTC))


class StrategyView(BaseModel):
    model_config = ConfigDict(frozen=True)

    strategy_id: str
    name: str
    family: str
    author: str
    hypothesis: str
    timeframes: list[str]
    universe: list[str]
    content_hash: str
    schema_version: str
    complexity: Figure
    parameter_count: Figure
    rule_count: Figure
    parent_strategy_ids: list[str]
    notes: str


class HypothesisView(BaseModel):
    model_config = ConfigDict(frozen=True)

    strategy_id: str
    statement: str
    family: str
    structural_keywords: list[str]
    tested: bool
    supporting_experiments: Figure


class ExperimentView(BaseModel):
    model_config = ConfigDict(frozen=True)

    experiment_id: str
    strategy_id: str
    actor: str
    actor_kind: str
    outcome: str
    created_at: datetime | None
    hypothesis: str
    dataset_version_id: str
    verdict: str


class ValidationSummary(BaseModel):
    model_config = ConfigDict(frozen=True)

    strategy_id: str
    verdict: str
    report_version: str
    dataset_version_id: str
    gate_set_version: str
    rungs_passed: Figure
    rungs_total: Figure
    binding_constraint: str
    provenance_labels: dict[str, str]
    available: bool
    detail: str


class ParameterRow(BaseModel):
    model_config = ConfigDict(frozen=True)

    name: str
    current: Figure
    minimum: Figure
    maximum: Figure
    step: Figure
    kind: str
    description: str


class ParameterLabView(BaseModel):
    model_config = ConfigDict(frozen=True)

    strategy_id: str
    parameters: list[ParameterRow]
    search_space_size: Figure
    #: Computed, because the number of combinations IS the multiple-testing
    #: burden, and an operator varying five knobs deserves to see it.
    deflation_warning: str


def _param_value(spec: Any, attr: str) -> float | None:
    value = getattr(spec, attr, None)
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


@router.get("/strategies", response_model=Page[StrategyView])
def strategies(platform: PlatformDep) -> Page[StrategyView]:
    docs = platform.strategy_documents()
    items = [
        StrategyView(
            strategy_id=d.strategy_id,
            name=d.name,
            family=str(getattr(d.family, "value", d.family)),
            author=getattr(d, "author", ""),
            hypothesis=getattr(d, "hypothesis", ""),
            timeframes=[str(getattr(t, "value", t)) for t in getattr(d, "timeframes", [])],
            universe=list(getattr(d, "universe", [])),
            content_hash=d.content_hash()[:12],
            schema_version=str(getattr(d, "schema_version", "")),
            complexity=Figure(
                value=float(d.complexity_score()) if callable(getattr(d, "complexity_score", None))
                else None,
                provenance=Provenance.BACKTEST,
                unit="score",
            ),
            parameter_count=Figure(
                value=float(len(getattr(d, "parameters", {}) or {})),
                provenance=Provenance.BACKTEST,
                unit="count",
            ),
            rule_count=Figure(
                value=float(len(d.all_rules())) if callable(getattr(d, "all_rules", None))
                else None,
                provenance=Provenance.BACKTEST,
                unit="count",
            ),
            parent_strategy_ids=list(getattr(d, "parent_strategy_ids", []) or []),
            notes=getattr(d, "notes", "") or "",
        )
        for d in docs
    ]
    return Page(
        items=items,
        total=len(items),
        source=_note(
            "live" if items else "absent",
            f"Strategy registry, {len(items)} document(s) from research/strategies.",
        ),
    )


@router.get("/strategies/{strategy_id}", response_model=Envelope[StrategyView])
def strategy_detail(strategy_id: str, platform: PlatformDep) -> Envelope[StrategyView]:
    page = strategies(platform)
    match = next((s for s in page.items if s.strategy_id == strategy_id), None)
    if match is None:
        raise ApiError(status.HTTP_404_NOT_FOUND, "unknown_strategy", "No such strategy.")
    return Envelope(data=match, source=page.source)


@router.get("/hypotheses", response_model=Page[HypothesisView])
def hypotheses(platform: PlatformDep) -> Page[HypothesisView]:
    from fiboki.research.structure import keywords

    items: list[HypothesisView] = []
    for d in platform.strategy_documents():
        try:
            words = sorted(keywords(d))[:12]
        except Exception:
            words = []
        items.append(
            HypothesisView(
                strategy_id=d.strategy_id,
                statement=getattr(d, "hypothesis", "") or "(no hypothesis recorded)",
                family=str(getattr(d.family, "value", d.family)),
                structural_keywords=words,
                tested=False,
                supporting_experiments=Figure.missing(
                    Provenance.BACKTEST,
                    unit="count",
                    reason="No experiment ledger is configured, so the number of "
                    "supporting experiments is unknown rather than zero.",
                ),
            )
        )
    return Page(
        items=items,
        total=len(items),
        source=_note("live", "Derived from the registered strategy documents."),
    )


@router.get("/experiments", response_model=Page[ExperimentView])
def experiments(
    platform: PlatformDep,
    settings: SettingsDep,
    limit: int = Query(100, ge=1, le=500),
) -> Page[ExperimentView]:
    if settings.experiment_db is None:
        return Page(
            items=[],
            total=0,
            limit=limit,
            source=_note(
                "absent",
                "No experiment ledger is configured (FIBOKI_EXPERIMENT_DB). This "
                "is an EMPTY SOURCE, not an empty research record: nothing can be "
                "concluded from the absence of rows here.",
            ),
            caveats=(
                Caveat(
                    code="source_not_configured",
                    severity="warning",
                    message="The append-only experiment ledger is not attached to "
                    "this deployment.",
                    direction="unknown",
                ),
            ),
        )
    from fiboki.research.experiment import ExperimentLedger

    try:
        with ExperimentLedger(settings.experiment_db) as ledger:
            rows = ledger.list(limit=limit)
    except Exception as exc:
        raise ApiError(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "experiment_ledger_unavailable",
            "The experiment ledger could not be read. The page must not render "
            "this as 'no experiments'.",
            context={"error_class": type(exc).__name__},
        ) from exc

    items = [
        ExperimentView(
            # fiboki.research.experiment.Experiment names these `id`,
            # `actor_name` and `hypothesis_id`. Reading `experiment_id`,
            # `actor` and `hypothesis` through getattr defaults meant this
            # view rendered every row with a blank id, actor and hypothesis
            # however full the ledger was -- the getattr default turned three
            # wrong attribute names into silence instead of an AttributeError.
            experiment_id=getattr(r, "id", ""),
            strategy_id=getattr(r, "strategy_id", ""),
            actor=getattr(r, "actor_name", ""),
            actor_kind=str(getattr(getattr(r, "actor_kind", ""), "value", "")),
            outcome=str(getattr(getattr(r, "outcome", ""), "value", "")),
            created_at=getattr(r, "created_at", None),
            hypothesis=getattr(r, "hypothesis_id", "") or "",
            dataset_version_id=getattr(r, "dataset_version_id", "") or "",
            verdict=str(getattr(getattr(r, "outcome", ""), "value", "")),
        )
        for r in rows
    ]
    return Page(
        items=items,
        total=len(items),
        limit=limit,
        source=_note("live", "Append-only experiment ledger."),
    )


@router.get("/validation", response_model=Page[ValidationSummary])
def validation_reports(platform: PlatformDep) -> Page[ValidationSummary]:
    """Validation reports on disk, per strategy.

    A strategy with no report is reported as ``available=False`` with an
    explanation, never as a passing one with blank fields.
    """
    from pathlib import Path

    from fiboki.validation.report import ValidationReport

    reports_dir = Path("research/reports")
    items: list[ValidationSummary] = []
    for doc in platform.strategy_documents():
        path = reports_dir / f"{doc.strategy_id}.json"
        if not path.exists():
            items.append(
                ValidationSummary(
                    strategy_id=doc.strategy_id,
                    verdict="not_validated",
                    report_version="",
                    dataset_version_id="",
                    gate_set_version="",
                    rungs_passed=Figure.missing(Provenance.HOLDOUT, unit="count"),
                    rungs_total=Figure.missing(Provenance.HOLDOUT, unit="count"),
                    binding_constraint="",
                    provenance_labels={},
                    available=False,
                    detail="No validation report exists for this strategy. It has "
                    "not been through the ladder; this is not a pass.",
                )
            )
            continue
        try:
            report = ValidationReport.load(path)
        except Exception as exc:
            items.append(
                ValidationSummary(
                    strategy_id=doc.strategy_id,
                    verdict="unreadable",
                    report_version="",
                    dataset_version_id="",
                    gate_set_version="",
                    rungs_passed=Figure.missing(Provenance.HOLDOUT, unit="count"),
                    rungs_total=Figure.missing(Provenance.HOLDOUT, unit="count"),
                    binding_constraint="",
                    provenance_labels={},
                    available=False,
                    detail=f"A report exists but could not be parsed ({type(exc).__name__}).",
                )
            )
            continue
        passed = sum(1 for r in report.rungs if r.passed)
        items.append(
            ValidationSummary(
                strategy_id=report.strategy_id,
                verdict=str(getattr(report.verdict, "value", report.verdict)),
                report_version=report.report_version,
                dataset_version_id=report.dataset_version_id,
                gate_set_version=report.gate_set_version,
                rungs_passed=Figure(
                    value=float(passed), provenance=Provenance.HOLDOUT, unit="count"
                ),
                rungs_total=Figure(
                    value=float(len(report.rungs)),
                    provenance=Provenance.HOLDOUT,
                    unit="count",
                ),
                binding_constraint=report.binding_constraint.describe(),
                provenance_labels=dict(report.provenance_labels),
                available=True,
                detail=report.summary(),
            )
        )
    return Page(
        items=items,
        total=len(items),
        source=_note("mixed", "research/reports, one file per strategy."),
    )


@router.get("/parameter-lab/{strategy_id}", response_model=Envelope[ParameterLabView])
def parameter_lab(strategy_id: str, platform: PlatformDep) -> Envelope[ParameterLabView]:
    doc = platform.strategy(strategy_id)
    if doc is None:
        raise ApiError(status.HTTP_404_NOT_FOUND, "unknown_strategy", "No such strategy.")
    params = getattr(doc, "parameters", {}) or {}
    rows: list[ParameterRow] = []
    combinations = 1.0
    for name, spec in sorted(params.items()):
        low = _param_value(spec, "minimum")
        high = _param_value(spec, "maximum")
        step = _param_value(spec, "step") or 1.0
        current = _param_value(spec, "default")
        if low is not None and high is not None and step:
            combinations *= max(1.0, (high - low) / step + 1.0)
        rows.append(
            ParameterRow(
                name=name,
                current=Figure(value=current, provenance=Provenance.BACKTEST),
                minimum=Figure(value=low, provenance=Provenance.BACKTEST),
                maximum=Figure(value=high, provenance=Provenance.BACKTEST),
                step=Figure(value=step, provenance=Provenance.BACKTEST),
                kind=str(getattr(spec, "kind", "") or type(spec).__name__),
                description=str(getattr(spec, "description", "") or ""),
            )
        )
    warning = (
        f"Sweeping this space is {combinations:,.0f} parameterisation(s). Every one "
        "is a trial, and the deflated Sharpe in any validation report must be "
        "deflated by that count. Searching more is not finding more."
        if combinations > 1
        else "No numeric parameter ranges are declared on this strategy."
    )
    view = ParameterLabView(
        strategy_id=strategy_id,
        parameters=rows,
        search_space_size=Figure(
            value=combinations, provenance=Provenance.BACKTEST, unit="count"
        ),
        deflation_warning=warning,
    )
    return Envelope(data=view, source=_note("live", "Declared parameter domains."))


class ExperimentRequest(BaseModel):
    strategy_id: str
    hypothesis: str = Field(min_length=10, max_length=2000)
    reason: str = Field(min_length=8, max_length=500)


@router.post("/experiments", response_model=Envelope[dict], status_code=status.HTTP_202_ACCEPTED)
def queue_experiment(
    body: ExperimentRequest,
    platform: PlatformDep,
    settings: SettingsDep,
    audit: AuditDep,
    principal: AdminPrincipal,
    request: Request,
) -> Envelope[dict]:
    """Queue an experiment. Admin-gated and audited: it consumes compute and,
    through the multiple-testing count, dilutes every later claim of edge."""
    if platform.strategy(body.strategy_id) is None:
        raise ApiError(status.HTTP_404_NOT_FOUND, "unknown_strategy", "No such strategy.")
    audit.record(
        "experiment.queue",
        actor=principal.user_id,
        actor_role=principal.role.value,
        outcome="allowed",
        reason=body.reason,
        target=body.strategy_id,
        execution_mode=settings.execution_mode.value,
        correlation_id=current_correlation_id(),
        source_ip=request.client.host if request.client else "",
        detail={"hypothesis": body.hypothesis[:400]},
    )
    return Envelope(
        data={
            "queued": False,
            "recorded": True,
            "detail": "No orchestrator is attached to this deployment, so the "
            "request was recorded to the audit trail but not dispatched.",
        },
        source=_note("absent", "No orchestrator attached."),
    )


@router.get("/datasets", response_model=Page[dict])
def datasets(platform: PlatformDep, settings: SettingsDep) -> Page[dict]:
    if settings.data_root is None or not settings.data_root.exists():
        return Page(
            items=[],
            total=0,
            source=_note(
                "absent",
                "No market-data root is mounted, so no dataset versions can be "
                "listed. Research run against this deployment has no data lineage.",
            ),
        )
    from fiboki.data.versioning import DatasetCatalogue

    try:
        with DatasetCatalogue(settings.data_root / "catalogue.sqlite") as catalogue:
            versions = catalogue.list_versions()
    except Exception as exc:
        raise ApiError(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "dataset_catalogue_unavailable",
            "The dataset catalogue could not be read.",
            context={"error_class": type(exc).__name__},
        ) from exc
    items = [
        {
            "version_id": v.version_id,
            "short_id": v.short_id,
            "describe": v.describe(),
        }
        for v in versions
    ]
    return Page(items=items, total=len(items), source=_note("live", "Dataset catalogue."))
