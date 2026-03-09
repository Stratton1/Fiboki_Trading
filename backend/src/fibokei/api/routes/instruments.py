"""Instrument listing endpoints."""

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel

from fibokei.api.auth import TokenData, get_current_user
from fibokei.core.instruments import _INSTRUMENT_MAP, INSTRUMENTS

router = APIRouter(tags=["instruments"])


class InstrumentResponse(BaseModel):
    symbol: str
    name: str
    asset_class: str
    has_canonical_data: bool


def _inst_to_dict(inst):
    return {
        "symbol": inst.symbol,
        "name": inst.name,
        "asset_class": inst.asset_class.value,
        "has_canonical_data": inst.has_canonical_data,
    }


@router.get("/instruments", response_model=list[InstrumentResponse])
def list_instruments(
    user: TokenData = Depends(get_current_user),
    asset_class: str | None = Query(None, description="Filter by asset class"),
):
    results = INSTRUMENTS
    if asset_class is not None:
        results = [i for i in results if i.asset_class.value == asset_class]
    return [_inst_to_dict(i) for i in results]


@router.get("/instruments/{symbol}", response_model=InstrumentResponse)
def get_instrument(symbol: str, user: TokenData = Depends(get_current_user)):
    inst = _INSTRUMENT_MAP.get(symbol.upper())
    if inst is None:
        raise HTTPException(status_code=404, detail=f"Instrument not found: {symbol}")
    return _inst_to_dict(inst)
