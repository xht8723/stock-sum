"""Collection pipeline persistence tests."""

from stock_sum.config.models import AppConfig, CollectorConfig, LLMConfig, ReportProfileConfig, StorageConfig
from stock_sum.core.context import RuntimeContext
from stock_sum.core.errors import ConfigurationError
from stock_sum.core.models import PipelineSectionWarning, ProviderApiResponse, RawItem, RawItemSaveResult
from stock_sum.core.pipeline import ReportPipeline


class FakeCollector:
    collector_id = "api.test"
    api_responses = [
        ProviderApiResponse(
            provider="xpoz",
            tool_name="getExampleRows",
            request_arguments={"limit": 1},
            raw_response_text="status: success",
            parsed_rows=[{"id": "123"}],
            row_count=1,
        )
    ]

    async def collect(self, context):
        return [
            RawItem(
                source_id="123",
                source_type="test_source",
                url="https://example.com/items/123",
                text="hello",
                metadata={"source": "test"},
            )
        ]


class FailingCollector:
    api_responses = [
        ProviderApiResponse(
            provider="xpoz",
            tool_name="getPartialRows",
            request_arguments={"limit": 1},
            raw_response_text="status: success",
            parsed_rows=[{"id": "partial"}],
            row_count=1,
        )
    ]

    async def collect(self, context):
        raise RuntimeError("collector unavailable")


class WarningCollector(FakeCollector):
    def __init__(self):
        self.warnings = [
            PipelineSectionWarning(
                section="collector",
                source_id="api.test",
                phase="collecting",
                message="fetch cap may have hidden more posts",
            )
        ]


class FakeRepository:
    def __init__(self):
        self.started = []
        self.finished = []
        self.saved = []
        self.saved_provider_responses = []

    async def initialize(self):
        return None

    async def start_collection_run(self, **kwargs):
        self.started.append(kwargs)

    async def finish_collection_run(self, **kwargs):
        self.finished.append(kwargs)

    async def save_raw_items(self, items):
        self.saved.append(items)
        return RawItemSaveResult(
            source_type="test_source",
            collected_count=len(items),
            inserted_count=len(items),
            updated_count=0,
        )

    async def save_provider_api_responses(self, **kwargs):
        self.saved_provider_responses.append(kwargs)

    async def save_summaries(self, summaries):
        raise NotImplementedError

    async def save_report(self, report):
        raise NotImplementedError


def _config(tmp_path) -> AppConfig:
    return AppConfig(
        storage=StorageConfig(sqlite_path=str(tmp_path / "test.sqlite3")),
        llm=LLMConfig(provider="deepseek", model="deepseek-v4-flash", api_key_env="DEEPSEEK_API_KEY"),
        collectors={"api": {"test": CollectorConfig(kind="test_source")}},
        reports={"default": ReportProfileConfig(schedule="0 8 * * *", collector_ids=["api.test"])},
    )


def _multi_config(tmp_path) -> AppConfig:
    return AppConfig(
        storage=StorageConfig(sqlite_path=str(tmp_path / "test.sqlite3")),
        llm=LLMConfig(provider="deepseek", model="deepseek-v4-flash", api_key_env="DEEPSEEK_API_KEY"),
        collectors={
            "api": {
                "good": CollectorConfig(kind="test_source"),
                "bad": CollectorConfig(kind="bad_source"),
            }
        },
        reports={"default": ReportProfileConfig(schedule="0 8 * * *", collector_ids=["api.bad", "api.good"])},
    )


async def test_pipeline_collects_and_persists_with_fake_collector(tmp_path) -> None:
    repository = FakeRepository()
    pipeline = ReportPipeline(
        RuntimeContext(config=_config(tmp_path)),
        repository=repository,
        collector_factory=lambda collector_id: FakeCollector(),
    )

    result = await pipeline.run_report("default")

    assert result.profile == "default"
    assert result.collected_count == 1
    assert result.inserted_count == 1
    assert len(repository.started) == 1
    assert len(repository.saved[0]) == 1
    assert repository.saved_provider_responses[0]["responses"][0].tool_name == "getExampleRows"
    assert repository.finished[0]["status"] == "succeeded"


async def test_pipeline_propagates_successful_collector_warnings(tmp_path) -> None:
    repository = FakeRepository()
    pipeline = ReportPipeline(
        RuntimeContext(config=_config(tmp_path)),
        repository=repository,
        collector_factory=lambda collector_id: WarningCollector(),
    )

    result = await pipeline.run_report("default")

    assert result.runs[0].warnings[0].message == "fetch cap may have hidden more posts"
    assert result.warnings[0].source_id == "api.test"


async def test_pipeline_profile_continues_after_one_collector_fails(tmp_path) -> None:
    repository = FakeRepository()
    pipeline = ReportPipeline(
        RuntimeContext(config=_multi_config(tmp_path)),
        repository=repository,
        collector_factory=lambda collector_id: FailingCollector() if collector_id == "api.bad" else FakeCollector(),
    )

    result = await pipeline.run_report("default")

    assert [run.status for run in result.runs] == ["failed", "succeeded"]
    assert result.collected_count == 1
    assert result.warnings[0].source_id == "api.bad"
    assert result.warnings[0].phase == "collecting"
    assert repository.saved_provider_responses[0]["responses"][0].tool_name == "getPartialRows"
    assert repository.finished[0]["status"] == "failed"
    assert repository.finished[1]["status"] == "succeeded"


async def test_pipeline_profile_records_all_failed_collectors(tmp_path) -> None:
    repository = FakeRepository()
    pipeline = ReportPipeline(
        RuntimeContext(config=_multi_config(tmp_path)),
        repository=repository,
        collector_factory=lambda collector_id: FailingCollector(),
    )

    result = await pipeline.run_report("default")

    assert [run.status for run in result.runs] == ["failed", "failed"]
    assert result.collected_count == 0
    assert [warning.source_id for warning in result.warnings] == ["api.bad", "api.good"]
    assert [item["status"] for item in repository.finished] == ["failed", "failed"]


async def test_pipeline_missing_profile_fails(tmp_path) -> None:
    pipeline = ReportPipeline(RuntimeContext(config=_config(tmp_path)), repository=FakeRepository())

    try:
        await pipeline.run_report("missing")
    except ConfigurationError as exc:
        assert "Unknown report profile" in str(exc)
    else:
        raise AssertionError("missing profile should fail")
