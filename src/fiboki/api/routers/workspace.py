"""WORKSPACE: tiling layouts, the panel registry, presets, grid column sets.

``GET /api/workspace/panels``
    The typed panel registry (``api/workspace_layouts.py``): every kind the
    workstation can tile, what it reads and whether it follows or publishes a
    symbol. The workstation's own registry is typed against the same union.
``GET /api/workspace/presets``
    "FX monitoring", "Research", "Incident": code, reviewed in commits.
``GET /api/workspace/layouts`` · ``GET/PUT/DELETE /api/workspace/layouts/{name}``
    The signed-in operator's named layouts, server-side (``api/workspace_store.py``).
    Versioned with optimistic concurrency: a PUT names ``base_version`` and a
    stale one is refused with 409 and the current version, so two tabs cannot
    overwrite each other. Every accepted write is a revision (``.../history``)
    and an API audit row; ``action`` says whether the operator saved or the
    workstation autosaved.
``GET/PUT /api/workspace/column-sets/{grid_id}``
    Saved column sets (order, visibility, pins) for one data grid, per operator,
    versioned the same way.

Everything here is display state. Nothing is read by a strategy, the risk
engine, a backtest or the broker layer.
"""
from __future__ import annotations

import sqlite3
from datetime import UTC, datetime
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, Query, Request, Response, status
from pydantic import BaseModel, ConfigDict, Field

from fiboki.api.deps import AnyPrincipal, AuditDep, SettingsDep
from fiboki.api.errors import ApiError
from fiboki.api.logging import current_correlation_id
from fiboki.api.models import Envelope, Page, SourceNote
from fiboki.api.security import Principal, Role, require_operator
from fiboki.api.workspace_layouts import (
    PANEL_REGISTRY,
    PRESETS,
    LayoutDoc,
    LayoutPreset,
    PanelKindSpec,
)
from fiboki.api.workspace_store import (
    NAME_PATTERN,
    DocumentConflict,
    StoredDocument,
    WorkspaceStore,
)
from fiboki.core import instruments as instrument_registry
from fiboki.core.paths import resolve_paths

router = APIRouter(prefix="/api/workspace", tags=["workspace"])
OperatorPrincipal = Annotated[Principal, Depends(require_operator)]

GRID_ID_PATTERN = r"^[a-z0-9][a-z0-9._-]{0,47}$"


def _note(kind: str, detail: str) -> SourceNote:
    return SourceNote(kind=kind, detail=detail, as_of=datetime.now(tz=UTC))


def workspace_store(settings: Any) -> WorkspaceStore:
    """One path rule (core.paths): the API's state dir, resolved like the CLI's."""
    return WorkspaceStore(resolve_paths({"FIBOKI_STATE_DIR": str(settings.state_dir)}).workspace_db)


def _store_note(store: WorkspaceStore) -> SourceNote:
    return _note(
        "live",
        f"Operator workspace documents at {store.path}: display state chosen by hand, "
        "never read by a strategy, the risk engine or a backtest.",
    )


def _unavailable(exc: Exception, what: str) -> ApiError:
    return ApiError(
        status.HTTP_503_SERVICE_UNAVAILABLE,
        "workspace_unavailable",
        f"The workspace store could not be {what}. Nothing was lost or changed.",
        context={"error_class": type(exc).__name__},
    )


def record_write(
    audit: Any,
    *,
    event: str,
    principal: Principal,
    request: Request,
    settings: Any,
    target: str,
    outcome: str,
    reason: str,
    detail: dict[str, Any],
) -> None:
    audit.record(
        event,
        actor=principal.user_id,
        actor_role=principal.role.value,
        outcome=outcome,
        reason=reason,
        target=target,
        execution_mode=settings.execution_mode.value,
        correlation_id=current_correlation_id(),
        source_ip=request.client.host if request.client else "",
        detail=detail,
    )


