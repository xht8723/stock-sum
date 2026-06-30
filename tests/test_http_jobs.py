"""HTTP job manager fail-safe behavior tests."""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from pathlib import Path

from stock_sum.api.jobs import HttpJobManager, ReportJobOptions
from stock_sum.config.loader import load_config
from stock_sum.core.models import CollectionRunResult, PipelineCollectionResult, PipelineSectionWarning, Summary
from stock_sum.retention import RetentionSummary
from stock_sum.storage.models import StoredXPost


async def test_report_job_succeeds_with_collection_warning(tmp_path) -> None:
    manager = HttpJobManager(
        _test_config(tmp_path),
        pipeline_factory=lambda: FakePipeline(
            PipelineCollectionResult(
                profile="default",
                runs=[],
                warnings=[
                    PipelineSectionWarning(
                        section="collector",
                        source_id="reddit.wallstreetbets",
                        phase="collecting",
                        message="temporary reddit failure",
                    )
                ],
            )
        ),
        repository_factory=lambda: FakeRepository(with_social_data=True),
        llm_client_factory=lambda: FakeLLM(),
    )
    job = manager.create_report_job("default", ReportJobOptions(mode="discord"))

    await manager.run_report_job(job.job_id, ReportJobOptions(mode="discord"))

    status = manager.get_job(job.job_id)
    assert status is not None
    assert status.status == "succeeded"
    assert status.artifact_path is not None
    assert len(status.warnings) == 1
    summary = Path(status.summary_path or "").read_text(encoding="utf-8")
    assert "pipeline_warnings" in summary
    assert "failed_sections" in summary
    assert "temporary reddit failure" in summary


async def test_report_job_fails_when_no_usable_social_data(tmp_path) -> None:
    llm = FakeLLM()
    manager = HttpJobManager(
        _test_config(tmp_path),
        pipeline_factory=lambda: FakePipeline(
            PipelineCollectionResult(
                profile="default",
                runs=[
                    CollectionRunResult(
                        run_id="run-1",
                        collector_id="x.missing",
                        source_type="raw_x_post",
                        status="failed",
                        collected_count=0,
                        inserted_count=0,
                        updated_count=0,
                        sqlite_path=str(tmp_path / "stock_sum.sqlite3"),
                        error="x failed",
                    )
                ],
                warnings=[
                    PipelineSectionWarning(
                        section="collector",
                        source_id="x.missing",
                        phase="collecting",
                        message="x failed",
                    )
                ],
            )
        ),
        repository_factory=lambda: FakeRepository(with_social_data=False),
        llm_client_factory=lambda: llm,
    )
    job = manager.create_report_job("default", ReportJobOptions(mode="html"))

    await manager.run_report_job(job.job_id, ReportJobOptions(mode="html"))

    status = manager.get_job(job.job_id)
    assert status is not None
    assert status.status == "failed"
    assert "no usable source data" in str(status.error)
    assert "x.missing" in str(status.error)
    assert status.artifact_path is None
    assert llm.calls == 0
    assert status.warnings[0]["source_id"] == "x.missing"


async def test_report_job_uses_recent_cache_and_rerenders_requested_mode(tmp_path) -> None:
    pipeline = FakePipeline(_successful_collection_result())
    llm = FakeLLM()
    manager = HttpJobManager(
        _test_config(tmp_path),
        pipeline_factory=lambda: pipeline,
        repository_factory=lambda: FakeRepository(with_social_data=True),
        llm_client_factory=lambda: llm,
    )
    first_options = ReportJobOptions(mode="html")
    first_job = manager.create_report_job("default", first_options)
    await manager.run_report_job(first_job.job_id, first_options)

    second_options = ReportJobOptions(mode="discord")
    second_job = manager.create_report_job("default", second_options)
    await manager.run_report_job(second_job.job_id, second_options)

    second_status = manager.get_job(second_job.job_id)
    assert second_status is not None
    assert second_status.status == "succeeded"
    assert second_status.cache_hit is True
    assert second_status.cached_from_job_id == first_job.job_id
    assert second_status.cache_age_seconds is not None
    assert second_status.artifact_path is not None
    assert second_status.artifact_path.endswith("report.md")
    assert pipeline.calls == 1
    assert llm.calls == 1


