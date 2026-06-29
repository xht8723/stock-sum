"""DeepSeek LLM client tests."""

from __future__ import annotations

import json

import httpx
import pytest

from stock_sum.llm.providers.deepseek import (
    DeepSeekAuthError,
    DeepSeekClient,
    DeepSeekCreditsError,
    DeepSeekRateLimitError,
    DeepSeekRetryableError,
    DeepSeekValidationError,
)


def _client(transport: httpx.AsyncBaseTransport | None = None) -> DeepSeekClient:
    return DeepSeekClient(
        api_key_env="DEEPSEEK_API_KEY",
        base_url="https://api.deepseek.com",
        model="deepseek-v4-flash",
        timeout_seconds=60,
        temperature=0.2,
        max_tokens=3200,
        transport=transport,
    )


async def test_deepseek_client_sends_openai_format_request(monkeypatch) -> None:
    seen: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["authorization"] = request.headers["authorization"]
        seen["path"] = request.url.path
        seen["body"] = json.loads(request.content.decode("utf-8"))
        return httpx.Response(
            200,
            json={
                "id": "chatcmpl-test",
                "created": 123,
                "choices": [
                    {
                        "message": {"content": '{"executive_summary":["ok"],"metadata":{"not_financial_advice":true}}'},
                        "finish_reason": "stop",
                    }
                ],
                "usage": {"prompt_tokens": 10, "completion_tokens": 5},
            },
        )

    monkeypatch.setenv("DEEPSEEK_API_KEY", "secret")
    summary = await _client(httpx.MockTransport(handler)).summarize({"sources": {"x": [], "reddit": []}})

    body = seen["body"]
    assert seen["authorization"] == "Bearer secret"
    assert seen["path"] == "/chat/completions"
    assert isinstance(body, dict)
    assert body["model"] == "deepseek-v4-flash"
    assert body["response_format"] == {"type": "json_object"}
    assert summary.model == "deepseek-v4-flash"
    assert summary.metadata["parsed"]["executive_summary"] == ["ok"]
    assert summary.metadata["usage"]["prompt_tokens"] == 10


async def test_deepseek_client_missing_api_key_fails(monkeypatch) -> None:
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)

    with pytest.raises(DeepSeekAuthError):
        await _client().summarize({"sources": {}})


async def test_deepseek_client_maps_request_errors(monkeypatch) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("blocked", request=request)

    monkeypatch.setenv("DEEPSEEK_API_KEY", "secret")

    with pytest.raises(DeepSeekRetryableError, match="before receiving a response"):
        await _client(httpx.MockTransport(handler)).summarize({"sources": {}})


async def test_deepseek_client_parses_fenced_json(monkeypatch) -> None:
    monkeypatch.setenv("DEEPSEEK_API_KEY", "secret")
    client = _client(
        httpx.MockTransport(
            lambda request: httpx.Response(
                200,
                json={
                    "choices": [
                        {
                            "message": {"content": '```json\n{"executive_summary":["ok"]}\n```'},
                            "finish_reason": "stop",
                        }
                    ]
                },
            )
        )
    )

    summary = await client.summarize({"sources": {}})

    assert summary.metadata["parsed"]["executive_summary"] == ["ok"]


@pytest.mark.parametrize(
    ("status_code", "expected_error"),
    [
        (401, DeepSeekAuthError),
        (402, DeepSeekCreditsError),
        (422, DeepSeekValidationError),
        (429, DeepSeekRateLimitError),
        (500, DeepSeekRetryableError),
        (503, DeepSeekRetryableError),
    ],
)
async def test_deepseek_client_maps_http_errors(monkeypatch, status_code, expected_error) -> None:
    monkeypatch.setenv("DEEPSEEK_API_KEY", "secret")
    client = _client(httpx.MockTransport(lambda request: httpx.Response(status_code, json={"error": "failed"})))

    with pytest.raises(expected_error):
        await client.summarize({"sources": {}})
