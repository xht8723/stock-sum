"""Daemon process entrypoint."""

from __future__ import annotations

from stock_sum.api.app import create_app
from stock_sum.api.runtime_config import RuntimeConfigManager
from stock_sum.config.models import AppConfig
from stock_sum.scheduler.service import SchedulerService


def build_daemon(
    config: AppConfig | None = None,
    *,
    config_path: str | None = None,
    env_file: str | None = None,
):
    """Create daemon dependencies."""

    scheduler = SchedulerService(config) if config is not None else None
    if scheduler is not None:
        scheduler.configure_jobs()
    runtime_config = (
        RuntimeConfigManager(config, config_path=config_path, env_file=env_file)
        if config is not None
        else None
    )
    return create_app(config, runtime_config=runtime_config)