async def test_report_job_cache_miss_when_content_options_change(tmp_path) -> None:
    pipeline = FakePipeline(_successful_collection_result())
    llm = FakeLLM()
    manager = HttpJobManager(
        _test_config(tmp_path),
        pipeline_factory=lambda: pipeline,
        repository_factory=lambda: FakeRepository(with_social_data=True),
        llm_client_factory=lambda: llm,
    )
    first_options = ReportJobOptions(mode="html")
    first_job = manager.create_report_job("default", first_options)
    await manager.run_report_job(first_job.job_id, first_options)

    second_options = ReportJobOptions(mode="html", instructions="Focus on semiconductor names.")
    second_job = manager.create_report_job("default", second_options)
    await manager.run_report_job(second_job.job_id, second_options)

    second_status = manager.get_job(second_job.job_id)
    assert second_status is not None
    assert second_status.cache_hit is False
    assert second_status.cached_from_job_id is None
    assert pipeline.calls == 2
    assert llm.calls == 2


async def test_report_job_cache_expires_after_ttl(tmp_path) -> None:
    pipeline = FakePipeline(_successful_collection_result())
    llm = FakeLLM()
    manager = HttpJobManager(
        _test_config(tmp_path),
        pipeline_factory=lambda: pipeline,
        repository_factory=lambda: FakeRepository(with_social_data=True),
        llm_client_factory=lambda: llm,
    )
    options = ReportJobOptions(mode="html")
    first_job = manager.create_report_job("default", options)
    await manager.run_report_job(first_job.job_id, options)
    first_status = manager.get_job(first_job.job_id)
    assert first_status is not None
    first_status.finished_at = (datetime.now(timezone.utc) - timedelta(hours=7)).isoformat()
    manager._save(first_status)

    second_job = manager.create_report_job("default", options)
    await manager.run_report_job(second_job.job_id, options)

    second_status = manager.get_job(second_job.job_id)
    assert second_status is not None
    assert second_status.cache_hit is False
    assert pipeline.calls == 2
    assert llm.calls == 2


async def test_report_job_ignores_cache_when_cached_summary_is_missing(tmp_path) -> None:
    pipeline = FakePipeline(_successful_collection_result())
    llm = FakeLLM()
    manager = HttpJobManager(
        _test_config(tmp_path),
        pipeline_factory=lambda: pipeline,
        repository_factory=lambda: FakeRepository(with_social_data=True),
        llm_client_factory=lambda: llm,
    )
    options = ReportJobOptions(mode="html")
    first_job = manager.create_report_job("default", options)
    await manager.run_report_job(first_job.job_id, options)
    first_status = manager.get_job(first_job.job_id)
    assert first_status is not None
    assert first_status.summary_path is not None
    Path(first_status.summary_path).unlink()

    second_job = manager.create_report_job("default", options)
    await manager.run_report_job(second_job.job_id, options)

    second_status = manager.get_job(second_job.job_id)
    assert second_status is not None
    assert second_status.cache_hit is False
    assert pipeline.calls == 2
    assert llm.calls == 2


async def test_report_job_does_not_use_cache_when_disabled(tmp_path) -> None:
    pipeline = FakePipeline(_successful_collection_result())
    llm = FakeLLM()
    config = _test_config(tmp_path)
    manager = HttpJobManager(
        config.model_copy(update={"server": config.server.model_copy(update={"report_cache_ttl_seconds": 0})}),
        pipeline_factory=lambda: pipeline,
        repository_factory=lambda: FakeRepository(with_social_data=True),
        llm_client_factory=lambda: llm,
    )
    options = ReportJobOptions(mode="html")
    first_job = manager.create_report_job("default", options)
    await manager.run_report_job(first_job.job_id, options)
    second_job = manager.create_report_job("default", options)
    await manager.run_report_job(second_job.job_id, options)

    second_status = manager.get_job(second_job.job_id)
    assert second_status is not None
    assert second_status.cache_hit is False
    assert pipeline.calls == 2
    assert llm.calls == 2


