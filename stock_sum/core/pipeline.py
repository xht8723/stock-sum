"""Report pipeline orchestration skeleton."""

from __future__ import annotations

from collections.abc import Callable
from uuid import uuid4

from stock_sum.collectors.base import Collector
from stock_sum.collectors.factory import build_collector, source_type_for_collector_id
from stock_sum.core.context import RuntimeContext
from stock_sum.core.errors import ConfigurationError
from stock_sum.core.models import CollectionRunResult, PipelineCollectionResult, PipelineSectionWarning
from stock_sum.storage.repository import StorageRepository
from stock_sum.storage.sqlite import SQLiteStorageRepository


class ReportPipeline:
    """Coordinates collection, summarization, rendering, delivery, and storage."""

    def __init__(
        self,
        context: RuntimeContext,
        *,
        repository: StorageRepository | None = None,
        collector_factory: Callable[[str], Collector] | None = None,
    ) -> None:
        self.context = context
        self.repository = repository or SQLiteStorageRepository(context.config.storage.sqlite_path)
        self.collector_factory = collector_factory or (lambda collector_id: build_collector(context.config, collector_id))

    async def collect_collector(
        self,
        collector_id: str,
        *,
        profile: str | None = None,
        raise_on_error: bool = True,
    ) -> CollectionRunResult:
        """Run one configured collector and persist its raw items."""

        source_type = source_type_for_collector_id(self.context.config, collector_id)
        run_id = str(uuid4())
        await self.repository.start_collection_run(
            run_id=run_id,
            profile=profile,
            collector_id=collector_id,
            source_type=source_type,
        )
        try:
            collector = self.collector_factory(collector_id)
            items = await collector.collect(self.context)
            save_result = await self.repository.save_raw_items(items)
            await self.repository.finish_collection_run(
                run_id=run_id,
                status="succeeded",
                source_type=source_type,
                collected_count=save_result.collected_count,
                inserted_count=save_result.inserted_count,
                updated_count=save_result.updated_count,
            )
            return CollectionRunResult(
                run_id=run_id,
                collector_id=collector_id,
                source_type=source_type,
                status="succeeded",
                collected_count=save_result.collected_count,
                inserted_count=save_result.inserted_count,
                updated_count=save_result.updated_count,
                sqlite_path=self.context.config.storage.sqlite_path,
            )
        except Exception as exc:
            error = str(exc)
            await self.repository.finish_collection_run(
                run_id=run_id,
                status="failed",
                source_type=source_type,
                error_text=error,
            )
            result = CollectionRunResult(
                run_id=run_id,
                collector_id=collector_id,
                source_type=source_type,
                status="failed",
                collected_count=0,
                inserted_count=0,
                updated_count=0,
                sqlite_path=self.context.config.storage.sqlite_path,
                error=error,
            )
            if raise_on_error:
                raise
            return result

    async def run_report(self, profile: str) -> PipelineCollectionResult:
        """Run the collection phase for a report profile."""

        try:
            profile_config = self.context.config.reports[profile]
        except KeyError as exc:
            raise ConfigurationError(f"Unknown report profile: {profile}") from exc

        runs: list[CollectionRunResult] = []
        warnings: list[PipelineSectionWarning] = []
        for collector_id in profile_config.collector_ids:
            run = await self.collect_collector(collector_id, profile=profile, raise_on_error=False)
            runs.append(run)
            if run.status == "failed":
                warnings.append(
                    PipelineSectionWarning(
                        section="collector",
                        source_id=collector_id,
                        phase="collecting",
                        message=run.error or "Collector failed.",
                    )
                )
        return PipelineCollectionResult(profile=profile, runs=runs, warnings=warnings)