def write_document(
    *,
    store: WorkspaceStore,
    kind: str,
    name: str,
    base_version: int,
    payload: dict[str, Any] | None,
    action: str,
    audit: Any,
    principal: Principal,
    request: Request,
    settings: Any,
    label: str,
) -> StoredDocument:
    """One write path for every workspace document: versioned, audited, 409 on stale."""
    event = f"workspace.{kind}.{action}"

    def rec(outcome: str, reason: str, detail: dict[str, Any]) -> None:
        record_write(
            audit, event=event, principal=principal, request=request, settings=settings,
            target=name, outcome=outcome, reason=reason,
            detail={"kind": kind, "name": name, "base_version": base_version, **detail},
        )

    try:
        stored = store.put(
            principal.user_id, kind, name, base_version=base_version, payload=payload,
            actor=principal.user_id, action=action,
        )
    except DocumentConflict as conflict:
        rec("refused", "stale base version", {"current_version": conflict.current.version})
        raise ApiError(
            status.HTTP_409_CONFLICT,
            "workspace_conflict",
            f"{label} {name!r} changed elsewhere (now version {conflict.current.version}, you "
            f"edited version {base_version}). Nothing was overwritten; reload to see the newer one.",
            context={"current_version": conflict.current.version, "base_version": base_version},
        ) from conflict
    except LookupError as exc:
        rec("refused", "does not exist", {})
        raise ApiError(status.HTTP_404_NOT_FOUND, "workspace_not_found", str(exc)) from exc
    except ValueError as exc:
        rec("refused", str(exc), {})
        raise ApiError(status.HTTP_422_UNPROCESSABLE_ENTITY, "invalid_workspace_document", str(exc)) from exc
    except sqlite3.Error as exc:
        rec("failed", type(exc).__name__, {})
        raise _unavailable(exc, "written") from exc
    rec("allowed", action, {"version": stored.version})
    return stored


def check_name(name: str, label: str) -> str:
    import re

    if not re.match(NAME_PATTERN, name):
        raise ApiError(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            "invalid_name",
            f"A {label} name is 1 to 48 letters, digits, spaces, '.', '-' or '_', starting "
            "with a letter or digit.",
        )
    return name


# ================================================================= registry


@router.get("/panels", response_model=Page[PanelKindSpec])
def panels() -> Page[PanelKindSpec]:
    """Every panel kind the workspace can tile."""
    return Page(
        items=list(PANEL_REGISTRY),
        total=len(PANEL_REGISTRY),
        limit=len(PANEL_REGISTRY),
        source=_note("live", "The panel registry in fiboki.api.workspace_layouts (code)."),
    )


@router.get("/presets", response_model=Page[LayoutPreset])
def presets() -> Page[LayoutPreset]:
    """The preset layouts. Code, reviewed in commits; save one under your own name to edit it."""
    return Page(
        items=list(PRESETS),
        total=len(PRESETS),
        limit=len(PRESETS),
        source=_note("live", "Preset layouts in fiboki.api.workspace_layouts (code)."),
    )


# ================================================================= layouts


class LayoutSummary(BaseModel):
    model_config = ConfigDict(frozen=True)

    name: str
    version: int
    updated_at: datetime | None
    updated_by: str
    panel_count: int
    kinds: list[str]


class LayoutView(BaseModel):
    model_config = ConfigDict(frozen=True)

    name: str
    version: int
    updated_at: datetime | None
    updated_by: str
    layout: LayoutDoc
    #: Whether the signed-in role may write (operator or admin). The server
    #: refuses a viewer's write whatever this says.
    can_edit: bool


