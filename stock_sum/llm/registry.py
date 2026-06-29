"""LLM provider registry skeleton."""

from __future__ import annotations

import httpx

from stock_sum.config.models import LLMConfig
from stock_sum.core.errors import ConfigurationError
from stock_sum.llm.base import LLMClient
from stock_sum.llm.providers.deepseek import DeepSeekClient


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


def build_llm_client(
    config: LLMConfig,
    *,
    transport: httpx.AsyncBaseTransport | None = None,
) -> LLMClient:
    """Build the configured LLM client."""

    if config.provider == "deepseek":
        return DeepSeekClient.from_config(config, transport=transport)
    raise ConfigurationError(f"Unsupported LLM provider: {config.provider}")
