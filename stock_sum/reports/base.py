"""Report rendering protocol."""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from stock_sum.core.models import Report, Summary


@runtime_checkable
class ReportRenderer(Protocol):
    """Common interface for report renderers."""

    async def render(self, profile: str, summaries: list[Summary]) -> Report:
        """Render summaries into a deliverable report."""
