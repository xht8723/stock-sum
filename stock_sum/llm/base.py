"""Provider-neutral LLM client protocol."""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from stock_sum.core.models import RawItem, Summary


@runtime_checkable
class LLMClient(Protocol):
    """Common interface for LLM provider adapters."""

    provider: str
    model: str

    async def summarize(self, items: list[RawItem], instructions: str) -> Summary:
        """Summarize collected items."""
