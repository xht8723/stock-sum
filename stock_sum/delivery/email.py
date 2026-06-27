"""Email delivery provider scaffold."""

from __future__ import annotations

from stock_sum.core.models import Report
from stock_sum.delivery.base import DeliveryProvider


class EmailDeliveryProvider(DeliveryProvider):
    """Sends reports through SMTP."""

    def __init__(self, delivery_id: str) -> None:
        self.delivery_id = delivery_id

    async def send(self, report: Report) -> None:
        """Send a report by email."""

        raise NotImplementedError("Email delivery is scaffolded only.")
