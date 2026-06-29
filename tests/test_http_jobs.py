"""HTTP job manager fail-safe behavior tests."""

from __future__ import annotations

from pathlib import Path

from stock_sum.api.jobs import HttpJobManager, ReportJobOptions
from stock_sum.config.loader import load_config
from stock_sum.core.models import CollectionRunResult, PipelineCollectionResult, PipelineSectionWarning, Summary
from stock_sum.storage.models import StoredXPost


async def test_report_job_succeeds_with_capitol_warning(tmp_path) -> None:
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
        capitol_scraper=failed_capitol_scraper,
    )
    job = manager.create_report_job("default", ReportJobOptions(mode="discord", include_capitol_trades=True))

    await manager.run_report_job(job.job_id, ReportJobOptions(mode="discord", include_capitol_trades=True))

    status = manager.get_job(job.job_id)
    assert status is not None
    assert status.status == "succeeded"
    assert status.artifact_path is not None
    assert len(status.warnings) == 2
    summary = Path(status.summary_path or "").read_text(encoding="utf-8")
    assert "pipeline_warnings" in summary
    assert "failed_sections" in summary
    assert "Capitol blocked" in summary


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


class FakePipeline:
    def __init__(self, result: PipelineCollectionResult) -> None:
        self.result = result

    async def run_report(self, profile: str) -> PipelineCollectionResult:
        return self.result


class FakeRepository:
    def __init__(self, *, with_social_data: bool) -> None:
        self.with_social_data = with_social_data

    async def list_collection_runs(self, *, profile: str | None = None, limit: int = 20):
        return []

    async def read_x_posts(self, *, handles=None, collector_id=None, profile=None, since=None, limit=50):
        if not self.with_social_data:
            return []
        return [
            StoredXPost(
                status_id="1",
                handle="aleabitoreddit",
                author_handle="aleabitoreddit",
                author_name="Serenity",
                posted_at_text="today",
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

    async def read_reddit_posts(self, *, subreddits=None, collector_id=None, profile=None, since=None, limit=50):
        return []


class FakeLLM:
    def __init__(self) -> None:
        self.calls = 0

    async def summarize(self, payload, *, instructions=None) -> Summary:
        self.calls += 1
        return Summary(
            text="{}",
            model="fake",
            metadata={
                "parsed": {
                    "x_reports": [
                        {
                            "handle": "aleabitoreddit",
                            "overall_summary": ["summary"],
                            "posts": [
                                {
                                    "title": "Signal",
                                    "post_summary": "summary",
                                    "sentiment": "bullish",
                                    "confidence": "medium",
                                    "urls": ["https://x.com/aleabitoreddit/status/1"],
                                }
                            ],
                        }
                    ]
                }
            },
        )


async def failed_capitol_scraper(**kwargs):
    raise RuntimeError("Capitol blocked")


def _test_config(tmp_path):
    config = load_config(Path("stock_sum/config/example.toml"))
    return config.model_copy(
        update={
            "server": config.server.model_copy(update={"artifact_dir": str(tmp_path / "jobs")}),
            "storage": config.storage.model_copy(update={"sqlite_path": str(tmp_path / "stock_sum.sqlite3")}),
        }
    )
