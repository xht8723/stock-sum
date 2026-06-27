"""Shared collector protocol."""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from stock_sum.core.context import RuntimeContext
from stock_sum.core.models import RawItem


@runtime_checkable
class Collector(Protocol):
    """Common interface for API, Playwright, and site-specific collectors."""

    collector_id: str

    async def collect(self, context: RuntimeContext) -> list[RawItem]:
        """Collect raw external items for a pipeline run."""
