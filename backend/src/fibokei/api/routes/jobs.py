"""Async job status API endpoints."""

from fastapi import APIRouter, Depends, HTTPException, Query

from fibokei.api.auth import TokenData, get_current_user
from fibokei.api.schemas.jobs import JobListResponse, JobResponse
from fibokei.jobs.engine import JobState, get_job_engine

router = APIRouter(tags=["jobs"])


def _job_to_dict(info) -> dict:
    """Convert a JobInfo to a response dict."""
    return {
        "job_id": info.job_id,
        "job_type": info.job_type,
        "label": info.label,
        "state": info.state.value,
        "progress": info.progress,
        "created_at": info.created_at,
        "started_at": info.started_at,
        "completed_at": info.completed_at,
        "result": info.result,
        "error": info.error,
    }


@router.get("/jobs", response_model=JobListResponse)
def list_jobs(
    job_type: str | None = None,
    state: str | None = None,
    limit: int = Query(50, ge=1, le=200),
    user: TokenData = Depends(get_current_user),
):
    """List all jobs with optional filters."""
    engine = get_job_engine()

    state_enum = None
    if state:
        try:
            state_enum = JobState(state)
        except ValueError:
            raise HTTPException(status_code=400, detail=f"Invalid state: {state}")

    jobs = engine.list_jobs(job_type=job_type, state=state_enum, limit=limit)
    return {
        "items": [_job_to_dict(j) for j in jobs],
        "active_count": engine.active_count(),
    }


@router.get("/jobs/{job_id}", response_model=JobResponse)
def get_job(
    job_id: str,
    user: TokenData = Depends(get_current_user),
):
    """Get a single job's status and result."""
    engine = get_job_engine()
    info = engine.get(job_id)
    if not info:
        raise HTTPException(status_code=404, detail="Job not found")
    return _job_to_dict(info)


@router.post("/jobs/{job_id}/cancel")
def cancel_job(
    job_id: str,
    user: TokenData = Depends(get_current_user),
):
    """Cancel a running or pending job."""
    engine = get_job_engine()
    cancelled = engine.cancel(job_id)
    if not cancelled:
        info = engine.get(job_id)
        if not info:
            raise HTTPException(status_code=404, detail="Job not found")
        raise HTTPException(
            status_code=400,
            detail=f"Cannot cancel job in state: {info.state.value}",
        )
    return {"job_id": job_id, "state": "cancelled"}
