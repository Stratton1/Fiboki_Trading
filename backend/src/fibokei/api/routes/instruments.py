"""Instrument listing endpoints."""

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from fibokei.api.auth import TokenData, get_current_user
from fibokei.core.instruments import _INSTRUMENT_MAP, INSTRUMENTS

router = APIRouter(tags=["instruments"])


class InstrumentResponse(BaseModel):
    symbol: str
    name: str
    asset_class: str


@router.get("/instruments", response_model=list[InstrumentResponse])
def list_instruments(user: TokenData = Depends(get_current_user)):
    return [
        {
            "symbol": inst.symbol,
            "name": inst.name,
            "asset_class": inst.asset_class.value,
        }
        for inst in INSTRUMENTS
    ]


@router.get("/instruments/{symbol}", response_model=InstrumentResponse)
def get_instrument(symbol: str, user: TokenData = Depends(get_current_user)):
    inst = _INSTRUMENT_MAP.get(symbol.upper())
    if inst is None:
        raise HTTPException(status_code=404, detail=f"Instrument not found: {symbol}")
    return {
        "symbol": inst.symbol,
        "name": inst.name,
        "asset_class": inst.asset_class.value,
    }
