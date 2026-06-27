"""WhatsApp delivery provider scaffold."""

from __future__ import annotations

from stock_sum.core.models import Report
from stock_sum.delivery.base import DeliveryProvider


class WhatsAppDeliveryProvider(DeliveryProvider):
    """Sends reports through a WhatsApp provider."""

    def __init__(self, delivery_id: str) -> None:
        self.delivery_id = delivery_id

    async def send(self, report: Report) -> None:
        """Send a report by WhatsApp."""

        raise NotImplementedError("WhatsApp delivery is scaffolded only.")
