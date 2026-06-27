"""FastAPI route registration."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException

from stock_sum.config.loader import redacted_config
from stock_sum.config.models import AppConfig


def build_router(config: AppConfig | None = None) -> APIRouter:
    """Build API routes."""

    router = APIRouter()

    @router.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    @router.post("/reports/{profile}/run", status_code=202)
    async def run_report(profile: str) -> dict[str, str]:
        if config is not None and profile not in config.reports:
            raise HTTPException(status_code=404, detail=f"Unknown report profile: {profile}")
        return {"status": "accepted", "profile": profile}

    @router.get("/config/effective")
    async def effective_config() -> dict:
        if config is None:
            return {}
        return redacted_config(config)

    return router
