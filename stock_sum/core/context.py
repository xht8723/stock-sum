"""Runtime context passed between pipeline modules."""

from __future__ import annotations

from dataclasses import dataclass

from stock_sum.config.models import AppConfig


@dataclass(frozen=True)
class RuntimeContext:
    """Dependencies and settings for a pipeline execution."""

    config: AppConfig