async def test_report_job_runs_retention_after_regular_and_cache_hit_jobs(tmp_path) -> None:
    pipeline = FakePipeline(_successful_collection_result())
    retention = FakeRetentionService()
    manager = HttpJobManager(
        _test_config(tmp_path),
        pipeline_factory=lambda: pipeline,
        repository_factory=lambda: FakeRepository(with_social_data=True),
        llm_client_factory=lambda: FakeLLM(),
        retention_service_factory=lambda: retention,
    )
    options = ReportJobOptions(mode="html")
    first_job = manager.create_report_job("default", options)
    await manager.run_report_job(first_job.job_id, options)
    second_job = manager.create_report_job("default", options)
    await manager.run_report_job(second_job.job_id, options)

    first_status = manager.get_job(first_job.job_id)
    second_status = manager.get_job(second_job.job_id)
    assert retention.calls == 2
    assert first_status is not None
    assert second_status is not None
    assert first_status.cleanup_result is not None
    assert second_status.cache_hit is True
    assert second_status.cleanup_result is not None


async def test_identical_concurrent_report_jobs_coalesce_to_one_pipeline_run(tmp_path) -> None:
    pipeline = FakePipeline(_successful_collection_result(), delay_seconds=0.05)
    llm = FakeLLM()
    manager = HttpJobManager(
        _test_config(tmp_path),
        pipeline_factory=lambda: pipeline,
        repository_factory=lambda: FakeRepository(with_social_data=True),
        llm_client_factory=lambda: llm,
    )
    first_options = ReportJobOptions(mode="html")
    second_options = ReportJobOptions(mode="discord")
    first_job = manager.create_report_job("default", first_options)
    first_task = asyncio.create_task(manager.run_report_job(first_job.job_id, first_options))
    await asyncio.sleep(0.01)
    second_job = manager.create_report_job("default", second_options)
    second_task = asyncio.create_task(manager.run_report_job(second_job.job_id, second_options))

    await asyncio.gather(first_task, second_task)

    first_status = manager.get_job(first_job.job_id)
    second_status = manager.get_job(second_job.job_id)
    assert first_status is not None
    assert second_status is not None
    assert first_status.status == "succeeded"
    assert second_status.status == "succeeded"
    assert second_status.coalesced_from_job_id == first_job.job_id
    assert second_status.coalesced_wait_seconds is not None
    assert second_status.artifact_path is not None
    assert second_status.artifact_path.endswith("report.md")
    assert second_status.cache_hit is False
    assert pipeline.calls == 1
    assert llm.calls == 1


async def test_concurrent_report_jobs_do_not_coalesce_when_content_options_change(tmp_path) -> None:
    pipeline = FakePipeline(_successful_collection_result(), delay_seconds=0.05)
    llm = FakeLLM()
    manager = HttpJobManager(
        _test_config(tmp_path),
        pipeline_factory=lambda: pipeline,
        repository_factory=lambda: FakeRepository(with_social_data=True),
        llm_client_factory=lambda: llm,
    )
    first_options = ReportJobOptions(mode="html")
    second_options = ReportJobOptions(mode="html", instructions="Focus on semiconductor names.")
    first_job = manager.create_report_job("default", first_options)
    second_job = manager.create_report_job("default", second_options)

    await asyncio.gather(
        manager.run_report_job(first_job.job_id, first_options),
        manager.run_report_job(second_job.job_id, second_options),
    )

    first_status = manager.get_job(first_job.job_id)
    second_status = manager.get_job(second_job.job_id)
    assert first_status is not None
    assert second_status is not None
    assert first_status.status == "succeeded"
    assert second_status.status == "succeeded"
    assert second_status.coalesced_from_job_id is None
    assert pipeline.calls == 2
    assert llm.calls == 2


