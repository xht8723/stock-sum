"""Xpoz MCP-over-HTTP client tests."""

from __future__ import annotations

import json

import httpx
import pytest

from stock_sum.collectors.api.xpoz import (
    XpozAuthError,
    XpozClient,
    XpozCreditsError,
    XpozRetryableError,
    parse_mcp_response_text,
    parse_xpoz_rows,
)


async def test_client_sends_bearer_auth_and_initializes(monkeypatch) -> None:
    seen_headers = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen_headers.append(request.headers.get("authorization"))
        payload = json.loads(request.content)
        if payload["method"] == "initialize":
            return httpx.Response(200, json={"jsonrpc": "2.0", "id": 1, "result": {}})
        if payload["method"] == "notifications/initialized":
            return httpx.Response(202)
        return httpx.Response(
            200,
            json={
                "jsonrpc": "2.0",
                "id": 3,
                "result": {"content": [{"type": "text", "text": "status: success\ndata:\n  results[0]{id}:"}]},
            },
        )

    monkeypatch.setenv("XPOZ_API_KEY", "secret")
    client = XpozClient(
        api_key_env="XPOZ_API_KEY",
        server_url="https://mcp.xpoz.ai/mcp",
        timeout_seconds=60,
        transport=httpx.MockTransport(handler),
    )

    await client.call_tool_rows("getTwitterPostsByAuthor", {"username": "aleabitoreddit"})

    assert seen_headers == ["Bearer secret", "Bearer secret", "Bearer secret"]


async def test_client_archives_tool_response_without_auth_secret(monkeypatch) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        if payload["method"] == "initialize":
            return httpx.Response(200, json={"jsonrpc": "2.0", "id": 1, "result": {}})
        if payload["method"] == "notifications/initialized":
            return httpx.Response(202)
        return httpx.Response(
            200,
            json={
                "jsonrpc": "2.0",
                "id": 3,
                "result": {
                    "content": [
                        {
                            "type": "text",
                            "text": "status: success\ndata:\n  results[1]:\n    - id: \"123\"\n      text: \"hello\"",
                        }
                    ]
                },
            },
        )

    monkeypatch.setenv("XPOZ_API_KEY", "secret")
    client = XpozClient(
        api_key_env="XPOZ_API_KEY",
        server_url="https://mcp.xpoz.ai/mcp",
        timeout_seconds=60,
        transport=httpx.MockTransport(handler),
    )

    await client.call_tool_rows("getTwitterPostsByAuthor", {"username": "aleabitoreddit", "limit": 100})
    responses = client.take_provider_responses()

    assert len(responses) == 1
    response = responses[0]
    assert response.provider == "xpoz"
    assert response.tool_name == "getTwitterPostsByAuthor"
    assert response.request_arguments == {"username": "aleabitoreddit", "limit": 100}
    assert response.parsed_rows == [{"id": "123", "text": "hello"}]
    assert response.row_count == 1
    assert "hello" in response.raw_response_text
    assert "secret" not in json.dumps(response.request_arguments)
    assert client.take_provider_responses() == []


async def test_client_missing_api_key_fails(monkeypatch) -> None:
    monkeypatch.delenv("XPOZ_API_KEY", raising=False)
    client = XpozClient(api_key_env="XPOZ_API_KEY", server_url="https://mcp.xpoz.ai/mcp", timeout_seconds=60)

    with pytest.raises(XpozAuthError):
        await client.call_tool_rows("getTwitterPostsByAuthor", {"username": "aleabitoreddit"})


@pytest.mark.parametrize(
    ("status_code", "expected_error"),
    [(401, XpozAuthError), (402, XpozCreditsError), (429, XpozCreditsError), (500, XpozRetryableError)],
)
async def test_client_maps_http_errors(monkeypatch, status_code, expected_error) -> None:
    monkeypatch.setenv("XPOZ_API_KEY", "secret")
    client = XpozClient(
        api_key_env="XPOZ_API_KEY",
        server_url="https://mcp.xpoz.ai/mcp",
        timeout_seconds=60,
        transport=httpx.MockTransport(lambda request: httpx.Response(status_code, json={"error": "failed"})),
    )

    with pytest.raises(expected_error):
        await client.call_tool_rows("getTwitterPostsByAuthor", {"username": "aleabitoreddit"})


def test_parse_mcp_sse_json() -> None:
    body = 'event: message\ndata: {"jsonrpc":"2.0","result":{"content":[]}}\n\n'

    assert parse_mcp_response_text(body)["result"]["content"] == []


def test_parse_xpoz_table_rows() -> None:
    rows = parse_xpoz_rows(
        'status: success\ndata:\n  posts[2]{id,title,createdAt}:\n    abc,"hello, world",1782611111\n    def,second,1782611112'
    )

    assert rows == [
        {"id": "abc", "title": "hello, world", "createdAt": "1782611111"},
        {"id": "def", "title": "second", "createdAt": "1782611112"},
    ]


def test_parse_xpoz_list_rows_with_indexed_media() -> None:
    rows = parse_xpoz_rows(
        """
status: success
data:
  results[1]:
    - id: "123"
      text: "body"
      mediaUrls[1]: "https://pbs.twimg.com/media/a.jpg"
      mediaUrls[2]: "https://pbs.twimg.com/media/b.jpg"
"""
    )

    assert rows[0]["id"] == "123"
    assert rows[0]["mediaUrls"] == ["https://pbs.twimg.com/media/a.jpg", "https://pbs.twimg.com/media/b.jpg"]
