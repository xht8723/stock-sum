"""HTTP job orchestration for automation clients."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Awaitable, Callable, Literal
from uuid import uuid4
import json

from stock_sum.collectors.playwright.capitol_trades import CAPITOL_TRADES_URL, scrape_capitol_trades
from stock_sum.config.models import AppConfig
from stock_sum.core.context import RuntimeContext
from stock_sum.core.models import PipelineCollectionResult, Summary
from stock_sum.core.pipeline import ReportPipeline
from stock_sum.llm.registry import build_llm_client
from stock_sum.media.downloader import MediaDownloader
from stock_sum.reports.presentation import PresentationRenderer
from stock_sum.reports.summary_input import SummaryInputBuilder
from stock_sum.storage.sqlite import SQLiteStorageRepository

JobStatus = Literal["queued", "running", "succeeded", "failed"]
JobKind = Literal["report", "collect"]
ReportMode = Literal["html", "markdown", "discord", "text", "json"]


@dataclass(frozen=True)
class ReportJobOptions:
    """Options for a full report job."""

    mode: ReportMode = "html"
    download_images: bool = False
    include_capitol_trades: bool = False
    capitol_trades_limit: int = 12
    instructions: str | None = None
    title: str = "Market Social Digest"
    max_images_per_post: int = 3
    max_images_total: int = 20


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

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-safe representation."""

        return asdict(self)


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
        capitol_scraper: Callable[..., Awaitable[Any]] | None = None,
    ) -> None:
        self.config = config
        self.artifact_dir = Path(config.server.artifact_dir)
        self._jobs: dict[str, HttpJobRecord] = {}
        self._pipeline_factory = pipeline_factory or (
            lambda: ReportPipeline(RuntimeContext(config=config), repository=self._repository_factory())
        )
        self._repository_factory = repository_factory or (lambda: SQLiteStorageRepository(config.storage.sqlite_path))
        self._llm_client_factory = llm_client_factory or (lambda: build_llm_client(config.llm))
        self._renderer_factory = renderer_factory or (lambda title: PresentationRenderer(title=title))
        self._capitol_scraper = capitol_scraper or scrape_capitol_trades

    def create_report_job(self, profile: str, options: ReportJobOptions) -> HttpJobRecord:
        """Create a queued full report job."""

        self._validate_profile(profile)
        record = self._new_job(kind="report", profile=profile, mode=options.mode)
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
        record = HttpJobRecord(**data)
        self._jobs[job_id] = record
        return record

    async def run_report_job(self, job_id: str, options: ReportJobOptions) -> None:
        """Run collection, payload assembly, LLM summarization, and rendering."""

        try:
            self._mark_running(job_id, phase="collecting")
            job = self._require_job(job_id)
            collection_result = await self._pipeline_factory().run_report(job.profile)
            self._update(job_id, phase="building_payload", collection_result=_pipeline_result_to_dict(collection_result))

            repository = self._repository_factory()
            downloader = MediaDownloader(self.config.media, repository) if options.download_images else None
            builder = SummaryInputBuilder(config=self.config, repository=repository, downloader=downloader)
            summary_input = await builder.build(profile=job.profile, download_images=options.download_images)
            payload_data = summary_input.to_dict(
                mode="compact",
                max_images_per_post=options.max_images_per_post,
                max_images_total=options.max_images_total,
            )

            self._update(job_id, phase="summarizing")
            summary = await self._llm_client_factory().summarize(payload_data, instructions=options.instructions)
            response_data = _summary_response_data(
                profile=job.profile,
                provider=self.config.llm.provider,
                summary=summary,
                input_media=payload_data.get("media", {}) if isinstance(payload_data, dict) else {},
            )

            if options.include_capitol_trades:
                self._update(job_id, phase="scraping_capitol_trades")
                snapshot = await self._capitol_scraper(
                    url=CAPITOL_TRADES_URL,
                    limit=options.capitol_trades_limit,
                )
                response_data["capitol_trades"] = snapshot.to_dict()

            summary_path = self._job_dir(job_id) / "summary.json"
            self._write_json(summary_path, response_data)

            self._update(job_id, phase="rendering", summary_path=str(summary_path))
            artifact_path, media_type = self._write_artifact(job_id, response_data, options)
            self._mark_succeeded(
                job_id,
                artifact_path=str(artifact_path),
                artifact_media_type=media_type,
                summary_path=str(summary_path),
            )
        except Exception as exc:
            self._mark_failed(job_id, str(exc))

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

    def _validate_profile(self, profile: str) -> None:
        if profile not in self.config.reports:
            raise KeyError(f"Unknown report profile: {profile}")

    def _new_job(self, *, kind: JobKind, profile: str, mode: str) -> HttpJobRecord:
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
    ) -> None:
        self._update(
            job_id,
            status="succeeded",
            phase="succeeded",
            finished_at=_utc_now(),
            artifact_path=artifact_path,
            artifact_media_type=artifact_media_type,
            summary_path=summary_path,
            collection_result=collection_result,
        )

    def _mark_failed(self, job_id: str, error: str) -> None:
        self._update(job_id, status="failed", phase="failed", finished_at=_utc_now(), error=error)

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
        rendered = self._renderer_factory(options.title).render(response_data, mode=options.mode)
        artifact_path = self._job_dir(job_id) / f"report.{extension}"
        artifact_path.write_text(rendered, encoding="utf-8")
        return artifact_path, media_type

    def _job_dir(self, job_id: str) -> Path:
        return self.artifact_dir / job_id

    def _save(self, record: HttpJobRecord) -> None:
        self._jobs[record.job_id] = record
        self._write_json(self._job_dir(record.job_id) / "status.json", record.to_dict())

    @staticmethod
    def _write_json(path: Path, data: dict[str, Any]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _pipeline_result_to_dict(result: PipelineCollectionResult) -> dict[str, Any]:
    data = asdict(result)
    data["collected_count"] = result.collected_count
    data["inserted_count"] = result.inserted_count
    data["updated_count"] = result.updated_count
    return data


def _summary_response_data(
    *,
    profile: str,
    provider: str,
    summary: Summary,
    input_media: dict[str, Any],
) -> dict[str, Any]:
    return {
        "profile": profile,
        "provider": provider,
        "model": summary.model,
        "summary_text": summary.text,
        "summary": summary.metadata.get("parsed"),
        "input_media": input_media,
        "metadata": {key: value for key, value in summary.metadata.items() if key != "parsed"},
    }
