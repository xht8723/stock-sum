"""DeepSeek LLM provider client."""

from __future__ import annotations

from typing import Any
import json
import os

import httpx

from stock_sum.config.models import LLMConfig
from stock_sum.core.errors import ConfigurationError, StockSumError
from stock_sum.core.models import Summary
from stock_sum.llm.base import SummaryPayload
from stock_sum.llm.prompts import build_trading_summary_messages


class DeepSeekError(StockSumError):
    """Base error for DeepSeek failures."""


class DeepSeekAuthError(DeepSeekError):
    """Raised when DeepSeek credentials are missing or invalid."""


class DeepSeekCreditsError(DeepSeekError):
    """Raised when the DeepSeek account has insufficient balance."""


class DeepSeekValidationError(DeepSeekError):
    """Raised when DeepSeek rejects the request payload."""


class DeepSeekRateLimitError(DeepSeekError):
    """Raised when DeepSeek rate-limits the request."""


class DeepSeekRetryableError(DeepSeekError):
    """Raised when DeepSeek returns a retryable server failure."""


class DeepSeekClient:
    """Async client for DeepSeek's OpenAI-format chat completions API."""

    provider = "deepseek"

    def __init__(
        self,
        *,
        api_key_env: str,
        base_url: str,
        model: str,
        timeout_seconds: int,
        temperature: float,
        max_tokens: int,
        thinking_enabled: bool = True,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.api_key_env = api_key_env
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.timeout_seconds = timeout_seconds
        self.temperature = temperature
        self.max_tokens = max_tokens
        self.thinking_enabled = thinking_enabled
        self.transport = transport

    @classmethod
    def from_config(
        cls,
        config: LLMConfig,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> "DeepSeekClient":
        """Build a DeepSeek client from app config."""

        if config.provider != "deepseek":
            raise ConfigurationError(f"Unsupported DeepSeek provider config: {config.provider}")
        return cls(
            api_key_env=config.api_key_env,
            base_url=config.base_url,
            model=config.model,
            timeout_seconds=config.timeout_seconds,
            temperature=config.temperature,
            max_tokens=config.max_tokens,
            thinking_enabled=config.thinking_enabled,
            transport=transport,
        )

    async def summarize(self, payload: SummaryPayload, instructions: str | None = None) -> Summary:
        """Summarize a compact source payload."""

        return await self.complete_json(build_trading_summary_messages(payload, instructions=instructions))

    async def complete_json(self, messages: list[dict[str, str]]) -> Summary:
        """Run a structured JSON chat completion."""

        api_key = os.getenv(self.api_key_env)
        if not api_key:
            raise DeepSeekAuthError(f"Missing DeepSeek API key. Set environment variable {self.api_key_env}.")

        request_body: dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            "temperature": self.temperature,
            "max_tokens": self.max_tokens,
            "response_format": {"type": "json_object"},
            "thinking": {"type": "enabled" if self.thinking_enabled else "disabled"},
        }

        try:
            async with httpx.AsyncClient(
                base_url=self.base_url,
                timeout=self.timeout_seconds,
                transport=self.transport,
            ) as client:
                response = await client.post(
                    "/chat/completions",
                    json=request_body,
                    headers={"Authorization": f"Bearer {api_key}"},
                )
        except httpx.TimeoutException as exc:
            raise DeepSeekRetryableError("DeepSeek request timed out.") from exc
        except httpx.RequestError as exc:
            raise DeepSeekRetryableError(f"DeepSeek request failed before receiving a response: {exc}") from exc

        _raise_for_deepseek_status(response)
        raw_response = _json_response(response)
        content = _first_message_content(raw_response)
        parsed_content = _parse_json_content(content)
        return Summary(
            text=content,
            model=self.model,
            metadata={
                "provider": self.provider,
                "response_id": raw_response.get("id"),
                "created": raw_response.get("created"),
                "usage": raw_response.get("usage"),
                "finish_reason": _first_finish_reason(raw_response),
                "parsed": parsed_content,
                "raw_response": raw_response,
            },
        )


def _raise_for_deepseek_status(response: httpx.Response) -> None:
    if response.status_code == 401:
        raise DeepSeekAuthError("DeepSeek rejected the configured API key.")
    if response.status_code == 402:
        raise DeepSeekCreditsError("DeepSeek account has insufficient balance.")
    if response.status_code == 422:
        raise DeepSeekValidationError(f"DeepSeek rejected the request: {_error_detail(response)}")
    if response.status_code == 429:
        raise DeepSeekRateLimitError("DeepSeek rate-limited the request.")
    if response.status_code in {500, 503} or response.status_code >= 500:
        raise DeepSeekRetryableError(f"DeepSeek returned retryable HTTP {response.status_code}.")
    try:
        response.raise_for_status()
    except httpx.HTTPStatusError as exc:
        raise DeepSeekError(f"DeepSeek request failed: HTTP {response.status_code}") from exc


def _json_response(response: httpx.Response) -> dict[str, Any]:
    try:
        payload = response.json()
    except ValueError as exc:
        raise DeepSeekError("DeepSeek returned invalid JSON.") from exc
    if not isinstance(payload, dict):
        raise DeepSeekError("DeepSeek returned a non-object JSON response.")
    return payload


def _first_message_content(response: dict[str, Any]) -> str:
    choices = response.get("choices")
    if not isinstance(choices, list) or not choices:
        raise DeepSeekError("DeepSeek response did not include choices.")
    first = choices[0]
    if not isinstance(first, dict):
        raise DeepSeekError("DeepSeek response choice was malformed.")
    message = first.get("message")
    if not isinstance(message, dict):
        raise DeepSeekError("DeepSeek response choice did not include a message.")
    content = message.get("content")
    if not isinstance(content, str) or not content.strip():
        raise DeepSeekError("DeepSeek response message was empty.")
    return content


def _parse_json_content(content: str) -> dict[str, Any] | list[Any] | None:
    content = _strip_json_fence(content.strip())
    try:
        parsed = json.loads(content)
    except ValueError:
        return None
    if isinstance(parsed, (dict, list)):
        return parsed
    return None


def _strip_json_fence(content: str) -> str:
    if not content.startswith("```"):
        return content
    lines = content.splitlines()
    if lines and lines[0].startswith("```"):
        lines = lines[1:]
    if lines and lines[-1].strip() == "```":
        lines = lines[:-1]
    return "\n".join(lines).strip()


def _first_finish_reason(response: dict[str, Any]) -> str | None:
    choices = response.get("choices")
    if isinstance(choices, list) and choices and isinstance(choices[0], dict):
        value = choices[0].get("finish_reason")
        return value if isinstance(value, str) else None
    return None


def _error_detail(response: httpx.Response) -> str:
    try:
        payload = response.json()
    except ValueError:
        return response.text
    return str(payload.get("error") or payload)
