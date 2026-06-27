"""Daemon process entrypoint."""

from __future__ import annotations

from stock_sum.api.app import create_app
from stock_sum.config.models import AppConfig
from stock_sum.scheduler.service import SchedulerService


def build_daemon(config: AppConfig | None = None):
    """Create daemon dependencies."""

    scheduler = SchedulerService(config) if config is not None else None
    if scheduler is not None:
        scheduler.configure_jobs()
    return create_app(config)
