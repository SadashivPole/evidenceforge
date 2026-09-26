"""Health and dependency readiness endpoints."""

from typing import Literal

from fastapi import APIRouter, HTTPException, Request, status
from pydantic import BaseModel

from app.db import is_database_ready

router = APIRouter(tags=["health"])


class HealthResponse(BaseModel):
    status: Literal["ok"]


class ReadinessResponse(BaseModel):
    status: Literal["ok"]
    database: Literal["ok"]


@router.get("/health/live", response_model=HealthResponse)
def liveness() -> HealthResponse:
    """Report that the API process is running without checking dependencies."""

    return HealthResponse(status="ok")


@router.get("/health/ready", response_model=ReadinessResponse)
def readiness(request: Request) -> ReadinessResponse:
    """Report readiness only when the configured database is reachable."""

    if not is_database_ready(request.app.state.engine):
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={"status": "unavailable", "database": "unavailable"},
        )
    return ReadinessResponse(status="ok", database="ok")
