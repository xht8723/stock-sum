"""Storage repository protocol."""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from stock_sum.core.models import RawItem, Report, Summary


@runtime_checkable
class StorageRepository(Protocol):
    """Persists collected items, summaries, reports, deliveries, and run metadata."""

    async def save_raw_items(self, items: list[RawItem]) -> None:
        """Persist raw collected items."""

    async def save_summaries(self, summaries: list[Summary]) -> None:
        """Persist generated summaries."""

    async def save_report(self, report: Report) -> None:
        """Persist a rendered report."""