class LayoutWrite(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    #: The version this layout was edited from (0 creates). Stale: 409.
    base_version: int = Field(ge=0)
    layout: LayoutDoc
    #: ``save`` when the operator saved; ``autosave`` when the workstation did.
    action: Literal["save", "autosave"] = "save"


class LayoutRevisionView(BaseModel):
    model_config = ConfigDict(frozen=True)

    version: int
    action: str
    at: datetime
    actor: str
    panel_count: int
    kinds: list[str]


def _known_symbols(layout: LayoutDoc) -> None:
    for panel in layout.panels:
        if panel.symbol is not None:
            try:
                instrument_registry.get(panel.symbol)
            except KeyError as exc:
                raise ApiError(
                    status.HTTP_422_UNPROCESSABLE_ENTITY,
                    "unknown_symbol",
                    f"Panel {panel.id} names {panel.symbol}, which the platform does not list.",
                ) from exc


def _summary(doc: StoredDocument) -> LayoutSummary:
    layout = LayoutDoc.model_validate(doc.payload)
    return LayoutSummary(
        name=doc.name,
        version=doc.version,
        updated_at=doc.updated_at,
        updated_by=doc.updated_by,
        panel_count=len(layout.panels),
        kinds=sorted({p.kind for p in layout.panels}),
    )


def _view(doc: StoredDocument, principal: Principal) -> LayoutView:
    return LayoutView(
        name=doc.name,
        version=doc.version,
        updated_at=doc.updated_at,
        updated_by=doc.updated_by,
        layout=LayoutDoc.model_validate(doc.payload),
        can_edit=principal.role.satisfies(Role.OPERATOR),
    )


@router.get("/layouts", response_model=Page[LayoutSummary])
def list_layouts(settings: SettingsDep, principal: AnyPrincipal) -> Page[LayoutSummary]:
    """The signed-in operator's saved layouts, by name."""
    store = workspace_store(settings)
    try:
        docs = store.list(principal.user_id, "layout")
    except (sqlite3.Error, ValueError) as exc:
        raise _unavailable(exc, "read") from exc
    items = [_summary(d) for d in docs]
    return Page(items=items, total=len(items), limit=max(len(items), 1), source=_store_note(store))


@router.get("/layouts/{name}", response_model=Envelope[LayoutView])
def get_layout(name: str, settings: SettingsDep, principal: AnyPrincipal) -> Envelope[LayoutView]:
    check_name(name, "layout")
    store = workspace_store(settings)
    try:
        doc = store.get(principal.user_id, "layout", name)
    except (sqlite3.Error, ValueError) as exc:
        raise _unavailable(exc, "read") from exc
    if not doc.exists:
        raise ApiError(
            status.HTTP_404_NOT_FOUND,
            "layout_not_found",
            f"No layout is saved as {name!r}. Start from a preset or the current workspace "
            "and save it under that name.",
            context={"version": doc.version},
        )
    return Envelope(data=_view(doc, principal), source=_store_note(store))


@router.put("/layouts/{name}", response_model=Envelope[LayoutView])
def put_layout(
    name: str,
    body: LayoutWrite,
    request: Request,
    settings: SettingsDep,
    audit: AuditDep,
    principal: OperatorPrincipal,
) -> Envelope[LayoutView]:
    """Save a layout version. Audited; ``base_version`` must be current (409 otherwise)."""
    check_name(name, "layout")
    _known_symbols(body.layout)
    store = workspace_store(settings)
    stored = write_document(
        store=store, kind="layout", name=name, base_version=body.base_version,
        payload=body.layout.model_dump(mode="json", by_alias=True), action=body.action,
        audit=audit, principal=principal, request=request, settings=settings, label="Layout",
    )
    return Envelope(data=_view(stored, principal), source=_store_note(store))


@router.delete("/layouts/{name}", status_code=status.HTTP_204_NO_CONTENT, response_class=Response)
def delete_layout(
    name: str,
    request: Request,
    settings: SettingsDep,
    audit: AuditDep,
    principal: OperatorPrincipal,
    base_version: int = Query(..., ge=1),
) -> Response:
    """Delete a layout (a tombstone version; the history keeps every earlier one)."""
    check_name(name, "layout")
    write_document(
        store=workspace_store(settings), kind="layout", name=name, base_version=base_version,
        payload=None, action="delete", audit=audit, principal=principal, request=request,
        settings=settings, label="Layout",
    )
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/layouts/{name}/history", response_model=Page[LayoutRevisionView])
def layout_history(
    name: str,
    settings: SettingsDep,
    principal: AnyPrincipal,
    limit: int = Query(50, ge=1, le=200),
) -> Page[LayoutRevisionView]:
    """Every accepted write of this layout, newest first: who, when, save or autosave."""
    check_name(name, "layout")
    store = workspace_store(settings)
    try:
        revs = store.revisions(principal.user_id, "layout", name, limit=limit)
    except (sqlite3.Error, ValueError) as exc:
        raise _unavailable(exc, "read") from exc
    items: list[LayoutRevisionView] = []
    for r in revs:
        panels = (r.payload or {}).get("panels") or []
        items.append(
            LayoutRevisionView(
                version=r.version, action=r.action, at=r.at, actor=r.actor,
                panel_count=len(panels), kinds=sorted({str(p.get("kind")) for p in panels}),
            )
        )
    return Page(items=items, total=len(items), limit=limit, source=_store_note(store))


# ============================================================== column sets


class ColumnSet(BaseModel):
    """One saved column arrangement of a grid: order, shown columns and pins."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    name: str = Field(pattern=NAME_PATTERN)
    #: Column ids in display order. Ids the grid no longer has are ignored.
    order: list[str] = Field(default_factory=list, max_length=64)
    hidden: list[str] = Field(default_factory=list, max_length=64)
    pinned: list[str] = Field(default_factory=list, max_length=16)


class ColumnSetsDoc(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    sets: list[ColumnSet] = Field(default_factory=list, max_length=32)
    #: The set applied when the grid opens, if any.
    default: str | None = None


class ColumnSetsView(BaseModel):
    model_config = ConfigDict(frozen=True)

    grid_id: str
    version: int
    updated_at: datetime | None
    sets: list[ColumnSet]
    default: str | None
    can_edit: bool


class ColumnSetsWrite(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    base_version: int = Field(ge=0)
    sets: list[ColumnSet] = Field(default_factory=list, max_length=32)
    default: str | None = None


def _grid_id(grid_id: str) -> str:
    import re

    if not re.match(GRID_ID_PATTERN, grid_id):
        raise ApiError(status.HTTP_422_UNPROCESSABLE_ENTITY, "invalid_grid_id",
                       "A grid id is lower-case letters, digits, '.', '-' or '_'.")
    return grid_id


def _sets_view(doc: StoredDocument, principal: Principal) -> ColumnSetsView:
    parsed = ColumnSetsDoc.model_validate(doc.payload) if doc.exists else ColumnSetsDoc()
    return ColumnSetsView(
        grid_id=doc.name, version=doc.version, updated_at=doc.updated_at, sets=list(parsed.sets),
        default=parsed.default, can_edit=principal.role.satisfies(Role.OPERATOR),
    )


@router.get("/column-sets/{grid_id}", response_model=Envelope[ColumnSetsView])
def get_column_sets(grid_id: str, settings: SettingsDep, principal: AnyPrincipal) -> Envelope[ColumnSetsView]:
    """The operator's saved column sets for one grid (version 0 and none saved yet: empty)."""
    store = workspace_store(settings)
    try:
        doc = store.get(principal.user_id, "column_sets", _grid_id(grid_id))
    except (sqlite3.Error, ValueError) as exc:
        raise _unavailable(exc, "read") from exc
    return Envelope(data=_sets_view(doc, principal), source=_store_note(store))


@router.put("/column-sets/{grid_id}", response_model=Envelope[ColumnSetsView])
def put_column_sets(
    grid_id: str,
    body: ColumnSetsWrite,
    request: Request,
    settings: SettingsDep,
    audit: AuditDep,
    principal: OperatorPrincipal,
) -> Envelope[ColumnSetsView]:
    """Replace the grid's saved sets. Versioned (409 on stale) and audited."""
    names = [s.name for s in body.sets]
    if len(set(names)) != len(names):
        raise ApiError(status.HTTP_422_UNPROCESSABLE_ENTITY, "invalid_column_sets",
                       "Column set names must be unique within a grid.")
    if body.default is not None and body.default not in names:
        raise ApiError(status.HTTP_422_UNPROCESSABLE_ENTITY, "invalid_column_sets",
                       f"The default set {body.default!r} is not one of the saved sets.")
    store = workspace_store(settings)
    stored = write_document(
        store=store, kind="column_sets", name=_grid_id(grid_id), base_version=body.base_version,
        payload=ColumnSetsDoc(sets=body.sets, default=body.default).model_dump(mode="json"),
        action="save", audit=audit, principal=principal, request=request, settings=settings,
        label="Column sets for",
    )
    return Envelope(data=_sets_view(stored, principal), source=_store_note(store))
