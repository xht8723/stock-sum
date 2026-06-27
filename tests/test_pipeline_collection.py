"""Collection pipeline persistence tests."""

from stock_sum.config.models import AppConfig, CollectorConfig, LLMConfig, ReportProfileConfig, StorageConfig
from stock_sum.core.context import RuntimeContext
from stock_sum.core.errors import ConfigurationError
from stock_sum.core.models import RawItem, RawItemSaveResult
from stock_sum.core.pipeline import ReportPipeline


class FakeCollector:
    collector_id = "x.test"

    async def collect(self, context):
        return [
            RawItem(
                source_id="123",
                source_type="x_user_timeline",
                url="https://x.com/user/status/123",
                text="hello",
                metadata={"handle": "user"},
            )
        ]


class FakeRepository:
    def __init__(self):
        self.started = []
        self.finished = []
        self.saved = []

    async def initialize(self):
        return None

    async def start_collection_run(self, **kwargs):
        self.started.append(kwargs)

    async def finish_collection_run(self, **kwargs):
        self.finished.append(kwargs)

    async def save_raw_items(self, items):
        self.saved.append(items)
        return RawItemSaveResult(
            source_type="x_user_timeline",
            collected_count=len(items),
            inserted_count=len(items),
            updated_count=0,
        )

    async def save_summaries(self, summaries):
        raise NotImplementedError

    async def save_report(self, report):
        raise NotImplementedError


def _config(tmp_path) -> AppConfig:
    return AppConfig(
        storage=StorageConfig(sqlite_path=str(tmp_path / "test.sqlite3")),
        llm=LLMConfig(provider="openai", model="test", api_key_env="OPENAI_API_KEY"),
        collectors={"x": {"test": CollectorConfig(kind="x_user_timeline", handles=["user"])}},
        reports={"morning": ReportProfileConfig(schedule="0 8 * * *", collector_ids=["x.test"])},
    )


async def test_pipeline_collects_and_persists_with_fake_collector(tmp_path) -> None:
    repository = FakeRepository()
    pipeline = ReportPipeline(
        RuntimeContext(config=_config(tmp_path)),
        repository=repository,
        collector_factory=lambda collector_id: FakeCollector(),
    )

    result = await pipeline.run_report("morning")

    assert result.profile == "morning"
    assert result.collected_count == 1
    assert result.inserted_count == 1
    assert len(repository.started) == 1
    assert len(repository.saved[0]) == 1
    assert repository.finished[0]["status"] == "succeeded"


async def test_pipeline_missing_profile_fails(tmp_path) -> None:
    pipeline = ReportPipeline(RuntimeContext(config=_config(tmp_path)), repository=FakeRepository())

    try:
        await pipeline.run_report("missing")
    except ConfigurationError as exc:
        assert "Unknown report profile" in str(exc)
    else:
        raise AssertionError("missing profile should fail")
