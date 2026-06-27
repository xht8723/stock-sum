"""FastAPI application factory."""

from __future__ import annotations

from fastapi import FastAPI

from stock_sum.api.routes import build_router
from stock_sum.config.models import AppConfig


def create_app(config: AppConfig | None = None) -> FastAPI:
    """Create the HTTP application."""

    app = FastAPI(title="stock-sum", version="0.1.0")
    app.include_router(build_router(config))
    return app