async def test_coalesced_report_job_fails_when_leader_fails(tmp_path) -> None:
    pipeline = FakePipeline(_successful_collection_result(), delay_seconds=0.05, fail=True)
    manager = HttpJobManager(
        _test_config(tmp_path),
        pipeline_factory=lambda: pipeline,
        repository_factory=lambda: FakeRepository(with_social_data=True),
        llm_client_factory=lambda: FakeLLM(),
    )
    options = ReportJobOptions(mode="html")
    first_job = manager.create_report_job("default", options)
    first_task = asyncio.create_task(manager.run_report_job(first_job.job_id, options))
    await asyncio.sleep(0.01)
    second_job = manager.create_report_job("default", options)
    second_task = asyncio.create_task(manager.run_report_job(second_job.job_id, options))

    await asyncio.gather(first_task, second_task)

    first_status = manager.get_job(first_job.job_id)
    second_status = manager.get_job(second_job.job_id)
    assert first_status is not None
    assert second_status is not None
    assert first_status.status == "failed"
    assert second_status.status == "failed"
    assert second_status.coalesced_from_job_id == first_job.job_id
    assert f"Coalesced report leader {first_job.job_id} failed" in str(second_status.error)
    assert pipeline.calls == 1


async def test_inflight_report_coalescing_can_be_disabled(tmp_path) -> None:
    pipeline = FakePipeline(_successful_collection_result(), delay_seconds=0.05)
    config = _test_config(tmp_path)
    manager = HttpJobManager(
        config.model_copy(update={"server": config.server.model_copy(update={"coalesce_inflight_reports": False})}),
        pipeline_factory=lambda: pipeline,
        repository_factory=lambda: FakeRepository(with_social_data=True),
        llm_client_factory=lambda: FakeLLM(),
    )
    options = ReportJobOptions(mode="html")
    first_job = manager.create_report_job("default", options)
    second_job = manager.create_report_job("default", options)

    await asyncio.gather(
        manager.run_report_job(first_job.job_id, options),
        manager.run_report_job(second_job.job_id, options),
    )

    second_status = manager.get_job(second_job.job_id)
    assert second_status is not None
    assert second_status.coalesced_from_job_id is None
    assert second_status.cache_hit is False
    assert pipeline.calls == 2


async def test_coalesced_report_job_runs_retention_after_writing_artifact(tmp_path) -> None:
    pipeline = FakePipeline(_successful_collection_result(), delay_seconds=0.05)
    retention = FakeRetentionService()
    manager = HttpJobManager(
        _test_config(tmp_path),
        pipeline_factory=lambda: pipeline,
        repository_factory=lambda: FakeRepository(with_social_data=True),
        llm_client_factory=lambda: FakeLLM(),
        retention_service_factory=lambda: retention,
    )
    options = ReportJobOptions(mode="html")
    first_job = manager.create_report_job("default", options)
    first_task = asyncio.create_task(manager.run_report_job(first_job.job_id, options))
    await asyncio.sleep(0.01)
    second_job = manager.create_report_job("default", options)
    second_task = asyncio.create_task(manager.run_report_job(second_job.job_id, options))

    await asyncio.gather(first_task, second_task)

    second_status = manager.get_job(second_job.job_id)
    assert second_status is not None
    assert second_status.status == "succeeded"
    assert second_status.coalesced_from_job_id == first_job.job_id
    assert second_status.cleanup_result is not None
    assert retention.calls == 2


class FakePipeline:
    def __init__(self, result: PipelineCollectionResult, *, delay_seconds: float = 0, fail: bool = False) -> None:
        self.result = result
        self.delay_seconds = delay_seconds
        self.fail = fail
        self.calls = 0

    async def run_report(self, profile: str) -> PipelineCollectionResult:
        self.calls += 1
        if self.delay_seconds:
            await asyncio.sleep(self.delay_seconds)
        if self.fail:
            raise RuntimeError("pipeline failed")
        return self.result


