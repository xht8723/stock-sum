"""Default report renderer scaffold."""

from __future__ import annotations

from stock_sum.core.models import Report, Summary
from stock_sum.reports.base import ReportRenderer


class DefaultReportRenderer(ReportRenderer):
    """Default report renderer placeholder."""

    async def render(self, profile: str, summaries: list[Summary]) -> Report:
        """Render a report."""

        raise NotImplementedError("Report rendering is scaffolded only.")
