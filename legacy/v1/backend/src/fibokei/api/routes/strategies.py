"""Strategy listing endpoints."""

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from fibokei.api.auth import TokenData, get_current_user
from fibokei.strategies.registry import classify_strategy, strategy_registry

router = APIRouter(tags=["strategies"])


class StrategyResponse(BaseModel):
    id: str
    name: str
    family: str
    complexity: str
    tier: str
    supports_long: bool
    supports_short: bool
    requires_fibonacci: bool
    requires_mtfa: bool


class StrategyDetailResponse(StrategyResponse):
    valid_market_regimes: list[str]
    required_indicators: list[str]


@router.get("/strategies", response_model=list[StrategyResponse])
def list_strategies(user: TokenData = Depends(get_current_user)):
    result = []
    for info in strategy_registry.list_available():
        strategy = strategy_registry.get(info["id"])
        result.append({
            "id": info["id"],
            "name": info["name"],
            "family": info["family"],
            "complexity": info["complexity"],
            "tier": info["tier"],
            "supports_long": strategy.supports_long,
            "supports_short": strategy.supports_short,
            "requires_fibonacci": strategy.requires_fibonacci,
            "requires_mtfa": strategy.requires_mtfa,
        })
    return result


class RegistryHealthResponse(BaseModel):
    registered_count: int
    file_count: int
    canonical_count: int
    experimental_count: int
    traditional_gen1_count: int
    hybrid_gen1_count: int
    tier_counts: dict[str, int]
    expected_min: int
    by_tier: dict[str, list[str]]
    unregistered_files: list[str]
    healthy: bool


@router.get("/strategies/registry-health", response_model=RegistryHealthResponse)
def get_registry_health(user: TokenData = Depends(get_current_user)):
    """Operator truth about the strategy registry: registered vs files on disk,
    per-tier counts, and any unregistered strategy files.

    Defined before ``/strategies/{strategy_id}`` so the literal path is not
    captured by the dynamic id route.
    """
    return strategy_registry.registry_health()


class StrategyGroupResponse(BaseModel):
    tier: str
    label: str
    badge: str
    description: str
    count: int
    strategies: list[StrategyResponse]


@router.get("/strategies/grouped", response_model=list[StrategyGroupResponse])
def list_strategies_grouped(user: TokenData = Depends(get_current_user)):
    """Strategies grouped by tier (canonical → research → experimental) with
    display metadata, for a grouped/searchable strategy picker.

    Defined before ``/strategies/{strategy_id}`` so the literal path is not
    captured by the dynamic id route.
    """
    groups = []
    for group in strategy_registry.list_grouped():
        enriched = []
        for info in group["strategies"]:
            strategy = strategy_registry.get(info["id"])
            enriched.append({
                "id": info["id"],
                "name": info["name"],
                "family": info["family"],
                "complexity": info["complexity"],
                "tier": info["tier"],
                "supports_long": strategy.supports_long,
                "supports_short": strategy.supports_short,
                "requires_fibonacci": strategy.requires_fibonacci,
                "requires_mtfa": strategy.requires_mtfa,
            })
        groups.append({**group, "strategies": enriched})
    return groups


@router.get("/strategies/{strategy_id}", response_model=StrategyDetailResponse)
def get_strategy(strategy_id: str, user: TokenData = Depends(get_current_user)):
    try:
        strategy = strategy_registry.get(strategy_id)
    except KeyError:
        raise HTTPException(status_code=404, detail=f"Strategy not found: {strategy_id}")
    return {
        "id": strategy.strategy_id,
        "name": strategy.strategy_name,
        "family": strategy.strategy_family,
        "complexity": strategy.complexity_level,
        "tier": classify_strategy(strategy.strategy_id),
        "supports_long": strategy.supports_long,
        "supports_short": strategy.supports_short,
        "requires_fibonacci": strategy.requires_fibonacci,
        "requires_mtfa": strategy.requires_mtfa,
        "valid_market_regimes": strategy.valid_market_regimes,
        "required_indicators": strategy.get_required_indicators(),
    }
