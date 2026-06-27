"""Config-driven collector factory."""

from __future__ import annotations

from stock_sum.collectors.base import Collector
from stock_sum.collectors.playwright.x import XUserCollector
from stock_sum.config.models import AppConfig, CollectorConfig
from stock_sum.core.errors import ConfigurationError, StockSumError
from stock_sum.storage.mappers import REDDIT_SOURCE_TYPE, X_SOURCE_TYPE


class CollectorNotImplementedError(StockSumError):
    """Raised when a configured collector has no implementation yet."""


def get_collector_config(config: AppConfig, collector_id: str) -> CollectorConfig:
    """Return a configured collector by dotted collector id."""

    try:
        group, name = collector_id.split(".", 1)
    except ValueError as exc:
        raise ConfigurationError(f"Collector id must be dotted, got: {collector_id}") from exc

    try:
        return config.collectors[group][name]
    except KeyError as exc:
        raise ConfigurationError(f"Unknown collector id: {collector_id}") from exc


def source_type_for_collector_id(config: AppConfig, collector_id: str) -> str:
    """Resolve the raw item source type for a configured collector."""

    return source_type_for_collector_config(get_collector_config(config, collector_id))


def source_type_for_collector_config(collector_config: CollectorConfig) -> str:
    """Resolve the raw item source type for a collector config."""

    if collector_config.kind == X_SOURCE_TYPE:
        return X_SOURCE_TYPE
    if collector_config.kind == REDDIT_SOURCE_TYPE:
        return REDDIT_SOURCE_TYPE
    raise ConfigurationError(f"Unsupported collector kind: {collector_config.kind}")


def build_collector(config: AppConfig, collector_id: str) -> Collector:
    """Build a concrete collector from config."""

    collector_config = get_collector_config(config, collector_id)
    if not collector_config.enabled:
        raise ConfigurationError(f"Collector is disabled: {collector_id}")

    if collector_config.kind == X_SOURCE_TYPE:
        return XUserCollector(collector_id, collector_config.handles)

    if collector_config.kind == REDDIT_SOURCE_TYPE:
        raise CollectorNotImplementedError(f"Reddit collector is not implemented yet: {collector_id}")

    raise ConfigurationError(f"Unsupported collector kind: {collector_config.kind}")
