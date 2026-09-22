"""Strategy variant management API endpoints."""

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from fibokei.api.auth import TokenData, get_current_user
from fibokei.api.deps import get_db
from fibokei.db.repository import (
    create_variant,
    delete_variant,
    get_variant,
    list_variants,
    update_variant,
)

router = APIRouter(tags=["variations"])


class VariantResponse(BaseModel):
    id: int
    strategy_id: str
    name: str
    params: dict
    is_active: bool
    backtest_run_id: int | None
    trade_overlap: float | None
    created_at: str | None


class VariantCreate(BaseModel):
    strategy_id: str
    name: str
    params: dict = Field(default_factory=dict)


class VariantListResponse(BaseModel):
    items: list[VariantResponse]
    total: int


class ParamRangesResponse(BaseModel):
    strategy_id: str
    params: dict[str, list[float]]
    constructor_params: dict[str, str]


def _variant_to_response(v) -> VariantResponse:
    return VariantResponse(
        id=v.id,
        strategy_id=v.strategy_id,
        name=v.name,
        params=v.params or {},
        is_active=v.is_active,
        backtest_run_id=v.backtest_run_id,
        trade_overlap=v.trade_overlap,
        created_at=v.created_at.isoformat() if v.created_at else None,
    )


@router.get("/variations", response_model=VariantListResponse)
def list_strategy_variants(
    strategy_id: str | None = Query(None),
    active_only: bool = Query(False),
    db: Session = Depends(get_db),
    _user: TokenData = Depends(get_current_user),
):
    """List strategy variants."""
    variants = list_variants(db, strategy_id=strategy_id, active_only=active_only)
    return VariantListResponse(
        items=[_variant_to_response(v) for v in variants],
        total=len(variants),
    )


@router.get("/variations/{variant_id}", response_model=VariantResponse)
def get_strategy_variant(
    variant_id: int,
    db: Session = Depends(get_db),
    _user: TokenData = Depends(get_current_user),
):
    """Get a single variant by ID."""
    v = get_variant(db, variant_id)
    if not v:
        raise HTTPException(status_code=404, detail="Variant not found")
    return _variant_to_response(v)


@router.post("/variations", response_model=VariantResponse, status_code=201)
def create_strategy_variant(
    body: VariantCreate,
    db: Session = Depends(get_db),
    _user: TokenData = Depends(get_current_user),
):
    """Create a new strategy variant with custom parameters."""
    from fibokei.strategies.registry import strategy_registry

    # Verify strategy exists
    try:
        strategy_registry.get(body.strategy_id)
    except KeyError:
        raise HTTPException(status_code=404, detail=f"Strategy '{body.strategy_id}' not found")

    # Check for duplicate name
    existing = list_variants(db, strategy_id=body.strategy_id)
    if any(v.name == body.name for v in existing):
        raise HTTPException(status_code=409, detail=f"Variant name '{body.name}' already exists")

    v = create_variant(db, body.strategy_id, body.name, body.params)
    return _variant_to_response(v)


@router.patch("/variations/{variant_id}", response_model=VariantResponse)
def update_strategy_variant(
    variant_id: int,
    body: dict,
    db: Session = Depends(get_db),
    _user: TokenData = Depends(get_current_user),
):
    """Update a variant's fields (name, is_active, params)."""
    allowed = {"name", "is_active", "params", "backtest_run_id", "trade_overlap"}
    updates = {k: v for k, v in body.items() if k in allowed}
    if not updates:
        raise HTTPException(status_code=400, detail="No valid fields to update")
    v = update_variant(db, variant_id, updates)
    if not v:
        raise HTTPException(status_code=404, detail="Variant not found")
    return _variant_to_response(v)


@router.delete("/variations/{variant_id}")
def delete_strategy_variant(
    variant_id: int,
    db: Session = Depends(get_db),
    _user: TokenData = Depends(get_current_user),
):
    """Delete a variant."""
    if not delete_variant(db, variant_id):
        raise HTTPException(status_code=404, detail="Variant not found")
    return {"deleted": variant_id}


@router.get("/variations/params/{strategy_id}", response_model=ParamRangesResponse)
def get_variant_param_ranges(
    strategy_id: str,
    _user: TokenData = Depends(get_current_user),
):
    """Get available parameter ranges for a strategy."""
    from fibokei.research.variation import get_param_ranges, get_strategy_params
    from fibokei.strategies.registry import strategy_registry

    try:
        strategy_registry.get(strategy_id)
    except KeyError:
        raise HTTPException(status_code=404, detail=f"Strategy '{strategy_id}' not found")

    ranges = get_param_ranges(strategy_id)
    constructor_params = {k: v.__name__ for k, v in get_strategy_params(strategy_id).items()}

    return ParamRangesResponse(
        strategy_id=strategy_id,
        params=ranges,
        constructor_params=constructor_params,
    )


@router.post("/variations/generate")
def generate_strategy_variants(
    body: dict,
    db: Session = Depends(get_db),
    _user: TokenData = Depends(get_current_user),
):
    """Generate parameter variants for a strategy (dry-run preview)."""
    from fibokei.research.variation import generate_variants

    strategy_id = body.get("strategy_id")
    if not strategy_id:
        raise HTTPException(status_code=400, detail="strategy_id is required")

    param_overrides = body.get("param_overrides")
    max_variants = body.get("max_variants", 20)

    combos = generate_variants(strategy_id, param_overrides, max_variants)
    return {
        "strategy_id": strategy_id,
        "variants": combos,
        "count": len(combos),
    }
