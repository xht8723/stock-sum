"""Collection pipeline persistence tests."""

import asyncio

from stock_sum.config.models import AppConfig, CollectorConfig, LLMConfig, ServiceConfig, StorageConfig
from stock_sum.core.context import RuntimeContext
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


class SlowCollector:
    active = 0
    max_active = 0

    def __init__(self, collector_id: str) -> None:
        self.collector_id = collector_id

    async def collect(self, context):
        type(self).active += 1
        type(self).max_active = max(type(self).max_active, type(self).active)
        try:
            await asyncio.sleep(0.01)
            return [
                RawItem(
                    source_id=self.collector_id,
                    source_type="test_source",
                    url=f"https://example.com/items/{self.collector_id}",
                    text="hello",
                    metadata={"source": "test"},
                )
            ]
        finally:
            type(self).active -= 1


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

def _config(tmp_path) -> AppConfig:
    return AppConfig(
        storage=StorageConfig(sqlite_path=str(tmp_path / "test.sqlite3")),
        llm=LLMConfig(provider="deepseek", model="deepseek-v4-flash", api_key_env="DEEPSEEK_API_KEY"),
        collectors={"api": {"test": CollectorConfig(kind="test_source")}},
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
    )


def _slow_config(tmp_path) -> AppConfig:
    return AppConfig(
        service=ServiceConfig(collector_concurrency=2),
        storage=StorageConfig(sqlite_path=str(tmp_path / "test.sqlite3")),
        llm=LLMConfig(provider="deepseek", model="deepseek-v4-flash", api_key_env="DEEPSEEK_API_KEY"),
        collectors={
            "api": {
                "one": CollectorConfig(kind="test_source"),
                "two": CollectorConfig(kind="test_source"),
                "three": CollectorConfig(kind="test_source"),
            }
        },
    )


async def test_pipeline_collects_and_persists_with_fake_collector(tmp_path) -> None:
    repository = FakeRepository()
    pipeline = ReportPipeline(
        RuntimeContext(config=_config(tmp_path)),
        repository=repository,
        collector_factory=lambda collector_id: FakeCollector(),
    )

    result = await pipeline.collect_sources(collector_ids=["api.test"])

    assert result.scope == "social"
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

    result = await pipeline.collect_sources(collector_ids=["api.test"])

    assert result.runs[0].warnings[0].message == "fetch cap may have hidden more posts"
    assert result.warnings[0].source_id == "api.test"


async def test_pipeline_continues_after_one_collector_fails(tmp_path) -> None:
    repository = FakeRepository()
    pipeline = ReportPipeline(
        RuntimeContext(config=_multi_config(tmp_path)),
        repository=repository,
        collector_factory=lambda collector_id: FailingCollector() if collector_id == "api.bad" else FakeCollector(),
    )

    result = await pipeline.collect_sources(collector_ids=["api.bad", "api.good"])

    assert [run.status for run in result.runs] == ["failed", "succeeded"]
    assert result.collected_count == 1
    assert result.warnings[0].source_id == "api.bad"
    assert result.warnings[0].phase == "collecting"
    assert repository.saved_provider_responses[0]["responses"][0].tool_name == "getPartialRows"
    assert repository.finished[0]["status"] == "failed"
    assert repository.finished[1]["status"] == "succeeded"


async def test_pipeline_runs_collectors_with_bounded_concurrency(tmp_path) -> None:
    repository = FakeRepository()
    SlowCollector.active = 0
    SlowCollector.max_active = 0
    pipeline = ReportPipeline(
        RuntimeContext(config=_slow_config(tmp_path)),
        repository=repository,
        collector_factory=lambda collector_id: SlowCollector(collector_id),
    )

    result = await pipeline.collect_sources(collector_ids=["api.one", "api.two", "api.three"])

    assert [run.collector_id for run in result.runs] == ["api.one", "api.two", "api.three"]
    assert result.collected_count == 3
    assert SlowCollector.max_active == 2


async def test_pipeline_records_all_failed_collectors(tmp_path) -> None:
    repository = FakeRepository()
    pipeline = ReportPipeline(
        RuntimeContext(config=_multi_config(tmp_path)),
        repository=repository,
        collector_factory=lambda collector_id: FailingCollector(),
    )

    result = await pipeline.collect_sources(collector_ids=["api.bad", "api.good"])

    assert [run.status for run in result.runs] == ["failed", "failed"]
    assert result.collected_count == 0
    assert [warning.source_id for warning in result.warnings] == ["api.bad", "api.good"]
    assert [item["status"] for item in repository.finished] == ["failed", "failed"]
