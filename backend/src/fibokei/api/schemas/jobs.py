"""Pydantic schemas for async job API endpoints."""

from datetime import datetime
from typing import Any

from pydantic import BaseModel


class JobResponse(BaseModel):
    job_id: str
    job_type: str
    label: str
    state: str
    progress: int
    created_at: datetime
    started_at: datetime | None = None
    completed_at: datetime | None = None
    result: dict[str, Any] | None = None
    error: str | None = None


class JobSubmittedResponse(BaseModel):
    job_id: str
    job_type: str
    label: str
    state: str


class JobListResponse(BaseModel):
    items: list[JobResponse]
    active_count: int
