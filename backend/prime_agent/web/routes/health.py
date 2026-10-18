"""Liveness / readiness probes (no auth)."""

from __future__ import annotations

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from prime_agent.db.session import get_db

router = APIRouter()

# Re-exported so tests can override ``health.get_db`` independently of app deps.
__all__ = ["router", "get_db", "HealthResponse", "ReadyResponse"]


class HealthResponse(BaseModel):
    status: str


class ReadyResponse(BaseModel):
    status: str
    database: str


@router.get("/healthz", response_model=HealthResponse)
@router.get("/health", response_model=HealthResponse)
async def healthz() -> HealthResponse:
    """Process is up."""
    return HealthResponse(status="ok")


@router.get("/readyz", response_model=HealthResponse)
async def readyz() -> HealthResponse:
    """BFF process ready (DB readiness is checked via /ready)."""
    return HealthResponse(status="ok")


@router.get("/ready", response_model=None)
async def ready(db: AsyncSession = Depends(get_db)) -> ReadyResponse | JSONResponse:
    """System readiness — DB only; never depends on AI supervisor."""
    try:
        await db.execute(text("SELECT 1"))
    except Exception:
        return JSONResponse(
            status_code=503,
            content={"status": "not_ready", "database": "disconnected"},
        )
    return ReadyResponse(status="ok", database="connected")
