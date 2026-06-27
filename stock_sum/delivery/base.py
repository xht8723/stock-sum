"""Delivery provider protocol."""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from stock_sum.core.models import Report


@runtime_checkable
class DeliveryProvider(Protocol):
    """Common interface for report delivery providers."""

    delivery_id: str

    async def send(self, report: Report) -> None:
        """Send a rendered report."""
