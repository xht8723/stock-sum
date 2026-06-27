"""Collector registry skeleton."""

from __future__ import annotations

from stock_sum.collectors.base import Collector


class CollectorRegistry:
    """Stores collector implementations by id."""

    def __init__(self) -> None:
        self._collectors: dict[str, Collector] = {}

    def register(self, collector: Collector) -> None:
        """Register a collector implementation."""

        self._collectors[collector.collector_id] = collector

    def get(self, collector_id: str) -> Collector:
        """Return a collector by id."""

        return self._collectors[collector_id]
