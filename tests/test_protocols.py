"""Protocol conformance tests."""

from stock_sum.collectors.base import Collector
from stock_sum.llm.base import LLMClient


class FakeCollector:
    collector_id = "fake.test"

    async def collect(self, context):
        return []


def test_collector_stubs_match_protocol() -> None:
    assert isinstance(FakeCollector(), Collector)


def test_protocols_are_runtime_checkable() -> None:
    assert hasattr(LLMClient, "_is_runtime_protocol")
