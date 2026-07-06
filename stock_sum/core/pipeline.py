"""Report pipeline orchestration skeleton."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
import inspect
from uuid import uuid4

from stock_sum.collectors.base import Collector
from stock_sum.collectors.factory import build_collector, social_collector_ids, source_type_for_collector_id
from stock_sum.core.context import RuntimeContext
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
        collector_factory: Callable[[str], Collector] | Callable[[str, str], Collector] | Callable[[str, str, str], Collector] | None = None,
    ) -> None:
        self.context = context
        self.repository = repository or SQLiteStorageRepository(context.config.storage.sqlite_path)
        if collector_factory is None:
            self.collector_factory = lambda collector_id, x_method="xpoz", reddit_method="xpoz": build_collector(
                context.config,
                collector_id,
                x_method=x_method,
                reddit_method=reddit_method,
            )
            self._collector_factory_method_count = 3
        else:
            self.collector_factory = collector_factory
            self._collector_factory_method_count = len(inspect.signature(collector_factory).parameters)

    async def collect_collector(
        self,
        collector_id: str,
        *,
        x_method: str = "xpoz",
        reddit_method: str = "xpoz",
        raise_on_error: bool = True,
    ) -> CollectionRunResult:
        """Run one configured collector and persist its raw items."""

        source_type = source_type_for_collector_id(self.context.config, collector_id, x_method=x_method, reddit_method=reddit_method)
        run_id = str(uuid4())
        await self.repository.start_collection_run(
            run_id=run_id,
            collector_id=collector_id,
            source_type=source_type,
        )
        collector: Collector | None = None
        try:
            if self._collector_factory_method_count >= 3:
                collector = self.collector_factory(collector_id, x_method, reddit_method)  # type: ignore[misc]
            elif self._collector_factory_method_count == 2:
                collector = self.collector_factory(collector_id, reddit_method)  # type: ignore[misc]
            else:
                collector = self.collector_factory(collector_id)  # type: ignore[operator]
            set_repository = getattr(collector, "set_repository", None)
            if callable(set_repository):
                set_repository(self.repository)
            items = await collector.collect(self.context)
            collector_warnings = list(getattr(collector, "warnings", []))
            await self._save_provider_api_responses(
                run_id=run_id,
                collector_id=collector_id,
                collector=collector,
            )
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
                warnings=collector_warnings,
            )
        except Exception as exc:
            error = str(exc)
            if collector is not None:
                await self._save_provider_api_responses(
                    run_id=run_id,
                    collector_id=collector_id,
                    collector=collector,
                    suppress_errors=True,
                )
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

    async def _save_provider_api_responses(
        self,
        *,
        run_id: str,
        collector_id: str,
        collector: Collector,
        suppress_errors: bool = False,
    ) -> None:
        responses = list(getattr(collector, "api_responses", []))
        if not responses:
            return
        try:
            await self.repository.save_provider_api_responses(
                collection_run_id=run_id,
                collector_id=collector_id,
                responses=responses,
            )
        except Exception:
            if not suppress_errors:
                raise

    async def collect_sources(
        self,
        *,
        collector_ids: list[str] | None = None,
        scope: str = "social",
        x_method: str = "xpoz",
        reddit_method: str = "xpoz",
    ) -> PipelineCollectionResult:
        """Collect and persist items for the unified source set."""

        active_collector_ids = collector_ids if collector_ids is not None else social_collector_ids(self.context.config)

        runs: list[CollectionRunResult] = []
        warnings: list[PipelineSectionWarning] = []
        semaphore = asyncio.Semaphore(self.context.config.service.collector_concurrency)

        async def run_collector(collector_id: str) -> CollectionRunResult:
            async with semaphore:
                return await self.collect_collector(collector_id, x_method=x_method, reddit_method=reddit_method, raise_on_error=False)

        runs = await asyncio.gather(*(run_collector(collector_id) for collector_id in active_collector_ids))
        for run in runs:
            warnings.extend(run.warnings)
            if run.status == "failed":
                warnings.append(
                    PipelineSectionWarning(
                        section="collector",
                        source_id=run.collector_id,
                        phase="collecting",
                        message=run.error or "Collector failed.",
                    )
                )
        return PipelineCollectionResult(scope=scope, runs=runs, warnings=warnings)
