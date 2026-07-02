"""HTTP job orchestration for automation clients."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field, fields
from datetime import datetime, time, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Literal
from uuid import uuid4
import asyncio
import hashlib
import json

from stock_sum.config.models import AppConfig
from stock_sum.collectors.api.house import HOUSE_PTR_SOURCE_TYPE
from stock_sum.collectors.factory import source_type_for_collector_id
from stock_sum.core.context import RuntimeContext
from stock_sum.core.models import PipelineCollectionResult, PipelineSectionWarning
from stock_sum.core.pipeline import ReportPipeline
from stock_sum.llm.analysis import LLMAnalysisService, PROMPT_VERSION
from stock_sum.llm.registry import build_llm_client
from stock_sum.media.downloader import MediaDownloader
from stock_sum.retention import DataRetentionService
from stock_sum.reports.presentation import PresentationRenderer
from stock_sum.reports.summary_input import SummaryInputBuilder
from stock_sum.storage.sqlite import SQLiteStorageRepository

JobStatus = Literal["queued", "running", "succeeded", "failed"]
JobKind = Literal["report", "trading_report", "collect"]
ReportMode = Literal["html", "markdown", "discord", "text", "json"]


@dataclass(frozen=True)
class ReportJobOptions:
    """Options for a full report job."""

    mode: ReportMode = "html"
    detail: Literal["minimum", "medium", "full"] = "minimum"
    download_images: bool = False
    instructions: str | None = None
    title: str = "Market Social Digest"
    max_images_per_post: int = 3
    max_images_total: int = 20


@dataclass(frozen=True)
class TradingReportJobOptions:
    """Options for a House PTR trading disclosure report job."""

    mode: ReportMode = "html"
    name: str | None = None
    start_date: str | None = None
    end_date: str | None = None
    days: int | None = None
    limit: int | None = None
    title: str = "Official Trading Disclosures"
    force_refresh: bool = False


@dataclass
class HttpJobRecord:
    """Persisted local HTTP job metadata."""

    job_id: str
    kind: JobKind
    profile: str
    status: JobStatus
    phase: str
    created_at: str
    updated_at: str
    started_at: str | None = None
    finished_at: str | None = None
    error: str | None = None
    mode: str | None = None
    artifact_path: str | None = None
    artifact_media_type: str | None = None
    summary_path: str | None = None
    collection_result: dict[str, Any] | None = None
    warnings: list[dict[str, Any]] = field(default_factory=list)
    cache_key: str | None = None
    cache_hit: bool = False
    cached_from_job_id: str | None = None
    cache_age_seconds: int | None = None
    coalesced_from_job_id: str | None = None
    coalesced_wait_seconds: int | None = None
    cleanup_result: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-safe representation."""

        return asdict(self)


