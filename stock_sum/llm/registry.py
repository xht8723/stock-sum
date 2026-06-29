"""LLM provider registry skeleton."""

from __future__ import annotations

from dataclasses import dataclass

import httpx

from stock_sum.config.models import LLMConfig
from stock_sum.core.errors import ConfigurationError
from stock_sum.llm.base import LLMClient
from stock_sum.llm.providers.deepseek import DeepSeekClient


@dataclass(frozen=True)
class LLMProviderDescriptor:
    """Metadata shown during setup and provider listing."""

    provider_id: str
    display_name: str
    default_model: str
    api_key_env: str
    implemented: bool
    setup_notes: str
    base_url: str


LLM_PROVIDER_DESCRIPTORS = [
    LLMProviderDescriptor(
        provider_id="deepseek",
        display_name="DeepSeek",
        default_model="deepseek-v4-flash",
        api_key_env="DEEPSEEK_API_KEY",
        implemented=True,
        setup_notes="Default fast text summarization provider.",
        base_url="https://api.deepseek.com",
    )
]


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


def list_llm_providers() -> list[LLMProviderDescriptor]:
    """Return provider metadata for setup and display."""

    return list(LLM_PROVIDER_DESCRIPTORS)


def get_llm_provider(provider_id: str) -> LLMProviderDescriptor:
    """Return one provider descriptor."""

    for descriptor in LLM_PROVIDER_DESCRIPTORS:
        if descriptor.provider_id == provider_id:
            return descriptor
    raise ConfigurationError(f"Unsupported LLM provider: {provider_id}")
