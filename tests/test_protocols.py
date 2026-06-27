"""Protocol conformance tests."""

from stock_sum.collectors.base import Collector
from stock_sum.delivery.base import DeliveryProvider
from stock_sum.delivery.email import EmailDeliveryProvider
from stock_sum.delivery.whatsapp import WhatsAppDeliveryProvider
from stock_sum.llm.base import LLMClient
from stock_sum.reports.base import ReportRenderer


class FakeCollector:
    collector_id = "fake.test"

    async def collect(self, context):
        return []


def test_collector_stubs_match_protocol() -> None:
    assert isinstance(FakeCollector(), Collector)


def test_delivery_stubs_match_protocol() -> None:
    assert isinstance(EmailDeliveryProvider("email.test"), DeliveryProvider)
    assert isinstance(WhatsAppDeliveryProvider("whatsapp.test"), DeliveryProvider)


def test_protocols_are_runtime_checkable() -> None:
    assert hasattr(LLMClient, "_is_runtime_protocol")
    assert hasattr(ReportRenderer, "_is_runtime_protocol")
