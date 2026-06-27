"""Report pipeline orchestration skeleton."""

from __future__ import annotations

from stock_sum.core.context import RuntimeContext
from stock_sum.core.errors import PipelineNotImplementedError
from stock_sum.core.models import PipelineRun, Report


class ReportPipeline:
    """Coordinates collection, summarization, rendering, delivery, and storage."""

    def __init__(self, context: RuntimeContext) -> None:
        self.context = context

    async def run_report(self, profile: str) -> Report:
        """Run a report profile through the full pipeline."""

        PipelineRun(profile=profile)
        raise PipelineNotImplementedError("Report pipeline is scaffolded only.")
