"""FastAPI application factory."""

from __future__ import annotations

from fastapi import FastAPI

from stock_sum.api.jobs import HttpJobManager
from stock_sum.api.runtime_config import RuntimeConfigManager
from stock_sum.api.routes import build_router
from stock_sum.config.models import AppConfig


def create_app(
    config: AppConfig | None = None,
    *,
    job_manager: HttpJobManager | None = None,
    runtime_config: RuntimeConfigManager | None = None,
) -> FastAPI:
    """Create the HTTP application."""

    app = FastAPI(title="stock-sum", version="0.1.0")
    app.include_router(build_router(config, job_manager=job_manager, runtime_config=runtime_config))
    return app
