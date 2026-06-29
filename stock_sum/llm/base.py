"""Provider-neutral LLM client protocol."""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable

from stock_sum.core.models import Summary
from stock_sum.core.summary_input import SummaryInput

SummaryPayload = SummaryInput | dict[str, Any]


@runtime_checkable
class LLMClient(Protocol):
    """Common interface for LLM provider adapters."""

    provider: str
    model: str

    async def summarize(self, payload: SummaryPayload, instructions: str | None = None) -> Summary:
        """Summarize an LLM-ready payload."""
