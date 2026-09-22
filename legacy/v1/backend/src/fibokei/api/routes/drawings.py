"""Chart drawing CRUD endpoints."""

import json

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from pydantic import BaseModel
from sqlalchemy.orm import Session

from fibokei.api.auth import TokenData, get_current_user
from fibokei.api.deps import get_db
from fibokei.api.schemas.drawings import (
    DrawingCreate,
    DrawingResponse,
    DrawingUpdate,
    PointSchema,
)
from fibokei.db.repository import (
    delete_all_drawings,
    delete_drawing,
    delete_drawing_template,
    get_drawing_templates,
    get_drawings,
    save_drawing,
    save_drawing_template,
    update_drawing,
    update_drawing_template,
)

router = APIRouter(tags=["drawings"])


def _model_to_response(model) -> DrawingResponse:
    """Convert a ChartDrawingModel to a DrawingResponse."""
    points_data = model.points_json
    if isinstance(points_data, str):
        points_data = json.loads(points_data)
    points = [PointSchema(**p) for p in points_data]

    styles_data = model.styles_json
    if isinstance(styles_data, str):
        styles_data = json.loads(styles_data)

    return DrawingResponse(
        id=model.id,
        instrument=model.instrument,
        timeframe=model.timeframe,
        tool_type=model.tool_type,
        points=points,
        styles=styles_data,
        lock=model.lock,
        visible=model.visible,
        created_at=model.created_at.isoformat(),
        updated_at=model.updated_at.isoformat(),
    )


@router.get("/drawings", response_model=list[DrawingResponse])
def list_drawings(
    instrument: str = Query(..., max_length=20),
    timeframe: str = Query(..., max_length=10),
    db: Session = Depends(get_db),
    user: TokenData = Depends(get_current_user),
):
    """List all drawings for the current user on a specific chart."""
    models = get_drawings(db, user.user_id, instrument, timeframe)
    return [_model_to_response(m) for m in models]


@router.post("/drawings", response_model=DrawingResponse, status_code=201)
def create_drawing(
    body: DrawingCreate,
    db: Session = Depends(get_db),
    user: TokenData = Depends(get_current_user),
):
    """Create a new chart drawing."""
    drawing_data = {
        "user_id": user.user_id,
        "instrument": body.instrument,
        "timeframe": body.timeframe,
        "tool_type": body.tool_type,
        "points_json": [p.model_dump() for p in body.points],
        "styles_json": body.styles,
        "lock": body.lock,
        "visible": body.visible,
    }
    model = save_drawing(db, drawing_data)
    return _model_to_response(model)


@router.put("/drawings/{drawing_id}", response_model=DrawingResponse)
def update_drawing_endpoint(
    drawing_id: int,
    body: DrawingUpdate,
    db: Session = Depends(get_db),
    user: TokenData = Depends(get_current_user),
):
    """Update an existing drawing's points, styles, lock, or visibility."""
    updates = {}
    if body.points is not None:
        updates["points_json"] = [p.model_dump() for p in body.points]
    if body.styles is not None:
        updates["styles_json"] = body.styles
    if body.lock is not None:
        updates["lock"] = body.lock
    if body.visible is not None:
        updates["visible"] = body.visible

    if not updates:
        raise HTTPException(status_code=422, detail="No fields to update")

    model = update_drawing(db, drawing_id, user.user_id, updates)
    if model is None:
        raise HTTPException(status_code=404, detail="Drawing not found")
    return _model_to_response(model)


@router.delete("/drawings/{drawing_id}", status_code=204)
def delete_drawing_endpoint(
    drawing_id: int,
    db: Session = Depends(get_db),
    user: TokenData = Depends(get_current_user),
):
    """Delete a single drawing."""
    deleted = delete_drawing(db, drawing_id, user.user_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Drawing not found")
    return Response(status_code=204)


@router.delete("/drawings")
def clear_drawings(
    instrument: str = Query(..., max_length=20),
    timeframe: str = Query(..., max_length=10),
    db: Session = Depends(get_db),
    user: TokenData = Depends(get_current_user),
):
    """Delete all drawings for a specific chart."""
    count = delete_all_drawings(db, user.user_id, instrument, timeframe)
    return {"deleted": count}


# ---------- Drawing templates ----------


class TemplateCreate(BaseModel):
    name: str
    description: str | None = None
    drawings: list[dict]


class TemplateUpdate(BaseModel):
    name: str | None = None
    description: str | None = None
    drawings: list[dict] | None = None


class TemplateResponse(BaseModel):
    id: int
    name: str
    description: str | None = None
    drawings: list[dict]
    created_at: str
    updated_at: str


@router.get("/charts/drawing-templates", response_model=list[TemplateResponse])
def list_templates(
    db: Session = Depends(get_db),
    user: TokenData = Depends(get_current_user),
):
    """List all drawing templates for the current user."""
    templates = get_drawing_templates(db, user.user_id)
    return [
        TemplateResponse(
            id=t.id,
            name=t.name,
            description=t.description,
            drawings=t.drawings_json,
            created_at=t.created_at.isoformat(),
            updated_at=t.updated_at.isoformat(),
        )
        for t in templates
    ]


@router.post("/charts/drawing-templates", response_model=TemplateResponse, status_code=201)
def create_template(
    body: TemplateCreate,
    db: Session = Depends(get_db),
    user: TokenData = Depends(get_current_user),
):
    """Save current drawings as a named template."""
    model = save_drawing_template(db, {
        "user_id": user.user_id,
        "name": body.name,
        "description": body.description,
        "drawings_json": body.drawings,
    })
    return TemplateResponse(
        id=model.id,
        name=model.name,
        description=model.description,
        drawings=model.drawings_json,
        created_at=model.created_at.isoformat(),
        updated_at=model.updated_at.isoformat(),
    )


@router.put("/charts/drawing-templates/{template_id}", response_model=TemplateResponse)
def update_template(
    template_id: int,
    body: TemplateUpdate,
    db: Session = Depends(get_db),
    user: TokenData = Depends(get_current_user),
):
    """Update a drawing template."""
    updates = {}
    if body.name is not None:
        updates["name"] = body.name
    if body.description is not None:
        updates["description"] = body.description
    if body.drawings is not None:
        updates["drawings_json"] = body.drawings
    if not updates:
        raise HTTPException(status_code=422, detail="No fields to update")
    model = update_drawing_template(db, template_id, user.user_id, updates)
    if model is None:
        raise HTTPException(status_code=404, detail="Template not found")
    return TemplateResponse(
        id=model.id,
        name=model.name,
        description=model.description,
        drawings=model.drawings_json,
        created_at=model.created_at.isoformat(),
        updated_at=model.updated_at.isoformat(),
    )


@router.delete("/charts/drawing-templates/{template_id}")
def delete_template(
    template_id: int,
    db: Session = Depends(get_db),
    user: TokenData = Depends(get_current_user),
):
    """Delete a drawing template."""
    deleted = delete_drawing_template(db, template_id, user.user_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Template not found")
    return {"deleted": template_id}