@dataclass
class _InFlightReport:
    """A report job currently producing a summary for a cache key."""

    cache_key: str
    leader_job_id: str
    started_at: datetime
    done: asyncio.Event


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
    ) -> None:
        self.config = config
        self.artifact_dir = Path(config.server.artifact_dir)
        self._jobs: dict[str, HttpJobRecord] = {}
        self._inflight_reports: dict[str, _InFlightReport] = {}
        self._inflight_lock = asyncio.Lock()
        self._pipeline_factory = pipeline_factory or (
            lambda: ReportPipeline(RuntimeContext(config=config), repository=self._repository_factory())
        )
        self._repository_factory = repository_factory or (lambda: SQLiteStorageRepository(config.storage.sqlite_path))
        self._llm_client_factory = llm_client_factory or (lambda: build_llm_client(config.llm))
        self._renderer_factory = renderer_factory or (lambda title: PresentationRenderer(title=title))
        self._retention_service_factory = retention_service_factory or (lambda: DataRetentionService(config))

    def create_report_job(self, profile: str, options: ReportJobOptions) -> HttpJobRecord:
        """Create a queued social-media report job."""

        self._validate_profile(profile)
        record = self._new_job(
            kind="report",
            profile=profile,
            mode=options.mode,
            cache_key=self._report_cache_key(profile, options),
        )
        self._save(record)
        return record

    def create_trading_report_job(self, options: TradingReportJobOptions) -> HttpJobRecord:
        """Create a queued House PTR trading disclosure report job."""

        _validate_trading_filters(options)
        record = self._new_job(
            kind="trading_report",
            profile="trading",
            mode=options.mode,
        )
        self._save(record)
        return record

    def create_collect_job(self, profile: str) -> HttpJobRecord:
        """Create a queued collection-only job."""

        self._validate_profile(profile)
        record = self._new_job(kind="collect", profile=profile, mode="json")
        self._save(record)
        return record

    def get_job(self, job_id: str) -> HttpJobRecord | None:
        """Return a known in-memory or persisted job record."""

        if job_id in self._jobs:
            return self._jobs[job_id]
        path = self._job_dir(job_id) / "status.json"
        if not path.exists():
            return None
        data = json.loads(path.read_text(encoding="utf-8"))
        record = _job_record_from_dict(data)
        self._jobs[job_id] = record
        return record

    async def run_report_job(self, job_id: str, options: ReportJobOptions) -> None:
        """Run social collection, payload assembly, LLM summarization, and rendering."""

        is_inflight_leader = False
        cache_key: str | None = None
        try:
            self._mark_running(job_id, phase="cache_lookup")
            job = self._require_job(job_id)
            cache_key = job.cache_key or self._report_cache_key(job.profile, options)
            self._update(job_id, cache_key=cache_key)
            cache_hit = self._find_report_cache_hit(job_id, cache_key)
            if cache_hit is not None:
                self._write_cached_report_artifacts(job_id, cache_hit, options)
                return

            is_inflight_leader, inflight_report = await self._join_or_register_inflight_report(job_id, cache_key)
            if not is_inflight_leader:
                await self._wait_for_coalesced_report(job_id, inflight_report, options)
                return

            self._update(job_id, phase="collecting")
            collection_result = await self._pipeline_factory().run_report(
                job.profile,
                collector_ids=_social_collector_ids(self.config, job.profile),
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
            summary_input = await builder.build(profile=job.profile, download_images=options.download_images)
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
                profile=job.profile,
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

    async def run_trading_report_job(self, job_id: str, options: TradingReportJobOptions) -> None:
        """Run a House PTR trading disclosure report without LLM analysis."""

        try:
            _validate_trading_filters(options)
            self._mark_running(job_id, phase="refresh_check")
            repository = self._repository_factory()
            warnings: list[PipelineSectionWarning] = []
            collection_result: PipelineCollectionResult | None = None

            if self.config.sources.house_ptr.enabled:
                if options.force_refresh or await self._house_ptr_refresh_needed(repository):
                    self._update(job_id, phase="refreshing_house_ptr")
                    run = await self._pipeline_factory().collect_collector(
                        "house.ptr",
                        profile="trading",
                        raise_on_error=False,
                    )
                    collection_result = PipelineCollectionResult(profile="trading", runs=[run], warnings=list(run.warnings))
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
            rows = await repository.read_house_ptr_trades(
                name_contains=options.name,
                transaction_start=transaction_start,
                transaction_end=transaction_end,
                limit=options.limit,
            )
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
                "filters": _trading_filter_data(options, transaction_start, transaction_end),
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
            )
            artifact_path, media_type = self._write_trading_artifact(job_id, response_data, options)
            self._mark_succeeded(
                job_id,
                artifact_path=str(artifact_path),
                artifact_media_type=media_type,
                summary_path=str(summary_path),
                warnings=warning_data,
                collection_result=_pipeline_result_to_dict(collection_result) if collection_result else None,
            )
        except Exception as exc:
            self._mark_failed(job_id, str(exc))
        finally:
            await self._run_retention(job_id)

    async def run_collect_job(self, job_id: str) -> None:
        """Run collection-only job and persist its JSON artifact."""

        try:
            self._mark_running(job_id, phase="collecting")
            job = self._require_job(job_id)
            collection_result = await self._pipeline_factory().run_report(job.profile)
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

    def _validate_profile(self, profile: str) -> None:
        if profile not in self.config.reports:
            raise KeyError(f"Unknown report profile: {profile}")

    async def _house_ptr_refresh_needed(self, repository: SQLiteStorageRepository) -> bool:
        ttl_seconds = self.config.sources.house_ptr.refresh_ttl_seconds
        if ttl_seconds <= 0:
            return True
        runs = await repository.list_collection_runs(profile="trading", limit=20)
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

    def _new_job(self, *, kind: JobKind, profile: str, mode: str, cache_key: str | None = None) -> HttpJobRecord:
        now = _utc_now()
        job_id = uuid4().hex
        record = HttpJobRecord(
            job_id=job_id,
            kind=kind,
            profile=profile,
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
            self._update(job_id, cleanup_result=summary.to_dict())
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
                },
            )

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
        options: ReportJobOptions,
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

    def _write_cached_report_artifacts(
        self,
        job_id: str,
        cache_hit: HttpJobRecord,
        options: ReportJobOptions,
    ) -> None:
        summary_path = Path(cache_hit.summary_path or "")
        response_data = self._read_json(summary_path)
        warning_data = _safe_warning_list(response_data.get("pipeline_warnings") or response_data.get("failed_sections"))
        current_summary_path = self._job_dir(job_id) / "summary.json"
        self._write_json(current_summary_path, response_data)
        self._update(job_id, phase="rendering", summary_path=str(current_summary_path), warnings=warning_data)
        artifact_path, media_type = self._write_artifact(job_id, response_data, options)
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
        options: ReportJobOptions,
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
        options: ReportJobOptions,
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
        artifact_path, media_type = self._write_artifact(job_id, response_data, options)
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

    def _find_report_cache_hit(self, current_job_id: str, cache_key: str | None) -> HttpJobRecord | None:
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
            if record.kind != "report" or record.status != "succeeded" or record.cache_key != cache_key:
                continue
            if not record.summary_path or not Path(record.summary_path).exists():
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

    def _report_cache_key(self, profile: str, options: ReportJobOptions) -> str:
        payload = {
            "version": 1,
            "profile": profile,
            "profile_config": _jsonable(self.config.reports.get(profile)),
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
            },
        }
        encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
        return hashlib.sha256(encoded.encode("utf-8")).hexdigest()

    def _job_dir(self, job_id: str) -> Path:
        return self.artifact_dir / job_id

    def _save(self, record: HttpJobRecord) -> None:
        self._jobs[record.job_id] = record
        self._write_json(self._job_dir(record.job_id) / "status.json", record.to_dict())

    @staticmethod
    def _read_json(path: Path) -> dict[str, Any]:
        return json.loads(path.read_text(encoding="utf-8"))

    @staticmethod
    def _write_json(path: Path, data: dict[str, Any]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _parse_utc_datetime(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _social_collector_ids(config: AppConfig, profile: str) -> list[str]:
    collector_ids = list(config.reports[profile].collector_ids)
    result: list[str] = []
    for collector_id in collector_ids:
        try:
            source_type = source_type_for_collector_id(config, collector_id)
        except Exception:
            result.append(collector_id)
            continue
        if source_type != HOUSE_PTR_SOURCE_TYPE:
            result.append(collector_id)
    return result


def _validate_trading_filters(options: TradingReportJobOptions) -> None:
    if not any((options.name, options.start_date, options.end_date, options.days)):
        raise ValueError("Trading report requires at least one filter: name, start_date/end_date, or days.")
    if options.days is not None and (options.start_date or options.end_date):
        raise ValueError("Trading report accepts either days or explicit start/end dates, not both.")


def _trading_date_window(options: TradingReportJobOptions) -> tuple[datetime | None, datetime | None]:
    if options.days is not None:
        now = datetime.now(timezone.utc)
        return now - timedelta(days=options.days), now
    return _parse_date_filter(options.start_date, end_of_day=False), _parse_date_filter(options.end_date, end_of_day=True)


def _parse_date_filter(value: str | None, *, end_of_day: bool) -> datetime | None:
    if not value:
        return None
    text = value.strip()
    for fmt in ("%Y-%m-%d", "%m/%d/%Y"):
        try:
            parsed_date = datetime.strptime(text, fmt).date()
            return datetime.combine(parsed_date, time.max if end_of_day else time.min, tzinfo=timezone.utc)
        except ValueError:
            continue
    parsed = _parse_utc_datetime(text)
    if parsed is None:
        raise ValueError(f"Invalid trading report date: {value}")
    if end_of_day and parsed.time() == time.min:
        return datetime.combine(parsed.date(), time.max, tzinfo=timezone.utc)
    return parsed


def _trading_filter_data(
    options: TradingReportJobOptions,
    transaction_start: datetime | None,
    transaction_end: datetime | None,
) -> dict[str, Any]:
    return {
        "name": options.name,
        "start_date": options.start_date,
        "end_date": options.end_date,
        "days": options.days,
        "transaction_start": transaction_start.isoformat() if transaction_start else None,
        "transaction_end": transaction_end.isoformat() if transaction_end else None,
        "limit": options.limit,
        "force_refresh": options.force_refresh,
    }


def _job_record_from_dict(data: dict[str, Any]) -> HttpJobRecord:
    allowed = {item.name for item in fields(HttpJobRecord)}
    return HttpJobRecord(**{key: value for key, value in data.items() if key in allowed})


def _jsonable(value: Any) -> Any:
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json")
    if isinstance(value, dict):
        return {str(key): _jsonable(value[key]) for key in sorted(value)}
    if isinstance(value, list):
        return [_jsonable(item) for item in value]
    return value


def _safe_warning_list(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, dict)]


def _pipeline_result_to_dict(result: PipelineCollectionResult) -> dict[str, Any]:
    data = asdict(result)
    data["collected_count"] = result.collected_count
    data["inserted_count"] = result.inserted_count
    data["updated_count"] = result.updated_count
    return data


def _warnings_to_dicts(warnings: list[PipelineSectionWarning]) -> list[dict[str, Any]]:
    return [asdict(warning) for warning in warnings]


def _summary_input_has_social_data(summary_input: Any) -> bool:
    for section in getattr(summary_input, "x", []):
        if getattr(section, "posts", []):
            return True
    for section in getattr(summary_input, "reddit", []):
        if getattr(section, "posts", []):
            return True
    return False


def _no_social_data_message(result: PipelineCollectionResult) -> str:
    failed = [run.collector_id for run in result.runs if run.status == "failed"]
    message = "Collection completed with no usable source data."
    if failed:
        message += f" Failed collectors: {', '.join(failed)}."
    return message


def _house_ptr_rows_to_dicts(rows: list[Any]) -> list[dict[str, Any]]:
    return [
        {
            "doc_id": row.doc_id,
            "year": row.year,
            "name": row.name,
            "status": row.status,
            "state": row.state,
            "filing_date": row.filing_date,
            "filing_date_utc": row.filing_date_utc,
            "pdf_url": row.pdf_url,
            "table_index": row.table_index,
            "row_index": row.row_index,
            "asset": row.asset,
            "transaction_type": row.transaction_type,
            "transaction_date": row.transaction_date,
            "transaction_date_utc": row.transaction_date_utc,
            "transaction_action": row.transaction_action,
            "amount": row.amount,
            "raw_cells": row.raw_cells,
            "collected_at": row.collected_at,
        }
        for row in rows
    ]

def _analysis_response_data(
    *,
    profile: str,
    provider: str,
    analysis: Any,
    input_media: dict[str, Any],
) -> dict[str, Any]:
    return {
        "profile": profile,
        "provider": provider,
        "model": analysis.model,
        "summary_text": json.dumps(analysis.summary, ensure_ascii=False),
        "summary": analysis.summary,
        "input_media": input_media,
        "metadata": {
            "analysis_run_id": analysis.analysis_run_id,
            "prompt_version": analysis.prompt_version,
            "chunk_count": analysis.chunk_count,
            "succeeded_count": analysis.succeeded_count,
            "failed_count": analysis.failed_count,
        },
    }
