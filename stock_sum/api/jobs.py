"""HTTP job orchestration for automation clients."""

from __future__ import annotations

from dataclasses import asdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable
from uuid import uuid4
import asyncio
import hashlib
import json
import os
import shutil
import sys
import time as monotonic_time

from stock_sum.config.models import AppConfig
from stock_sum.collectors.api.sec_13f import SEC_13F_COLLECTOR_ID
from stock_sum.collectors.factory import social_collector_ids
from stock_sum.api.job_models import (
    HttpJobRecord,
    JobKind,
    JobStatus,
    ReportMode,
    Sec13FReportJobOptions,
    SocialReportJobOptions,
    StatisticBucket,
    StatisticJobOptions,
    StatisticMode,
    TradingReportJobOptions,
    TrendingsReportJobOptions,
    WorkerOperation,
    _InFlightReport,
)
from stock_sum.api.job_serialization import (
    _adanos_sector_rows_to_dicts,
    _adanos_stock_rows_to_dicts,
    _adanos_trending_change_dicts,
    _analysis_response_data,
    _house_ptr_rows_to_dicts,
    _jsonable,
    _no_social_data_message,
    _pipeline_result_to_dict,
    _safe_warning_list,
    _sec_13f_rows_to_dicts,
    _sort_house_ptr_rows,
    _summary_input_has_social_data,
    _warnings_to_dicts,
    _worker_error_detail,
)
from stock_sum.api.job_store import (
    _job_record_from_dict,
    _job_sort_datetime,
    _parse_utc_datetime,
    _utc_now,
)
from stock_sum.api.job_validation import (
    _parse_date_filter,
    _sec_13f_filter_data,
    _statistic_date_window,
    _statistic_filter_data,
    _trading_date_window,
    _trading_filing_date_window,
    _trading_filter_data,
    _trendings_date_window,
    _validate_13f_filters,
    _validate_statistic_filters,
    _validate_trading_filters,
    _validate_trendings_filters,
)
from stock_sum.llm.analysis import PROMPT_VERSION


