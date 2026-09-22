"""Pydantic schemas for chart drawing API endpoints."""

from pydantic import BaseModel, Field


class PointSchema(BaseModel):
    timestamp: int  # Unix ms
    value: float


class DrawingCreate(BaseModel):
    instrument: str = Field(max_length=20)
    timeframe: str = Field(max_length=10)
    tool_type: str = Field(max_length=30)
    points: list[PointSchema]
    styles: dict | None = None
    lock: bool = False
    visible: bool = True


class DrawingUpdate(BaseModel):
    points: list[PointSchema] | None = None
    styles: dict | None = None
    lock: bool | None = None
    visible: bool | None = None


class DrawingResponse(BaseModel):
    id: int
    instrument: str
    timeframe: str
    tool_type: str
    points: list[PointSchema]
    styles: dict | None
    lock: bool
    visible: bool
    created_at: str
    updated_at: str
