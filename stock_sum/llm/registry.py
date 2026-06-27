"""LLM provider registry skeleton."""

from __future__ import annotations

from stock_sum.llm.base import LLMClient


class LLMRegistry:
    """Stores LLM clients by provider id."""

    def __init__(self) -> None:
        self._clients: dict[str, LLMClient] = {}

    def register(self, client: LLMClient) -> None:
        """Register a provider client."""

        self._clients[client.provider] = client

    def get(self, provider: str) -> LLMClient:
        """Return a provider client."""

        return self._clients[provider]