class HttpJobManager:
    """Runs stock-sum jobs for the local HTTP API."""

    def __init__(
        self,
        config: AppConfig,
        *,
        pipeline_factory: Callable[[], ReportPipeline] | None = None,
        repository_factory: Callable[[], SQLiteStorageRepository] | None = None,
        llm_client_factory: Callable[[], Any] | None = None,
        renderer_factory: Callable[[str], PresentationRenderer] | None = None,
        retention_service_factory: Callable[[], DataRetentionService] | None = None,
        use_subprocess_workers: bool | None = None,
        recover_stale_jobs: bool = True,
    ) -> None:
        self.config = config
        self.artifact_dir = Path(config.server.artifact_dir)
        self._jobs: dict[str, HttpJobRecord] = {}
        self._inflight_reports: dict[str, _InFlightReport] = {}
        self._inflight_lock = asyncio.Lock()
        self._repository_factory = repository_factory or self._default_repository_factory
        self._pipeline_factory = pipeline_factory or self._default_pipeline_factory
        self._llm_client_factory = llm_client_factory or self._default_llm_client_factory
        self._renderer_factory = renderer_factory or self._default_renderer_factory
        self._retention_service_factory = retention_service_factory or self._default_retention_service_factory
        self._use_subprocess_workers = (
            pipeline_factory is None
            and repository_factory is None
            and llm_client_factory is None
            and renderer_factory is None
            and retention_service_factory is None
            if use_subprocess_workers is None
            else use_subprocess_workers
        )
        if recover_stale_jobs:
            self._mark_stale_running_jobs_failed()

    def _default_repository_factory(self):
        from stock_sum.storage.sqlite import SQLiteStorageRepository

        return SQLiteStorageRepository(self.config.storage.sqlite_path)

    def _default_pipeline_factory(self):
        from stock_sum.core.context import RuntimeContext
        from stock_sum.core.pipeline import ReportPipeline

        return ReportPipeline(RuntimeContext(config=self.config), repository=self._repository_factory())

    def _default_llm_client_factory(self):
        from stock_sum.llm.registry import build_llm_client

        return build_llm_client(self.config.llm)

    def _default_renderer_factory(self, title: str):
        from stock_sum.reports.renderer import PresentationRenderer

        return PresentationRenderer(title=title)

    def _default_retention_service_factory(self):
        from stock_sum.retention import DataRetentionService

        return DataRetentionService(self.config)

    def create_social_report_job(self, options: SocialReportJobOptions) -> HttpJobRecord:
        """Create a queued social-media report job."""

        record = self._new_job(
            kind="social_report",
            scope="social",
            mode=options.mode,
            cache_key=self._social_report_cache_key(options),
        )
        self._save(record)
        self._refresh_memory_status(record.job_id)
        return record

    def create_trading_report_job(self, options: TradingReportJobOptions) -> HttpJobRecord:
        """Create a queued House PTR trading disclosure report job."""

        _validate_trading_filters(options)
        record = self._new_job(
            kind="trading_report",
            scope="trading",
            mode=options.mode,
            cache_key=self._artifact_job_cache_key("trading_report", options),
        )
        self._save(record)
        self._refresh_memory_status(record.job_id)
        return record

    def create_13f_report_job(self, options: Sec13FReportJobOptions) -> HttpJobRecord:
        """Create a queued SEC 13F holdings report job."""

        _validate_13f_filters(options)
        record = self._new_job(
            kind="13f_report",
            scope="13f",
            mode=options.mode,
            cache_key=self._artifact_job_cache_key("13f_report", options),
        )
        self._save(record)
        self._refresh_memory_status(record.job_id)
        return record

    def create_trendings_report_job(self, options: TrendingsReportJobOptions) -> HttpJobRecord:
        """Create a queued Adanos trendings report job."""

        _validate_trendings_filters(options)
        record = self._new_job(
            kind="trendings_report",
            scope="trendings",
            mode=options.mode,
            cache_key=self._artifact_job_cache_key("trendings_report", options),
        )
        self._save(record)
        self._refresh_memory_status(record.job_id)
        return record

    def create_statistic_job(self, options: StatisticJobOptions) -> HttpJobRecord:
        """Create a queued statistic PNG job."""

        _validate_statistic_filters(options)
        record = self._new_job(
            kind="statistic",
            scope=options.mode,
            mode=options.mode,
            cache_key=self._artifact_job_cache_key("statistic", options),
        )
        self._save(record)
        self._refresh_memory_status(record.job_id)
        return record

    async def statistic_fuzzy_matches(
        self,
        *,
        mode: StatisticMode,
        query: str,
        limit: int = 5,
    ) -> list[dict[str, Any]]:
        """Return fuzzy statistic candidates from SQLite."""

        normalized_query = query.strip()
        if mode not in {"social", "trading"}:
            raise ValueError("Statistic fuzzy mode must be social or trading.")
        if not normalized_query:
            raise ValueError("Statistic fuzzy search query is required.")
        bounded_limit = max(1, min(5, limit))
        repository = self._repository_factory()
        if mode == "social":
            matches = await repository.search_social_statistic_tags(
                query=normalized_query,
                limit=bounded_limit,
            )
        else:
            matches = await repository.search_trading_statistic_assets(
                query=normalized_query,
                limit=bounded_limit,
            )
        return [asdict(match) for match in matches]

    def create_collect_job(self) -> HttpJobRecord:
        """Create a queued collection-only job."""

        record = self._new_job(kind="collect", scope="social", mode="json")
        self._save(record)
        self._refresh_memory_status(record.job_id)
        return record

    def get_job(self, job_id: str) -> HttpJobRecord | None:
        """Return a known in-memory or persisted job record."""

        if job_id in self._jobs:
            if self._jobs[job_id].status in {"queued", "running"}:
                reloaded = self._load_job_from_disk(job_id)
                if reloaded is not None:
                    return reloaded
            return self._jobs[job_id]
        record = self._load_job_from_disk(job_id)
        if record is not None:
            self._refresh_memory_status(job_id)
        return record

    def _load_job_from_disk(self, job_id: str) -> HttpJobRecord | None:
        path = self._job_dir(job_id) / "status.json"
        if not path.exists():
            return None
        data = json.loads(path.read_text(encoding="utf-8"))
        record = _job_record_from_dict(data)
        self._jobs[job_id] = record
        return record

    def memory_status(self, *, evicted_in_memory_jobs: int | None = None) -> dict[str, int]:
        """Return memory-bound job cache counters for status payloads."""

        data = {
            "in_memory_jobs": len(self._jobs),
            "inflight_reports": len(self._inflight_reports),
            "max_in_memory_jobs": self.config.server.max_in_memory_jobs,
        }
        if evicted_in_memory_jobs is not None:
            data["evicted_in_memory_jobs"] = evicted_in_memory_jobs
        return data

    async def run_social_report_job(self, job_id: str, options: SocialReportJobOptions) -> None:
        """Run a social report job in a child worker unless test factories request in-process execution."""

        if not self._use_subprocess_workers:
            await self._run_social_report_job_in_process(job_id, options)
            return

        is_inflight_leader = False
        cache_key: str | None = None
        try:
            self._mark_running(job_id, phase="cache_lookup")
            job = self._require_job(job_id)
            cache_key = job.cache_key or self._social_report_cache_key(options)
            self._update(job_id, cache_key=cache_key)
            cache_hit = self._find_report_cache_hit(job_id, cache_key, kind="social_report")
            if cache_hit is not None:
                await self._run_worker_operation(
                    job_id,
                    "http_render_cached_artifact_job",
                    {"kind": "social_report", "options": asdict(options), "cache_hit_job_id": cache_hit.job_id},
                )
                return

            is_inflight_leader, inflight_report = await self._join_or_register_inflight_report(job_id, cache_key)
            if not is_inflight_leader:
                await self._wait_for_coalesced_report_worker(job_id, inflight_report, options)
                return

            await self._run_worker_operation(job_id, "http_social_report", {"options": asdict(options)})
        except Exception as exc:
            self._mark_failed(job_id, str(exc))
        finally:
            if is_inflight_leader and cache_key is not None:
                await self._release_inflight_report(cache_key, job_id)
            self._refresh_memory_status(job_id)

    async def _run_social_report_job_in_process(self, job_id: str, options: SocialReportJobOptions) -> None:
        """Run social collection, payload assembly, LLM summarization, and rendering."""

        from stock_sum.llm.analysis import LLMAnalysisService
        from stock_sum.media.downloader import MediaDownloader
        from stock_sum.reports.summary_input import SummaryInputBuilder

        is_inflight_leader = False
        cache_key: str | None = None
        try:
            self._mark_running(job_id, phase="cache_lookup")
            job = self._require_job(job_id)
            cache_key = job.cache_key or self._social_report_cache_key(options)
            self._update(job_id, cache_key=cache_key)
            cache_hit = self._find_report_cache_hit(job_id, cache_key, kind="social_report")
            if cache_hit is not None:
                self._write_cached_artifact_job_artifacts(job_id, cache_hit, options)
                return

            is_inflight_leader, inflight_report = await self._join_or_register_inflight_report(job_id, cache_key)
            if not is_inflight_leader:
                await self._wait_for_coalesced_report(job_id, inflight_report, options)
                return

            self._update(job_id, phase="collecting")
            collection_result = await self._pipeline_factory().collect_sources(
                collector_ids=social_collector_ids(self.config),
                scope="social",
                x_method=options.x_method,
                reddit_method=options.reddit_method,
            )
            warnings = list(collection_result.warnings)
            warning_data = _warnings_to_dicts(warnings)
            self._update(
                job_id,
                phase="building_payload",
                collection_result=_pipeline_result_to_dict(collection_result),
                warnings=warning_data,
            )

            repository = self._repository_factory()
            downloader = MediaDownloader(self.config.media, repository) if options.download_images else None
            builder = SummaryInputBuilder(config=self.config, repository=repository, downloader=downloader)
            summary_input = await builder.build(download_images=options.download_images)
            has_social_data = _summary_input_has_social_data(summary_input)
            if not has_social_data:
                raise RuntimeError(_no_social_data_message(collection_result))
            payload_data = summary_input.to_dict(
                mode="compact",
                max_images_per_post=options.max_images_per_post,
                max_images_total=options.max_images_total,
            )

            self._update(job_id, phase="analyzing")
            analysis = await LLMAnalysisService(
                config=self.config,
                repository=repository,
                llm_client=self._llm_client_factory(),
            ).analyze(
                summary_input,
                instructions=options.instructions,
                max_images_per_post=options.max_images_per_post,
                max_images_total=options.max_images_total,
            )
            warnings.extend(analysis.warnings)
            response_data = _analysis_response_data(
                provider=self.config.llm.provider,
                analysis=analysis,
                input_media=payload_data.get("media", {}) if isinstance(payload_data, dict) else {},
            )

            warning_data = _warnings_to_dicts(warnings)
            response_data["pipeline_warnings"] = warning_data
            response_data["failed_sections"] = warning_data
            self._update(job_id, warnings=warning_data)

            summary_path = self._job_dir(job_id) / "summary.json"
            self._write_json(summary_path, response_data)

            self._update(job_id, phase="rendering", summary_path=str(summary_path))
            artifact_path, media_type = self._write_artifact(job_id, response_data, options)
            self._mark_succeeded(
                job_id,
                artifact_path=str(artifact_path),
                artifact_media_type=media_type,
                summary_path=str(summary_path),
                warnings=warning_data,
                cache_key=cache_key,
            )
        except Exception as exc:
            self._mark_failed(job_id, str(exc))
        finally:
            if is_inflight_leader and cache_key is not None:
                await self._release_inflight_report(cache_key, job_id)
            await self._run_retention(job_id)
            self._refresh_memory_status(job_id)

    async def run_trading_report_job(self, job_id: str, options: TradingReportJobOptions) -> None:
        """Run a House PTR trading report in a child worker unless configured otherwise."""

        if self._use_subprocess_workers:
            await self._run_artifact_job_worker(
                job_id,
                kind="trading_report",
                options=options,
                worker_operation="http_trading_report",
            )
            return
        await self._run_trading_report_job_in_process(job_id, options)

    async def _run_trading_report_job_in_process(self, job_id: str, options: TradingReportJobOptions) -> None:
        """Run a House PTR trading disclosure report without LLM analysis."""

        from stock_sum.core.models import PipelineCollectionResult, PipelineSectionWarning

        is_inflight_leader = False
        cache_key: str | None = None
        try:
            _validate_trading_filters(options)
            self._mark_running(job_id, phase="cache_lookup")
            job = self._require_job(job_id)
            cache_key = job.cache_key or self._artifact_job_cache_key("trading_report", options)
            self._update(job_id, cache_key=cache_key)
            if not self._bypass_completed_cache("trading_report", options):
                cache_hit = self._find_report_cache_hit(job_id, cache_key, kind="trading_report")
                if cache_hit is not None:
                    self._write_cached_artifact_job_artifacts(job_id, cache_hit, options)
                    return
            is_inflight_leader, inflight_report = await self._join_or_register_inflight_report(job_id, cache_key)
            if not is_inflight_leader:
                await self._wait_for_coalesced_report(job_id, inflight_report, options)
                return

            self._mark_running(job_id, phase="refresh_check")
            repository = self._repository_factory()
            warnings: list[PipelineSectionWarning] = []
            collection_result: PipelineCollectionResult | None = None

            if self.config.sources.house_ptr.enabled:
                if options.force_refresh or await self._house_ptr_refresh_needed(repository):
                    self._update(job_id, phase="refreshing_house_ptr")
                    run = await self._pipeline_factory().collect_collector(
                        "house.ptr",
                        raise_on_error=False,
                    )
                    collection_result = PipelineCollectionResult(scope="trading", runs=[run], warnings=list(run.warnings))
                    warnings.extend(run.warnings)
                    if run.status == "failed":
                        warnings.append(
                            PipelineSectionWarning(
                                section="house_ptr",
                                source_id="house.ptr",
                                phase="refreshing",
                                message=run.error or "House PTR refresh failed.",
                            )
                        )
            else:
                warnings.append(
                    PipelineSectionWarning(
                        section="house_ptr",
                        source_id="house.ptr",
                        phase="refreshing",
                        message="House PTR source is disabled; using existing SQLite data only.",
                    )
                )

            self._update(job_id, phase="querying")
            transaction_start, transaction_end = _trading_date_window(options)
            filing_start, filing_end = _trading_filing_date_window(options)
            order_by_filing_date = filing_start is not None or filing_end is not None
            rows = await repository.read_house_ptr_trades(
                name_contains=options.name,
                transaction_start=transaction_start,
                transaction_end=transaction_end,
                filing_start=filing_start,
                filing_end=filing_end,
                asset_type=options.asset_type,
                ticker=options.ticker,
                limit=options.limit,
                order_by_filing_date=order_by_filing_date,
            )
            rows = _sort_house_ptr_rows(rows, prefer_filing_date=order_by_filing_date)
            if not rows:
                message = "No House PTR trade rows matched the trading report filters."
                if warnings:
                    message += " Refresh warnings: " + "; ".join(warning.message for warning in warnings)
                raise RuntimeError(message)

            warning_data = _warnings_to_dicts(warnings)
            response_data = {
                "report_type": "trading",
                "summary": {"house_ptr": _house_ptr_rows_to_dicts(rows)},
                "house_ptr": _house_ptr_rows_to_dicts(rows),
                "filters": _trading_filter_data(options, transaction_start, transaction_end, filing_start, filing_end),
                "pipeline_warnings": warning_data,
                "failed_sections": warning_data,
            }
            summary_path = self._job_dir(job_id) / "summary.json"
            self._write_json(summary_path, response_data)
            self._update(
                job_id,
                phase="rendering",
                summary_path=str(summary_path),
                warnings=warning_data,
                collection_result=_pipeline_result_to_dict(collection_result) if collection_result else None,
                cache_key=cache_key,
            )
            artifact_path, media_type = self._write_trading_artifact(job_id, response_data, options)
            self._mark_succeeded(
                job_id,
                artifact_path=str(artifact_path),
                artifact_media_type=media_type,
                summary_path=str(summary_path),
                warnings=warning_data,
                collection_result=_pipeline_result_to_dict(collection_result) if collection_result else None,
                cache_key=cache_key,
            )
        except Exception as exc:
            self._mark_failed(job_id, str(exc))
        finally:
            if is_inflight_leader and cache_key is not None:
                await self._release_inflight_report(cache_key, job_id)
            await self._run_retention(job_id)
            self._refresh_memory_status(job_id)

    async def run_13f_report_job(self, job_id: str, options: Sec13FReportJobOptions) -> None:
        """Run an SEC 13F report in a child worker unless configured otherwise."""

        if self._use_subprocess_workers:
            await self._run_artifact_job_worker(
                job_id,
                kind="13f_report",
                options=options,
                worker_operation="http_13f_report",
            )
            return
        await self._run_13f_report_job_in_process(job_id, options)

    async def _run_13f_report_job_in_process(self, job_id: str, options: Sec13FReportJobOptions) -> None:
        """Run an SEC 13F holdings report without LLM analysis."""

        from stock_sum.core.models import PipelineCollectionResult, PipelineSectionWarning

        is_inflight_leader = False
        cache_key: str | None = None
        try:
            _validate_13f_filters(options)
            self._mark_running(job_id, phase="cache_lookup")
            job = self._require_job(job_id)
            cache_key = job.cache_key or self._artifact_job_cache_key("13f_report", options)
            self._update(job_id, cache_key=cache_key)
            if not self._bypass_completed_cache("13f_report", options):
                cache_hit = self._find_report_cache_hit(job_id, cache_key, kind="13f_report")
                if cache_hit is not None:
                    self._write_cached_artifact_job_artifacts(job_id, cache_hit, options)
                    return
            is_inflight_leader, inflight_report = await self._join_or_register_inflight_report(job_id, cache_key)
            if not is_inflight_leader:
                await self._wait_for_coalesced_report(job_id, inflight_report, options)
                return

            self._mark_running(job_id, phase="refresh_check")
            repository = self._repository_factory()
            warnings: list[PipelineSectionWarning] = []
            collection_result: PipelineCollectionResult | None = None

            if self.config.sources.sec_13f.enabled:
                if options.force_refresh or await self._sec_13f_refresh_needed(repository):
                    self._update(job_id, phase="refreshing_sec_13f")
                    run = await self._pipeline_factory().collect_collector(
                        SEC_13F_COLLECTOR_ID,
                        raise_on_error=False,
                    )
                    collection_result = PipelineCollectionResult(scope="13f", runs=[run], warnings=list(run.warnings))
                    warnings.extend(run.warnings)
                    if run.status == "failed":
                        warnings.append(
                            PipelineSectionWarning(
                                section="sec_13f",
                                source_id=SEC_13F_COLLECTOR_ID,
                                phase="refreshing",
                                message=run.error or "SEC 13F refresh failed.",
                            )
                        )
            else:
                warnings.append(
                    PipelineSectionWarning(
                        section="sec_13f",
                        source_id=SEC_13F_COLLECTOR_ID,
                        phase="refreshing",
                        message="SEC 13F source is disabled; using existing SQLite data only.",
                    )
                )

            self._update(job_id, phase="querying")
            period_start = _parse_date_filter(options.period_start, end_of_day=False)
            period_end = _parse_date_filter(options.period_end, end_of_day=True)
            filing_start = _parse_date_filter(options.filing_start, end_of_day=False)
            filing_end = _parse_date_filter(options.filing_end, end_of_day=True)
            rows = await repository.read_sec_13f_holdings(
                manager=options.manager,
                cik=options.cik,
                accession_number=options.accession_number,
                issuer=options.issuer,
                cusip=options.cusip,
                figi=options.figi,
                put_call=options.put_call,
                period_start=period_start,
                period_end=period_end,
                filing_start=filing_start,
                filing_end=filing_end,
                min_value=options.min_value,
                min_shares=options.min_shares,
                limit=options.limit,
            )
            if not rows:
                message = "No SEC 13F holdings matched the report filters."
                if warnings:
                    message += " Refresh warnings: " + "; ".join(warning.message for warning in warnings)
                raise RuntimeError(message)

            warning_data = _warnings_to_dicts(warnings)
            response_data = {
                "report_type": "sec_13f",
                "summary": {"sec_13f": _sec_13f_rows_to_dicts(rows)},
                "sec_13f": _sec_13f_rows_to_dicts(rows),
                "filters": _sec_13f_filter_data(options, period_start, period_end, filing_start, filing_end),
                "pipeline_warnings": warning_data,
                "failed_sections": warning_data,
            }
            summary_path = self._job_dir(job_id) / "summary.json"
            self._write_json(summary_path, response_data)
            self._update(
                job_id,
                phase="rendering",
                summary_path=str(summary_path),
                warnings=warning_data,
                collection_result=_pipeline_result_to_dict(collection_result) if collection_result else None,
                cache_key=cache_key,
            )
            artifact_path, media_type = self._write_13f_artifact(job_id, response_data, options)
            self._mark_succeeded(
                job_id,
                artifact_path=str(artifact_path),
                artifact_media_type=media_type,
                summary_path=str(summary_path),
                warnings=warning_data,
                collection_result=_pipeline_result_to_dict(collection_result) if collection_result else None,
                cache_key=cache_key,
            )
        except Exception as exc:
            self._mark_failed(job_id, str(exc))
        finally:
            if is_inflight_leader and cache_key is not None:
                await self._release_inflight_report(cache_key, job_id)
            await self._run_retention(job_id)
            self._refresh_memory_status(job_id)

    async def run_trendings_report_job(self, job_id: str, options: TrendingsReportJobOptions) -> None:
        """Run an Adanos trendings report in a child worker unless configured otherwise."""

        if self._use_subprocess_workers:
            await self._run_artifact_job_worker(
                job_id,
                kind="trendings_report",
                options=options,
                worker_operation="http_trendings_report",
            )
            return
        await self._run_trendings_report_job_in_process(job_id, options)

    async def _run_trendings_report_job_in_process(self, job_id: str, options: TrendingsReportJobOptions) -> None:
        """Fetch, persist, and render Adanos trendings."""

        from stock_sum.collectors.api.adanos import AdanosClient
        from stock_sum.core.models import PipelineSectionWarning

        is_inflight_leader = False
        cache_key: str | None = None
        try:
            _validate_trendings_filters(options)
            from_date, to_date = _trendings_date_window(options)
            self._mark_running(job_id, phase="cache_lookup")
            job = self._require_job(job_id)
            cache_key = job.cache_key or self._artifact_job_cache_key("trendings_report", options)
            self._update(job_id, cache_key=cache_key)
            cache_hit = self._find_report_cache_hit(job_id, cache_key, kind="trendings_report")
            if cache_hit is not None:
                self._write_cached_artifact_job_artifacts(job_id, cache_hit, options)
                return
            is_inflight_leader, inflight_report = await self._join_or_register_inflight_report(job_id, cache_key)
            if not is_inflight_leader:
                await self._wait_for_coalesced_report(job_id, inflight_report, options)
                return

            self._mark_running(job_id, phase="querying_adanos")
            repository = self._repository_factory()
            result = await AdanosClient(self.config.providers.adanos).fetch_trendings(
                from_date=from_date,
                to_date=to_date,
            )
            warnings = [
                PipelineSectionWarning(
                    section="trendings",
                    source_id="adanos",
                    phase="querying",
                    message=message,
                )
                for message in result.warnings
            ]
            if result.responses:
                await repository.save_adanos_trendings(
                    job_id=job_id,
                    from_date=from_date.isoformat(),
                    to_date=to_date.isoformat(),
                    responses=result.responses,
                )

            self._update(job_id, phase="rendering")
            stocks = await repository.read_adanos_trending_stocks(job_id=job_id)
            sectors = await repository.read_adanos_trending_sectors(job_id=job_id)
            comparison_cutoff = datetime.now(timezone.utc) - timedelta(days=options.comparison_days)
            has_trending_history = await repository.has_prior_adanos_trending_stock_history(
                exclude_job_id=job_id,
                since_fetched_at=comparison_cutoff.isoformat(),
            )
            prior_stocks = await repository.read_latest_prior_adanos_trending_stocks(
                exclude_job_id=job_id,
                tickers=[row.ticker for row in stocks],
                since_fetched_at=comparison_cutoff.isoformat(),
            )
            changes = _adanos_trending_change_dicts(
                stocks,
                prior_stocks,
                has_history=has_trending_history,
                mentions_change_pct=options.mentions_change_pct,
                sentiment_change_pct=options.sentiment_change_pct,
                minimum_mentions=options.minimum_mentions,
            )
            warning_data = _warnings_to_dicts(warnings)
            response_data = {
                "report_type": "trendings",
                "summary": {
                    "stocks": _adanos_stock_rows_to_dicts(stocks),
                    "sectors": _adanos_sector_rows_to_dicts(sectors),
                    "changes": changes,
                },
                "trendings": {
                    "stocks": _adanos_stock_rows_to_dicts(stocks),
                    "sectors": _adanos_sector_rows_to_dicts(sectors),
                    "changes": changes,
                },
                "filters": {
                    "from": from_date.isoformat(),
                    "to": to_date.isoformat(),
                    "display_limit": options.limit,
                    "fetch_limit": 100,
                    "days": options.days,
                    "comparison_days": options.comparison_days,
                    "mentions_change_pct": options.mentions_change_pct,
                    "sentiment_change_pct": options.sentiment_change_pct,
                    "minimum_mentions": options.minimum_mentions,
                },
                "skipped": result.skipped,
                "skip_reason": "ADANOS_API_KEY is not configured." if result.skipped else None,
                "pipeline_warnings": warning_data,
                "failed_sections": warning_data,
            }
            summary_path = self._job_dir(job_id) / "summary.json"
            self._write_json(summary_path, response_data)
            self._update(job_id, summary_path=str(summary_path), warnings=warning_data)
            artifact_path, media_type = self._write_trendings_artifact(job_id, response_data, options)
            self._mark_succeeded(
                job_id,
                artifact_path=str(artifact_path),
                artifact_media_type=media_type,
                summary_path=str(summary_path),
                warnings=warning_data,
                cache_key=cache_key,
            )
        except Exception as exc:
            self._mark_failed(job_id, str(exc))
        finally:
            if is_inflight_leader and cache_key is not None:
                await self._release_inflight_report(cache_key, job_id)
            await self._run_retention(job_id)
            self._refresh_memory_status(job_id)

    async def run_statistic_job(self, job_id: str, options: StatisticJobOptions) -> None:
        """Run a statistic PNG job in a child worker unless configured otherwise."""

        if self._use_subprocess_workers:
            await self._run_artifact_job_worker(
                job_id,
                kind="statistic",
                options=options,
                worker_operation="http_statistic",
            )
            return
        await self._run_statistic_job_in_process(job_id, options)

    async def _run_statistic_job_in_process(self, job_id: str, options: StatisticJobOptions) -> None:
        """Query SQLite statistic rows and render a PNG artifact."""

        from stock_sum.statistics import (
            build_social_statistic_summary,
            build_trading_statistic_summary,
            render_statistic_png,
        )

        is_inflight_leader = False
        cache_key: str | None = None
        try:
            _validate_statistic_filters(options)
            self._mark_running(job_id, phase="cache_lookup")
            job = self._require_job(job_id)
            cache_key = job.cache_key or self._artifact_job_cache_key("statistic", options)
            self._update(job_id, cache_key=cache_key)
            cache_hit = self._find_report_cache_hit(job_id, cache_key, kind="statistic")
            if cache_hit is not None:
                self._write_cached_artifact_job_artifacts(job_id, cache_hit, options)
                return
            is_inflight_leader, inflight_report = await self._join_or_register_inflight_report(job_id, cache_key)
            if not is_inflight_leader:
                await self._wait_for_coalesced_report(job_id, inflight_report, options)
                return

            self._mark_running(job_id, phase="querying")
            repository = self._repository_factory()
            start_at, end_at = _statistic_date_window(options)
            filter_data = _statistic_filter_data(options, start_at, end_at)
            if options.mode == "social":
                points = await repository.read_social_statistic_points(
                    ticker=options.ticker,
                    fuzzy_tag=options.fuzzy_tag,
                    source=options.source,
                    sentiment=None if options.sentiment == "all" else options.sentiment,
                    posted_start=start_at,
                    posted_end=end_at,
                )
                if not points:
                    raise RuntimeError("No analyzed social posts matched the statistic filters.")
                response_data = build_social_statistic_summary(
                    points,
                    filters=filter_data,
                    bucket=options.bucket,
                    title=options.title,
                )
            else:
                points = await repository.read_trading_statistic_points(
                    name_contains=options.name,
                    asset_name=options.asset_name,
                    transaction_start=start_at,
                    transaction_end=end_at,
                    asset_type=options.asset_type,
                    ticker=options.ticker,
                    action=None if options.action == "all" else options.action,
                )
                if not points:
                    raise RuntimeError("No House PTR trades matched the statistic filters.")
                response_data = build_trading_statistic_summary(
                    points,
                    filters=filter_data,
                    bucket=options.bucket,
                    title=options.title,
                )
                if not response_data.get("buckets"):
                    raise RuntimeError("No House PTR trades with usable actions matched the statistic filters.")

            summary_path = self._job_dir(job_id) / "summary.json"
            self._write_json(summary_path, response_data)
            self._update(job_id, phase="rendering", summary_path=str(summary_path))
            artifact_path = self._job_dir(job_id) / "statistic.png"
            render_statistic_png(response_data, artifact_path)
            self._mark_succeeded(
                job_id,
                artifact_path=str(artifact_path),
                artifact_media_type="image/png",
                summary_path=str(summary_path),
                cache_key=cache_key,
            )
        except Exception as exc:
            self._mark_failed(job_id, str(exc))
        finally:
            if is_inflight_leader and cache_key is not None:
                await self._release_inflight_report(cache_key, job_id)
            await self._run_retention(job_id)
            self._refresh_memory_status(job_id)

    async def run_collect_job(self, job_id: str) -> None:
        """Run a collection job in a child worker unless configured otherwise."""

        if self._use_subprocess_workers:
            await self._run_worker_operation(job_id, "http_collect", {})
            self._refresh_memory_status(job_id)
            return
        await self._run_collect_job_in_process(job_id)

    async def _run_collect_job_in_process(self, job_id: str) -> None:
        """Run collection-only job and persist its JSON artifact."""

        try:
            self._mark_running(job_id, phase="collecting")
            collection_result = await self._pipeline_factory().collect_sources(scope="social")
            result_data = _pipeline_result_to_dict(collection_result)
            artifact_path = self._job_dir(job_id) / "collection.json"
            self._write_json(artifact_path, result_data)
            self._mark_succeeded(
                job_id,
                artifact_path=str(artifact_path),
                artifact_media_type="application/json",
                summary_path=None,
                collection_result=result_data,
            )
        except Exception as exc:
            self._mark_failed(job_id, str(exc))
        finally:
            await self._run_retention(job_id)
            self._refresh_memory_status(job_id)

    async def _house_ptr_refresh_needed(self, repository: SQLiteStorageRepository) -> bool:
        ttl_seconds = self.config.sources.house_ptr.refresh_ttl_seconds
        if ttl_seconds <= 0:
            return True
        runs = await repository.list_collection_runs(limit=20)
        now = datetime.now(timezone.utc)
        for run in runs:
            if run.collector_id != "house.ptr" or run.status != "succeeded":
                continue
            finished_at = _parse_utc_datetime(run.finished_at)
            if finished_at is None:
                continue
            if (now - finished_at).total_seconds() <= ttl_seconds:
                return False
        return True

    async def _sec_13f_refresh_needed(self, repository: SQLiteStorageRepository) -> bool:
        ttl_seconds = self.config.sources.sec_13f.refresh_ttl_seconds
        if ttl_seconds <= 0:
            return True
        runs = await repository.list_collection_runs(limit=20)
        now = datetime.now(timezone.utc)
        for run in runs:
            if run.collector_id != SEC_13F_COLLECTOR_ID or run.status != "succeeded":
                continue
            finished_at = _parse_utc_datetime(run.finished_at)
            if finished_at is None:
                continue
            if (now - finished_at).total_seconds() <= ttl_seconds:
                return False
        return True

    def _new_job(self, *, kind: JobKind, scope: str, mode: str, cache_key: str | None = None) -> HttpJobRecord:
        now = _utc_now()
        job_id = uuid4().hex
        record = HttpJobRecord(
            job_id=job_id,
            kind=kind,
            scope=scope,
            status="queued",
            phase="queued",
            created_at=now,
            updated_at=now,
            mode=mode,
            cache_key=cache_key,
        )
        self._jobs[job_id] = record
        return record

    def _require_job(self, job_id: str) -> HttpJobRecord:
        job = self.get_job(job_id)
        if job is None:
            raise KeyError(f"Unknown job: {job_id}")
        return job

    def _mark_running(self, job_id: str, *, phase: str) -> None:
        self._update(job_id, status="running", phase=phase, started_at=_utc_now())

    def _mark_succeeded(
        self,
        job_id: str,
        *,
        artifact_path: str,
        artifact_media_type: str,
        summary_path: str | None,
        collection_result: dict[str, Any] | None = None,
        warnings: list[dict[str, Any]] | None = None,
        cache_key: str | None = None,
        cache_hit: bool = False,
        cached_from_job_id: str | None = None,
        cache_age_seconds: int | None = None,
        coalesced_from_job_id: str | None = None,
        coalesced_wait_seconds: int | None = None,
    ) -> None:
        changes: dict[str, Any] = {
            "status": "succeeded",
            "phase": "succeeded",
            "finished_at": _utc_now(),
            "artifact_path": artifact_path,
            "artifact_media_type": artifact_media_type,
            "summary_path": summary_path,
            "collection_result": collection_result,
            "cache_key": cache_key,
            "cache_hit": cache_hit,
            "cached_from_job_id": cached_from_job_id,
            "cache_age_seconds": cache_age_seconds,
            "coalesced_from_job_id": coalesced_from_job_id,
            "coalesced_wait_seconds": coalesced_wait_seconds,
        }
        if warnings is not None:
            changes["warnings"] = warnings
        self._update(job_id, **changes)

    def _mark_failed(self, job_id: str, error: str) -> None:
        self._update(job_id, status="failed", phase="failed", finished_at=_utc_now(), error=error)

    async def _run_retention(self, job_id: str) -> None:
        if not self.config.retention.prune_after_pipeline:
            return
        try:
            summary = await self._retention_service_factory().prune(
                protected_paths=[self._job_dir(job_id)],
            )
            cleanup_result = summary.to_dict()
            cleanup_result.update(self.memory_status(evicted_in_memory_jobs=0))
            self._update(job_id, cleanup_result=cleanup_result)
        except Exception as exc:
            self._update(
                job_id,
                cleanup_result={
                    "enabled": self.config.retention.enabled,
                    "dry_run": False,
                    "max_total_bytes": self.config.retention.max_total_bytes,
                    "bytes_before": 0,
                    "bytes_after": 0,
                    "bytes_deleted": 0,
                    "http_job_dirs_deleted": 0,
                    "media_files_deleted": 0,
                    "sqlite_rows_deleted": 0,
                    "errors": [str(exc)],
                    "over_limit": False,
                    **self.memory_status(evicted_in_memory_jobs=0),
                },
            )

    async def _run_worker_operation(self, job_id: str, operation: WorkerOperation, payload: dict[str, Any]) -> None:
        request_path = self._write_worker_request(job_id, operation, payload)
        started_at = _utc_now()
        started = monotonic_time.monotonic()
        process = await asyncio.create_subprocess_exec(
            sys.executable,
            "-m",
            "stock_sum.worker",
            "--request",
            str(request_path),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        self._update(
            job_id,
            worker_pid=process.pid,
            worker_started_at=started_at,
            worker_finished_at=None,
            worker_exit_code=None,
            worker_runtime_seconds=None,
            worker_mode="subprocess",
        )
        stdout, stderr = await process.communicate()
        runtime_seconds = round(monotonic_time.monotonic() - started, 3)
        self._load_job_from_disk(job_id)
        self._update(
            job_id,
            worker_pid=process.pid,
            worker_started_at=started_at,
            worker_finished_at=_utc_now(),
            worker_exit_code=process.returncode,
            worker_runtime_seconds=runtime_seconds,
            worker_mode="subprocess",
        )
        if process.returncode != 0:
            job = self.get_job(job_id)
            if job is None or job.status not in {"succeeded", "failed"}:
                detail = _worker_error_detail(stdout, stderr)
                self._mark_failed(job_id, detail or f"Worker exited with code {process.returncode}.")

    async def _run_artifact_job_worker(
        self,
        job_id: str,
        *,
        kind: JobKind,
        options: SocialReportJobOptions | TradingReportJobOptions | Sec13FReportJobOptions | TrendingsReportJobOptions | StatisticJobOptions,
        worker_operation: WorkerOperation,
    ) -> None:
        is_inflight_leader = False
        cache_key: str | None = None
        try:
            self._mark_running(job_id, phase="cache_lookup")
            job = self._require_job(job_id)
            cache_key = job.cache_key or self._artifact_job_cache_key(kind, options)
            self._update(job_id, cache_key=cache_key)
            if not self._bypass_completed_cache(kind, options):
                cache_hit = self._find_report_cache_hit(job_id, cache_key, kind=kind)
                if cache_hit is not None:
                    await self._run_worker_operation(
                        job_id,
                        "http_render_cached_artifact_job",
                        {"kind": kind, "options": asdict(options), "cache_hit_job_id": cache_hit.job_id},
                    )
                    return

            is_inflight_leader, inflight_report = await self._join_or_register_inflight_report(job_id, cache_key)
            if not is_inflight_leader:
                await self._wait_for_coalesced_report_worker(job_id, inflight_report, options)
                return

            await self._run_worker_operation(job_id, worker_operation, {"options": asdict(options)})
        except Exception as exc:
            self._mark_failed(job_id, str(exc))
        finally:
            if is_inflight_leader and cache_key is not None:
                await self._release_inflight_report(cache_key, job_id)
            self._refresh_memory_status(job_id)

    def _write_worker_request(self, job_id: str, operation: WorkerOperation, payload: dict[str, Any]) -> Path:
        request_path = self._job_dir(job_id) / "worker-request.json"
        data = {
            "schema_version": 1,
            "operation": operation,
            "job_id": job_id,
            "config": self.config.model_dump(mode="json"),
            "payload": payload,
        }
        self._write_json(request_path, data)
        return request_path

    async def _wait_for_coalesced_report_worker(
        self,
        job_id: str,
        report: _InFlightReport | None,
        options: SocialReportJobOptions | TradingReportJobOptions | Sec13FReportJobOptions | TrendingsReportJobOptions | StatisticJobOptions,
    ) -> None:
        if report is None:
            raise RuntimeError("No in-flight report was available to coalesce.")
        wait_started = datetime.now(timezone.utc)
        self._update(job_id, phase="waiting_for_inflight", coalesced_from_job_id=report.leader_job_id)
        await report.done.wait()
        wait_seconds = max(0, int((datetime.now(timezone.utc) - wait_started).total_seconds()))
        self._update(job_id, coalesced_wait_seconds=wait_seconds)
        leader = self.get_job(report.leader_job_id)
        if leader is None:
            raise RuntimeError(f"Coalesced report leader disappeared: {report.leader_job_id}")
        if leader.status != "succeeded":
            detail = f": {leader.error}" if leader.error else "."
            raise RuntimeError(f"Coalesced report leader {leader.job_id} failed{detail}")
        if not leader.summary_path or not Path(leader.summary_path).exists():
            raise RuntimeError(f"Coalesced report leader {leader.job_id} did not produce a summary.")
        await self._run_worker_operation(
            job_id,
            "http_render_coalesced_artifact_job",
            {"kind": leader.kind, "options": asdict(options), "leader_job_id": leader.job_id, "wait_seconds": wait_seconds},
        )

    def _mark_stale_running_jobs_failed(self) -> int:
        count = 0
        for status_path in self.artifact_dir.glob("*/status.json"):
            try:
                data = json.loads(status_path.read_text(encoding="utf-8"))
                record = _job_record_from_dict(data)
            except (OSError, TypeError, ValueError):
                continue
            if record.status not in {"queued", "running"}:
                continue
            record.status = "failed"
            record.phase = "failed"
            record.error = "Job was interrupted by daemon restart before completion."
            record.finished_at = _utc_now()
            record.updated_at = record.finished_at
            self._save(record)
            count += 1
        return count

    def _update(self, job_id: str, **changes: Any) -> HttpJobRecord:
        job = self._require_job(job_id)
        for key, value in changes.items():
            if value is not None or hasattr(job, key):
                setattr(job, key, value)
        job.updated_at = _utc_now()
        self._save(job)
        return job

    def _write_artifact(
        self,
        job_id: str,
        response_data: dict[str, Any],
        options: SocialReportJobOptions,
    ) -> tuple[Path, str]:
        if options.mode == "json":
            artifact_path = self._job_dir(job_id) / "summary.json"
            return artifact_path, "application/json"

        extension = {"html": "html", "markdown": "md", "discord": "md", "text": "txt"}[options.mode]
        media_type = {
            "html": "text/html; charset=utf-8",
            "markdown": "text/markdown; charset=utf-8",
            "discord": "text/markdown; charset=utf-8",
            "text": "text/plain; charset=utf-8",
        }[options.mode]
        rendered = self._renderer_factory(options.title).render(response_data, mode=options.mode, detail=options.detail)
        artifact_path = self._job_dir(job_id) / f"report.{extension}"
        artifact_path.write_text(rendered, encoding="utf-8")
        return artifact_path, media_type

    def _write_trading_artifact(
        self,
        job_id: str,
        response_data: dict[str, Any],
        options: TradingReportJobOptions,
    ) -> tuple[Path, str]:
        if options.mode == "json":
            artifact_path = self._job_dir(job_id) / "summary.json"
            return artifact_path, "application/json"

        extension = {"html": "html", "markdown": "md", "discord": "md", "text": "txt"}[options.mode]
        media_type = {
            "html": "text/html; charset=utf-8",
            "markdown": "text/markdown; charset=utf-8",
            "discord": "text/markdown; charset=utf-8",
            "text": "text/plain; charset=utf-8",
        }[options.mode]
        rendered = self._renderer_factory(options.title).render_trading(response_data, mode=options.mode)
        artifact_path = self._job_dir(job_id) / f"trading-report.{extension}"
        artifact_path.write_text(rendered, encoding="utf-8")
        return artifact_path, media_type

    def _write_13f_artifact(
        self,
        job_id: str,
        response_data: dict[str, Any],
        options: Sec13FReportJobOptions,
    ) -> tuple[Path, str]:
        if options.mode == "json":
            artifact_path = self._job_dir(job_id) / "summary.json"
            return artifact_path, "application/json"

        extension = {"html": "html", "markdown": "md", "discord": "md", "text": "txt"}[options.mode]
        media_type = {
            "html": "text/html; charset=utf-8",
            "markdown": "text/markdown; charset=utf-8",
            "discord": "text/markdown; charset=utf-8",
            "text": "text/plain; charset=utf-8",
        }[options.mode]
        rendered = self._renderer_factory(options.title).render_13f(response_data, mode=options.mode)
        artifact_path = self._job_dir(job_id) / f"13f-report.{extension}"
        artifact_path.write_text(rendered, encoding="utf-8")
        return artifact_path, media_type

    def _write_trendings_artifact(
        self,
        job_id: str,
        response_data: dict[str, Any],
        options: TrendingsReportJobOptions,
    ) -> tuple[Path, str]:
        if options.mode == "json":
            artifact_path = self._job_dir(job_id) / "summary.json"
            return artifact_path, "application/json"

        extension = {"html": "html", "markdown": "md", "discord": "md", "text": "txt"}[options.mode]
        media_type = {
            "html": "text/html; charset=utf-8",
            "markdown": "text/markdown; charset=utf-8",
            "discord": "text/markdown; charset=utf-8",
            "text": "text/plain; charset=utf-8",
        }[options.mode]
        rendered = self._renderer_factory(options.title).render_trendings(
            response_data,
            mode=options.mode,
            limit=options.limit,
        )
        artifact_path = self._job_dir(job_id) / f"trendings-report.{extension}"
        artifact_path.write_text(rendered, encoding="utf-8")
        return artifact_path, media_type

    def _write_artifact_for_kind(
        self,
        job_id: str,
        kind: JobKind,
        response_data: dict[str, Any],
        options: SocialReportJobOptions | TradingReportJobOptions | Sec13FReportJobOptions | TrendingsReportJobOptions | StatisticJobOptions,
        *,
        source_job: HttpJobRecord | None = None,
    ) -> tuple[Path, str]:
        if kind == "social_report":
            if not isinstance(options, SocialReportJobOptions):
                raise TypeError("Social report cache render received incompatible options.")
            return self._write_artifact(job_id, response_data, options)
        if kind == "trading_report":
            if not isinstance(options, TradingReportJobOptions):
                raise TypeError("Trading report cache render received incompatible options.")
            return self._write_trading_artifact(job_id, response_data, options)
        if kind == "13f_report":
            if not isinstance(options, Sec13FReportJobOptions):
                raise TypeError("13F report cache render received incompatible options.")
            return self._write_13f_artifact(job_id, response_data, options)
        if kind == "trendings_report":
            if not isinstance(options, TrendingsReportJobOptions):
                raise TypeError("Trendings report cache render received incompatible options.")
            return self._write_trendings_artifact(job_id, response_data, options)
        if kind == "statistic":
            if not isinstance(options, StatisticJobOptions):
                raise TypeError("Statistic cache render received incompatible options.")
            if source_job is None or not source_job.artifact_path:
                raise RuntimeError("Cached statistic job did not produce an artifact.")
            source_path = Path(source_job.artifact_path)
            if not source_path.exists():
                raise RuntimeError(f"Cached statistic artifact is missing: {source_path}")
            artifact_path = self._job_dir(job_id) / "statistic.png"
            shutil.copyfile(source_path, artifact_path)
            return artifact_path, "image/png"
        raise RuntimeError(f"Job kind is not cacheable as an artifact: {kind}")

    def _write_cached_artifact_job_artifacts(
        self,
        job_id: str,
        cache_hit: HttpJobRecord,
        options: SocialReportJobOptions | TradingReportJobOptions | Sec13FReportJobOptions | TrendingsReportJobOptions | StatisticJobOptions,
    ) -> None:
        summary_path = Path(cache_hit.summary_path or "")
        response_data = self._read_json(summary_path)
        warning_data = _safe_warning_list(response_data.get("pipeline_warnings") or response_data.get("failed_sections"))
        current_summary_path = self._job_dir(job_id) / "summary.json"
        self._write_json(current_summary_path, response_data)
        self._update(job_id, phase="rendering", summary_path=str(current_summary_path), warnings=warning_data)
        artifact_path, media_type = self._write_artifact_for_kind(job_id, cache_hit.kind, response_data, options, source_job=cache_hit)
        self._mark_succeeded(
            job_id,
            artifact_path=str(artifact_path),
            artifact_media_type=media_type,
            summary_path=str(current_summary_path),
            warnings=warning_data,
            cache_key=cache_hit.cache_key,
            cache_hit=True,
            cached_from_job_id=cache_hit.job_id,
            cache_age_seconds=cache_hit.cache_age_seconds,
        )

    async def _join_or_register_inflight_report(
        self,
        job_id: str,
        cache_key: str | None,
    ) -> tuple[bool, _InFlightReport | None]:
        if not self.config.server.coalesce_inflight_reports or not cache_key:
            return True, None

        async with self._inflight_lock:
            existing = self._inflight_reports.get(cache_key)
            if existing is not None and existing.leader_job_id != job_id:
                return False, existing

            report = _InFlightReport(
                cache_key=cache_key,
                leader_job_id=job_id,
                started_at=datetime.now(timezone.utc),
                done=asyncio.Event(),
            )
            self._inflight_reports[cache_key] = report
            return True, report

    async def _release_inflight_report(self, cache_key: str, job_id: str) -> None:
        async with self._inflight_lock:
            report = self._inflight_reports.get(cache_key)
            if report is None or report.leader_job_id != job_id:
                return
            report.done.set()
            del self._inflight_reports[cache_key]

    async def _wait_for_coalesced_report(
        self,
        job_id: str,
        report: _InFlightReport | None,
        options: SocialReportJobOptions,
    ) -> None:
        if report is None:
            raise RuntimeError("No in-flight report was available to coalesce.")

        wait_started = datetime.now(timezone.utc)
        self._update(job_id, phase="waiting_for_inflight", coalesced_from_job_id=report.leader_job_id)
        await report.done.wait()
        wait_seconds = max(0, int((datetime.now(timezone.utc) - wait_started).total_seconds()))
        self._update(job_id, coalesced_wait_seconds=wait_seconds)

        leader = self.get_job(report.leader_job_id)
        if leader is None:
            raise RuntimeError(f"Coalesced report leader disappeared: {report.leader_job_id}")
        if leader.status != "succeeded":
            detail = f": {leader.error}" if leader.error else "."
            raise RuntimeError(f"Coalesced report leader {leader.job_id} failed{detail}")
        if not leader.summary_path or not Path(leader.summary_path).exists():
            raise RuntimeError(f"Coalesced report leader {leader.job_id} did not produce a summary.")

        self._write_coalesced_report_artifacts(
            job_id=job_id,
            leader=leader,
            options=options,
            wait_seconds=wait_seconds,
        )

    def _write_coalesced_report_artifacts(
        self,
        *,
        job_id: str,
        leader: HttpJobRecord,
        options: SocialReportJobOptions | TradingReportJobOptions | Sec13FReportJobOptions | TrendingsReportJobOptions | StatisticJobOptions,
        wait_seconds: int,
    ) -> None:
        response_data = self._read_json(Path(leader.summary_path or ""))
        warning_data = _safe_warning_list(response_data.get("pipeline_warnings") or response_data.get("failed_sections"))
        current_summary_path = self._job_dir(job_id) / "summary.json"
        self._write_json(current_summary_path, response_data)
        self._update(
            job_id,
            phase="rendering",
            summary_path=str(current_summary_path),
            warnings=warning_data,
            coalesced_from_job_id=leader.job_id,
            coalesced_wait_seconds=wait_seconds,
        )
        artifact_path, media_type = self._write_artifact_for_kind(job_id, leader.kind, response_data, options, source_job=leader)
        self._mark_succeeded(
            job_id,
            artifact_path=str(artifact_path),
            artifact_media_type=media_type,
            summary_path=str(current_summary_path),
            warnings=warning_data,
            cache_key=leader.cache_key,
            coalesced_from_job_id=leader.job_id,
            coalesced_wait_seconds=wait_seconds,
        )

    def _find_report_cache_hit(self, current_job_id: str, cache_key: str | None, *, kind: JobKind = "social_report") -> HttpJobRecord | None:
        ttl_seconds = self.config.server.report_cache_ttl_seconds
        if ttl_seconds <= 0 or not cache_key:
            return None

        now = datetime.now(timezone.utc)
        candidates: list[tuple[datetime, HttpJobRecord]] = []
        for status_path in self.artifact_dir.glob("*/status.json"):
            try:
                data = json.loads(status_path.read_text(encoding="utf-8"))
                record = _job_record_from_dict(data)
            except (OSError, TypeError, ValueError):
                continue
            if record.job_id == current_job_id:
                continue
            if record.kind != kind or record.status != "succeeded" or record.cache_key != cache_key:
                continue
            if not record.summary_path or not Path(record.summary_path).exists():
                continue
            if kind == "statistic" and (not record.artifact_path or not Path(record.artifact_path).exists()):
                continue
            finished_at = _parse_utc_datetime(record.finished_at)
            if finished_at is None:
                continue
            age_seconds = max(0, int((now - finished_at).total_seconds()))
            if age_seconds > ttl_seconds:
                continue
            record.cache_age_seconds = age_seconds
            candidates.append((finished_at, record))

        if not candidates:
            return None
        return max(candidates, key=lambda item: item[0])[1]

    def _artifact_job_cache_key(
        self,
        kind: JobKind,
        options: SocialReportJobOptions | TradingReportJobOptions | Sec13FReportJobOptions | TrendingsReportJobOptions | StatisticJobOptions,
    ) -> str:
        if kind == "social_report":
            if not isinstance(options, SocialReportJobOptions):
                raise TypeError("Social report cache key received incompatible options.")
            return self._social_report_cache_key(options)
        if kind == "trading_report":
            if not isinstance(options, TradingReportJobOptions):
                raise TypeError("Trading report cache key received incompatible options.")
            return self._structured_cache_key(
                {
                    "version": 1,
                    "kind": kind,
                    "source": _jsonable(self.config.sources.house_ptr),
                    "options": {
                        "name": options.name,
                        "start_date": options.start_date,
                        "end_date": options.end_date,
                        "days": options.days,
                        "filing_start_date": options.filing_start_date,
                        "filing_end_date": options.filing_end_date,
                        "filing_days": options.filing_days,
                        "asset_type": options.asset_type,
                        "ticker": options.ticker,
                        "limit": options.limit,
                        "force_refresh": options.force_refresh,
                    },
                }
            )
        if kind == "13f_report":
            if not isinstance(options, Sec13FReportJobOptions):
                raise TypeError("13F report cache key received incompatible options.")
            return self._structured_cache_key(
                {
                    "version": 1,
                    "kind": kind,
                    "source": _jsonable(self.config.sources.sec_13f),
                    "options": {
                        "manager": options.manager,
                        "cik": options.cik,
                        "accession_number": options.accession_number,
                        "issuer": options.issuer,
                        "cusip": options.cusip,
                        "figi": options.figi,
                        "put_call": options.put_call,
                        "period_start": options.period_start,
                        "period_end": options.period_end,
                        "filing_start": options.filing_start,
                        "filing_end": options.filing_end,
                        "min_value": options.min_value,
                        "min_shares": options.min_shares,
                        "limit": options.limit,
                        "force_refresh": options.force_refresh,
                    },
                }
            )
        if kind == "trendings_report":
            if not isinstance(options, TrendingsReportJobOptions):
                raise TypeError("Trendings report cache key received incompatible options.")
            from_date, to_date = _trendings_date_window(options)
            provider = self.config.providers.adanos
            return self._structured_cache_key(
                {
                    "version": 1,
                    "kind": kind,
                    "provider": {
                        "base_url": provider.base_url,
                        "api_key_env": provider.api_key_env,
                        "api_key_present": bool(os.getenv(provider.api_key_env)),
                        "max_fetch_limit": 100,
                    },
                    "options": {
                        "from": from_date.isoformat(),
                        "to": to_date.isoformat(),
                        "days": options.days,
                        "comparison_days": options.comparison_days,
                        "mentions_change_pct": options.mentions_change_pct,
                        "sentiment_change_pct": options.sentiment_change_pct,
                        "minimum_mentions": options.minimum_mentions,
                    },
                }
            )
        if kind == "statistic":
            if not isinstance(options, StatisticJobOptions):
                raise TypeError("Statistic cache key received incompatible options.")
            return self._structured_cache_key(
                {
                    "version": 1,
                    "kind": kind,
                    "options": {
                        "mode": options.mode,
                        "ticker": options.ticker,
                        "fuzzy_tag": options.fuzzy_tag,
                        "name": options.name,
                        "asset_name": options.asset_name,
                        "asset_type": options.asset_type,
                        "action": options.action,
                        "source": options.source,
                        "sentiment": options.sentiment,
                        "start_date": options.start_date,
                        "end_date": options.end_date,
                        "days": options.days,
                        "bucket": options.bucket,
                        "title": options.title,
                    },
                }
            )
        raise RuntimeError(f"Job kind is not cacheable: {kind}")

    def _bypass_completed_cache(
        self,
        kind: JobKind,
        options: SocialReportJobOptions | TradingReportJobOptions | Sec13FReportJobOptions | TrendingsReportJobOptions | StatisticJobOptions,
    ) -> bool:
        return kind in {"trading_report", "13f_report"} and bool(getattr(options, "force_refresh", False))

    def _structured_cache_key(self, payload: dict[str, Any]) -> str:
        encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
        return hashlib.sha256(encoded.encode("utf-8")).hexdigest()

    def _social_report_cache_key(self, options: SocialReportJobOptions) -> str:
        payload = {
            "version": 1,
            "sources": {
                "x_users": _jsonable(self.config.sources.x_users),
                "subreddits": _jsonable(self.config.sources.subreddits),
            },
            "collectors": _jsonable(self.config.collectors),
            "llm": {
                "provider": self.config.llm.provider,
                "model": self.config.llm.model,
                "thinking_enabled": self.config.llm.thinking_enabled,
                "analysis_prompt_version": PROMPT_VERSION,
                "analysis_x_posts_per_chunk": self.config.llm.analysis_x_posts_per_chunk,
                "analysis_max_chars_per_chunk": self.config.llm.analysis_max_chars_per_chunk,
                "analysis_max_concurrency": self.config.llm.analysis_max_concurrency,
            },
            "options": {
                "download_images": options.download_images,
                "instructions": options.instructions,
                "max_images_per_post": options.max_images_per_post,
                "max_images_total": options.max_images_total,
                "x_method": options.x_method,
                "reddit_method": options.reddit_method,
            },
        }
        return self._structured_cache_key(payload)

    def _job_dir(self, job_id: str) -> Path:
        return self.artifact_dir / job_id

    def _save(self, record: HttpJobRecord) -> None:
        self._jobs[record.job_id] = record
        self._write_json(self._job_dir(record.job_id) / "status.json", record.to_dict())

    def _refresh_memory_status(
        self,
        current_job_id: str | None = None,
        *,
        protected_job_ids: set[str] | None = None,
    ) -> int:
        protected = set(protected_job_ids or set())
        if current_job_id:
            protected.add(current_job_id)
        evicted = self._prune_in_memory_jobs(protected_job_ids=protected)
        if current_job_id and current_job_id in self._jobs:
            job = self._jobs[current_job_id]
            stats = self.memory_status(evicted_in_memory_jobs=evicted)
            job.in_memory_jobs = stats["in_memory_jobs"]
            job.inflight_reports = stats["inflight_reports"]
            job.max_in_memory_jobs = stats["max_in_memory_jobs"]
            job.evicted_in_memory_jobs = stats["evicted_in_memory_jobs"]
            if isinstance(job.cleanup_result, dict):
                job.cleanup_result.update(stats)
            self._save(job)
        return evicted

    def _prune_in_memory_jobs(self, *, protected_job_ids: set[str] | None = None) -> int:
        protected = set(protected_job_ids or set())
        protected.update(report.leader_job_id for report in self._inflight_reports.values())
        evicted = 0
        cutoff = datetime.now(timezone.utc) - timedelta(hours=self.config.server.job_retention_hours)

        for job_id, record in list(self._jobs.items()):
            if job_id in protected or record.status in {"queued", "running"}:
                continue
            status_path = self._job_dir(job_id) / "status.json"
            if not status_path.exists() or _job_sort_datetime(record) < cutoff:
                del self._jobs[job_id]
                evicted += 1

        overflow = len(self._jobs) - self.config.server.max_in_memory_jobs
        if overflow <= 0:
            return evicted

        candidates = [
            record
            for record in self._jobs.values()
            if record.job_id not in protected and record.status not in {"queued", "running"}
        ]
        candidates.sort(key=_job_sort_datetime)
        for record in candidates[:overflow]:
            if record.job_id in self._jobs:
                del self._jobs[record.job_id]
                evicted += 1
        return evicted

    @staticmethod
    def _read_json(path: Path) -> dict[str, Any]:
        return json.loads(path.read_text(encoding="utf-8"))

    @staticmethod
    def _write_json(path: Path, data: dict[str, Any]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