class FakeRepository:
    def __init__(self, *, with_social_data: bool) -> None:
        self.with_social_data = with_social_data
        self.x_analysis_rows = []
        self.reddit_post_analysis_rows = []
        self.reddit_comment_analysis_rows = []

    async def list_collection_runs(self, *, profile: str | None = None, limit: int = 20):
        return []

    async def read_x_posts(self, *, handles=None, since_posted_at=None, collector_id=None, profile=None, since=None, limit=50):
        if not self.with_social_data:
            return []
        return [
            StoredXPost(
                status_id="1",
                handle="aleabitoreddit",
                author_handle="aleabitoreddit",
                author_name="Serenity",
                posted_at_text="2999-01-01T00:00:00+00:00",
                url="https://x.com/aleabitoreddit/status/1",
                text="market signal",
                reply_count=1,
                repost_count=2,
                like_count=3,
                quote_count=4,
                view_count=5,
                raw_metadata={},
                collected_at="2026-06-29T00:00:00+00:00",
            )
        ]

    async def read_reddit_posts(self, *, subreddits=None, since_posted_at=None, collector_id=None, profile=None, since=None, limit=50):
        return []

    async def start_llm_analysis_run(self, **kwargs):
        return None

    async def finish_llm_analysis_run(self, **kwargs):
        return None

    async def save_llm_x_post_analyses(self, rows):
        self.x_analysis_rows.extend(rows)

    async def save_llm_reddit_post_analyses(self, rows):
        self.reddit_post_analysis_rows.extend(rows)

    async def save_llm_reddit_comment_analyses(self, rows):
        self.reddit_comment_analysis_rows.extend(rows)

    async def read_llm_analysis_report(self, *, profile: str, analysis_run_id: str | None = None):
        posts = [
            {
                "source_ref": row["source_ref"],
                "source_id": row["status_id"],
                "title": row["summary"],
                "post_summary": row["summary"],
                "sentiment": row["sentiment"],
                "tags": ["market", "social", "signal", "risk", "watch"],
                "interpretation": row["interpretation"],
                "confidence": row["confidence"],
                "urls": [row["url"]],
            }
            for row in self.x_analysis_rows
        ]
        return {
            "x_reports": [{"handle": "aleabitoreddit", "overall_summary": ["summary"], "posts": posts}],
            "reddit_report": {"overall_summary": [], "posts": []},
        }


class FakeLLM:
    provider = "fake"
    model = "fake"

    def __init__(self) -> None:
        self.calls = 0

    async def summarize(self, payload, *, instructions=None) -> Summary:
        return await self.complete_json([])

    async def complete_json(self, messages) -> Summary:
        self.calls += 1
        return Summary(
            text='{"source":"x","posts":[{"source_ref":"x1","source_id":"1","sentiment":"bullish","tags":["market","social","signal","risk","watch"],"summary":"summary","interpretation":"interpretation","confidence":"medium"}]}',
            model="fake",
            metadata={
                "parsed": {
                    "source": "x",
                    "posts": [
                        {
                            "source_ref": "x1",
                            "source_id": "1",
                            "sentiment": "bullish",
                            "tags": ["market", "social", "signal", "risk", "watch"],
                            "summary": "summary",
                            "interpretation": "interpretation",
                            "confidence": "medium",
                        }
                    ],
                }
            },
        )


class FakeRetentionService:
    def __init__(self) -> None:
        self.calls = 0

    async def prune(self, *, protected_paths=None, dry_run: bool = False) -> RetentionSummary:
        self.calls += 1
        return RetentionSummary(
            enabled=True,
            dry_run=dry_run,
            max_total_bytes=100,
            bytes_before=50,
            bytes_after=50,
        )




def _successful_collection_result() -> PipelineCollectionResult:
    return PipelineCollectionResult(profile="default", runs=[], warnings=[])


def _test_config(tmp_path):
    config = load_config(Path("stock_sum/config/example.toml"))
    return config.model_copy(
        update={
            "server": config.server.model_copy(update={"artifact_dir": str(tmp_path / "jobs")}),
            "storage": config.storage.model_copy(update={"sqlite_path": str(tmp_path / "stock_sum.sqlite3")}),
        }
    )
