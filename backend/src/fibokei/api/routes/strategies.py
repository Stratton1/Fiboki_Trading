"""Strategy listing endpoints."""

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from fibokei.api.auth import TokenData, get_current_user
from fibokei.strategies.registry import strategy_registry

router = APIRouter(tags=["strategies"])


class StrategyResponse(BaseModel):
    id: str
    name: str
    family: str
    complexity: str
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
            "supports_long": strategy.supports_long,
            "supports_short": strategy.supports_short,
            "requires_fibonacci": strategy.requires_fibonacci,
            "requires_mtfa": strategy.requires_mtfa,
        })
    return result


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
        "supports_long": strategy.supports_long,
        "supports_short": strategy.supports_short,
        "requires_fibonacci": strategy.requires_fibonacci,
        "requires_mtfa": strategy.requires_mtfa,
        "valid_market_regimes": strategy.valid_market_regimes,
        "required_indicators": strategy.get_required_indicators(),
    }
